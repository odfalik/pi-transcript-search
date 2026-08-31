"""Disposable incremental SQLite index over Pi's native JSONL sessions."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from .dates import iso_from_millis, parse_timestamp
from .parser import ParsedLine, SessionHeader, iter_complete_lines

SCHEMA_VERSION = 1
FINGERPRINT_BYTES = 1024 * 1024
MAX_CONTEXT = 50
MAX_LIMIT = 100
MAX_SHOW_CHARS = 50_000
MAX_SNIPPETS = 5
MAX_SNIPPET_CHARS = 400
MAX_NAME_CHARS = 200

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    cwd TEXT NOT NULL,
    name TEXT,
    started_at_ms INTEGER,
    updated_at_ms INTEGER,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(session_id) ON DELETE CASCADE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    next_ordinal INTEGER NOT NULL,
    prefix_fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    entry_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    timestamp_ms INTEGER,
    text TEXT NOT NULL,
    UNIQUE(session_id, entry_id)
);
CREATE INDEX IF NOT EXISTS messages_session_ordinal ON messages(session_id, ordinal);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at_ms DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


@dataclass(frozen=True)
class RefreshStats:
    scanned_files: int
    changed_files: int
    removed_files: int
    indexed_messages: int
    elapsed_ms: int

    def record(self) -> dict:
        return {
            "scanned_files": self.scanned_files,
            "changed_files": self.changed_files,
            "removed_files": self.removed_files,
            "indexed_messages": self.indexed_messages,
            "elapsed_ms": self.elapsed_ms,
        }


class ConversationIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(_SCHEMA)
        self.db.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def refresh(self, sessions_dir: Path, rebuild: bool = False) -> RefreshStats:
        started = perf_counter()
        sessions_dir = sessions_dir.expanduser().resolve()
        paths = (
            sorted(p.resolve() for p in sessions_dir.rglob("*.jsonl"))
            if sessions_dir.exists()
            else []
        )
        current = {str(path) for path in paths}
        changed = 0
        indexed_messages = 0

        with self.db:
            if rebuild:
                self._clear()
            known = {
                row["path"]: row
                for row in self.db.execute(
                    """SELECT path, session_id, size, mtime_ns, offset, next_ordinal,
                              prefix_fingerprint FROM files"""
                )
            }
            if rebuild:
                known = {}
            removed = sorted(set(known) - current)
            for path in removed:
                self._remove_path(path)

            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                previous = known.get(str(path))
                if previous is None:
                    count = self._replace_file(path, stat.st_size, stat.st_mtime_ns)
                elif (
                    stat.st_size == previous["size"]
                    and stat.st_mtime_ns == previous["mtime_ns"]
                ):
                    continue
                elif (
                    stat.st_size > previous["size"]
                    and previous["session_id"] is not None
                    and previous["prefix_fingerprint"]
                    == prefix_fingerprint(path, previous["offset"])
                ):
                    count = self._append_file(
                        path, previous, stat.st_size, stat.st_mtime_ns
                    )
                else:
                    count = self._replace_file(path, stat.st_size, stat.st_mtime_ns)
                changed += 1
                indexed_messages += count

        return RefreshStats(
            scanned_files=len(paths),
            changed_files=changed,
            removed_files=len(set(known) - current),
            indexed_messages=indexed_messages,
            elapsed_ms=round((perf_counter() - started) * 1000),
        )

    def _clear(self) -> None:
        self.db.execute("DELETE FROM messages")
        self.db.execute("DELETE FROM files")
        self.db.execute("DELETE FROM sessions")

    def _remove_path(self, path: str) -> None:
        row = self.db.execute(
            "SELECT session_id FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row and row["session_id"] is not None:
            self.db.execute(
                "DELETE FROM sessions WHERE session_id = ?", (row["session_id"],)
            )
        self.db.execute("DELETE FROM files WHERE path = ?", (path,))

    def _replace_file(self, path: Path, size: int, mtime_ns: int) -> int:
        self._remove_path(str(path))
        header: SessionHeader | None = None
        name: str | None = None
        name_seen = False
        messages: list[ParsedLine] = []
        offset = 0
        try:
            with path.open("rb") as handle:
                for offset, parsed in iter_complete_lines(handle):
                    if parsed is None:
                        continue
                    if parsed.header and header is None:
                        header = parsed.header
                    elif parsed.name_seen:
                        name_seen = True
                        name = parsed.name
                    elif parsed.message:
                        messages.append(parsed)
        except OSError:
            return 0
        if header is None:
            self.db.execute(
                """INSERT OR REPLACE INTO files
                   (path, session_id, size, mtime_ns, offset, next_ordinal, prefix_fingerprint)
                   VALUES (?, NULL, ?, ?, ?, 0, ?)""",
                (str(path), size, mtime_ns, offset, prefix_fingerprint(path, offset)),
            )
            return 0

        started_at = parse_timestamp(header.timestamp)
        updated_at = started_at
        self.db.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, path, cwd, name, started_at_ms, updated_at_ms, message_count)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (
                header.session_id,
                str(path),
                header.cwd,
                name if name_seen else None,
                started_at,
                updated_at,
            ),
        )
        next_ordinal = 0
        inserted = 0
        for parsed in messages:
            inserted += self._insert_message(header.session_id, next_ordinal, parsed)
            next_ordinal += 1
        self._finish_session(header.session_id)
        self.db.execute(
            """INSERT INTO files
               (path, session_id, size, mtime_ns, offset, next_ordinal, prefix_fingerprint)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(path),
                header.session_id,
                size,
                mtime_ns,
                offset,
                next_ordinal,
                prefix_fingerprint(path, offset),
            ),
        )
        return inserted

    def _append_file(
        self, path: Path, previous: sqlite3.Row, size: int, mtime_ns: int
    ) -> int:
        session_id = previous["session_id"]
        ordinal = previous["next_ordinal"]
        start_offset = previous["offset"]
        offset = start_offset
        inserted = 0
        name_seen = False
        name: str | None = None
        try:
            with path.open("rb") as handle:
                for offset, parsed in iter_complete_lines(handle, start_offset):
                    if parsed is None:
                        continue
                    if parsed.header:
                        return self._replace_file(path, size, mtime_ns)
                    if parsed.name_seen:
                        name_seen = True
                        name = parsed.name
                    elif parsed.message:
                        inserted += self._insert_message(session_id, ordinal, parsed)
                        ordinal += 1
        except OSError:
            return 0
        if name_seen:
            self.db.execute(
                "UPDATE sessions SET name = ? WHERE session_id = ?", (name, session_id)
            )
        self._finish_session(session_id)
        self.db.execute(
            """UPDATE files SET size = ?, mtime_ns = ?, offset = ?, next_ordinal = ?,
                                prefix_fingerprint = ?
               WHERE path = ?""",
            (
                size,
                mtime_ns,
                offset,
                ordinal,
                prefix_fingerprint(path, offset),
                str(path),
            ),
        )
        return inserted

    def _insert_message(self, session_id: str, ordinal: int, parsed: ParsedLine) -> int:
        message = parsed.message
        if message is None:
            return 0
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO messages
               (session_id, entry_id, ordinal, role, timestamp_ms, text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                message.entry_id,
                ordinal,
                message.role,
                parse_timestamp(message.timestamp),
                message.text,
            ),
        )
        return 1 if cursor.rowcount else 0

    def _finish_session(self, session_id: str) -> None:
        self.db.execute(
            """UPDATE sessions SET
                 message_count = (SELECT COUNT(*) FROM messages WHERE session_id = ?),
                 updated_at_ms = COALESCE(
                   (SELECT MAX(timestamp_ms) FROM messages WHERE session_id = ?),
                   started_at_ms
                 )
               WHERE session_id = ?""",
            (session_id, session_id, session_id),
        )

    def search(
        self,
        query: str,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        cwd: str | None = None,
        limit: int = 20,
        max_snippets: int = 3,
        exact: bool = False,
    ) -> list[dict]:
        validate_range("limit", limit, 1, MAX_LIMIT)
        validate_range("max_snippets", max_snippets, 1, MAX_SNIPPETS)
        match = build_match_query(query, exact=exact)
        clauses = ["messages_fts MATCH ?"]
        args: list[object] = [match]
        if since_ms is not None:
            clauses.append("s.started_at_ms >= ?")
            args.append(since_ms)
        if until_ms is not None:
            clauses.append("s.started_at_ms < ?")
            args.append(until_ms)
        if cwd:
            clauses.append("LOWER(s.cwd) LIKE ? ESCAPE '\\'")
            args.append(f"%{escape_like(cwd.lower())}%")
        sql = f"""
            SELECT s.session_id, s.cwd, s.name, s.started_at_ms, s.updated_at_ms,
                   s.message_count, m.entry_id, m.ordinal, m.role, m.timestamp_ms,
                   snippet(messages_fts, 0, '«', '»', '…', 24) AS snippet,
                   bm25(messages_fts) AS rank
              FROM messages_fts
              JOIN messages m ON m.id = messages_fts.rowid
              JOIN sessions s ON s.session_id = m.session_id
             WHERE {" AND ".join(clauses)}
             ORDER BY rank ASC, s.updated_at_ms DESC, m.ordinal ASC
        """
        grouped: dict[str, dict] = {}
        for row in self.db.execute(sql, args):
            result = grouped.get(row["session_id"])
            if result is None:
                result = self._session_record(row)
                result.update({"match_count": 0, "matches": [], "_rank": row["rank"]})
                grouped[row["session_id"]] = result
            result["match_count"] += 1
            if len(result["matches"]) < max_snippets:
                result["matches"].append(
                    {
                        "entry_id": row["entry_id"],
                        "ordinal": row["ordinal"],
                        "role": row["role"],
                        "timestamp": iso_from_millis(row["timestamp_ms"]),
                        "snippet": clip_text(row["snippet"], MAX_SNIPPET_CHARS),
                    }
                )
        results = sorted(
            grouped.values(),
            key=lambda item: (item["_rank"], -(item["updated_at_ms"] or 0)),
        )[:limit]
        for result in results:
            result.pop("_rank", None)
            result.pop("updated_at_ms", None)
        return results

    def list_sessions(
        self,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        cwd: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        validate_range("limit", limit, 1, MAX_LIMIT)
        clauses = ["1 = 1"]
        args: list[object] = []
        if since_ms is not None:
            clauses.append("started_at_ms >= ?")
            args.append(since_ms)
        if until_ms is not None:
            clauses.append("started_at_ms < ?")
            args.append(until_ms)
        if cwd:
            clauses.append("LOWER(cwd) LIKE ? ESCAPE '\\'")
            args.append(f"%{escape_like(cwd.lower())}%")
        args.append(limit)
        rows = self.db.execute(
            f"""SELECT session_id, cwd, name, started_at_ms, updated_at_ms, message_count
                  FROM sessions WHERE {" AND ".join(clauses)}
                 ORDER BY COALESCE(updated_at_ms, started_at_ms) DESC LIMIT ?""",
            args,
        )
        results = []
        for row in rows:
            result = self._session_record(row)
            result.pop("updated_at_ms", None)
            results.append(result)
        return results

    def show(
        self,
        session_id: str,
        *,
        around: int | None = None,
        context: int = 3,
        role: str | None = None,
        max_chars: int = 20_000,
    ) -> dict | None:
        validate_range("context", context, 0, MAX_CONTEXT)
        validate_range("max_chars", max_chars, 1, MAX_SHOW_CHARS)
        session = self.db.execute(
            """SELECT session_id, cwd, name, started_at_ms, updated_at_ms, message_count
                 FROM sessions WHERE session_id = ?""",
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        clauses = ["session_id = ?"]
        args: list[object] = [session_id]
        if around is not None:
            clauses.append("ordinal BETWEEN ? AND ?")
            args.extend([max(0, around - context), around + context])
        if role:
            clauses.append("role = ?")
            args.append(role)
        rows = self.db.execute(
            f"""SELECT entry_id, ordinal, role, timestamp_ms, text FROM messages
                  WHERE {" AND ".join(clauses)} ORDER BY ordinal""",
            args,
        )
        messages = []
        used = 0
        truncated = False
        for row in rows:
            remaining = max_chars - used
            if remaining <= 0:
                truncated = True
                break
            text = row["text"]
            if len(text) > remaining:
                text = clip_text(text, remaining)
                truncated = True
            messages.append(
                {
                    "entry_id": row["entry_id"],
                    "ordinal": row["ordinal"],
                    "role": row["role"],
                    "timestamp": iso_from_millis(row["timestamp_ms"]),
                    "text": text,
                }
            )
            used += len(text)
            if truncated:
                break
        result = self._session_record(session)
        result.pop("updated_at_ms", None)
        result.update(
            {"messages": messages, "truncated": truncated, "max_chars": max_chars}
        )
        return result

    def status(self) -> dict:
        session_count = self.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        message_count = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        file_count = self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "schema_version": SCHEMA_VERSION,
            "database": str(self.db_path),
            "files": file_count,
            "sessions": session_count,
            "messages": message_count,
            "bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
        }

    @staticmethod
    def _session_record(row: sqlite3.Row) -> dict:
        result = {
            "session_id": row["session_id"],
            "cwd": row["cwd"],
            "started_at": iso_from_millis(row["started_at_ms"]),
            "updated_at": iso_from_millis(row["updated_at_ms"]),
            "updated_at_ms": row["updated_at_ms"],
            "message_count": row["message_count"],
        }
        if row["name"]:
            result["name"] = clip_text(row["name"], MAX_NAME_CHARS)
        return result


def prefix_fingerprint(path: Path, offset: int) -> str:
    """Digest every processed byte so a growing rewrite cannot masquerade as append."""
    digest = hashlib.sha256()
    remaining = offset
    try:
        with path.open("rb") as handle:
            while remaining > 0:
                chunk = handle.read(min(FINGERPRINT_BYTES, remaining))
                if not chunk:
                    return ""
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clip_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1] + "…"


def validate_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def build_match_query(query: str, *, exact: bool = False) -> str:
    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("query must not be empty")
    if exact:
        return f'"{normalized.replace(chr(34), chr(34) * 2)}"'
    tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)
    if not tokens:
        raise ValueError("query must contain a searchable word")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
