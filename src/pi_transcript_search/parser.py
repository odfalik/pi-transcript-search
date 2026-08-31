"""Read Pi JSONL sessions without loading or mutating them through Pi."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class SessionHeader:
    session_id: str
    cwd: str
    timestamp: str | None


@dataclass(frozen=True)
class ParsedMessage:
    entry_id: str
    role: str
    text: str
    timestamp: str | None


@dataclass(frozen=True)
class ParsedLine:
    header: SessionHeader | None = None
    message: ParsedMessage | None = None
    name_seen: bool = False
    name: str | None = None


def text_content(content: object) -> str:
    """Return product-visible text while excluding thinking and tool calls."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def parse_object(obj: object) -> ParsedLine | None:
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind == "session":
        session_id = obj.get("id")
        cwd = obj.get("cwd")
        if not isinstance(session_id, str) or not session_id:
            return None
        return ParsedLine(
            header=SessionHeader(
                session_id=session_id,
                cwd=cwd if isinstance(cwd, str) else "",
                timestamp=obj.get("timestamp")
                if isinstance(obj.get("timestamp"), str)
                else None,
            )
        )
    if kind == "session_info":
        raw_name = obj.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        return ParsedLine(name_seen=True, name=name or None)
    if kind != "message":
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role not in {"user", "assistant"}:
        return None
    text = text_content(message.get("content"))
    if not text:
        return None
    entry_id = obj.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        return None
    timestamp = obj.get("timestamp")
    return ParsedLine(
        message=ParsedMessage(
            entry_id=entry_id,
            role=role,
            text=text,
            timestamp=timestamp if isinstance(timestamp, str) else None,
        )
    )


def iter_complete_lines(
    handle: BinaryIO, start: int = 0
) -> Iterator[tuple[int, ParsedLine | None]]:
    """Yield offsets after complete lines; leave an unterminated tail unread."""
    handle.seek(start)
    while True:
        raw = handle.readline()
        if not raw:
            return
        if not raw.endswith(b"\n"):
            return
        offset = handle.tell()
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            yield offset, None
            continue
        yield offset, parse_object(obj)


def read_header(path: Path) -> SessionHeader | None:
    try:
        with path.open("rb") as handle:
            for _offset, parsed in iter_complete_lines(handle):
                if parsed and parsed.header:
                    return parsed.header
                return None
    except OSError:
        return None
    return None
