"""Command-line interface for Pi conversation search."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .dates import day_bounds, explicit_bounds, rolling_bounds
from .index import (
    MAX_CONTEXT,
    MAX_LIMIT,
    MAX_SHOW_CHARS,
    MAX_SNIPPETS,
    SCHEMA_VERSION,
    ConversationIndex,
    RefreshStats,
)
from .skill import install_skill


def default_sessions_dir() -> Path:
    override = os.environ.get("PI_TRANSCRIPT_SESSIONS_DIR")
    return Path(override) if override else Path.home() / ".pi" / "agent" / "sessions"


def default_db_path() -> Path:
    override = os.environ.get("PI_TRANSCRIPT_SEARCH_DB")
    if override:
        return Path(override)
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "pi-transcript-search" / "index.sqlite"


def _date_bounds(args: argparse.Namespace) -> tuple[int | None, int | None]:
    selected = [
        args.days is not None,
        args.date is not None,
        args.since is not None or args.until is not None,
    ]
    if sum(bool(value) for value in selected) > 1:
        raise ValueError("choose only one of --days, --date, or --since/--until")
    if args.days is not None:
        return rolling_bounds(args.days)
    if args.date:
        return day_bounds(args.date)
    return explicit_bounds(args.since, args.until)


def _refresh(index: ConversationIndex, args: argparse.Namespace) -> RefreshStats | None:
    if getattr(args, "no_refresh", False):
        return None
    sessions_dir = Path(args.sessions_dir)
    if not sessions_dir.is_dir():
        raise FileNotFoundError(f"Pi sessions directory not found: {sessions_dir}")
    return index.refresh(sessions_dir, rebuild=getattr(args, "rebuild", False))


def _payload(results: list[dict], refresh: RefreshStats | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "refresh": refresh.record() if refresh else None,
        "results": results,
        "meta": {"returned": len(results)},
    }


def _print(payload: object, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    if isinstance(payload, dict) and "results" in payload:
        results = payload["results"]
        if not results:
            print("No matching Pi conversations found.")
            return
        for result in results:
            name = f"  {result['name']}" if result.get("name") else ""
            print(
                f"\n{result.get('updated_at') or result.get('started_at') or '?'}  {result['session_id']}{name}"
            )
            print(f"  cwd: {result['cwd']}")
            if "match_count" in result:
                print(f"  matches: {result['match_count']}")
                for match in result.get("matches", []):
                    print(
                        f"    [{match['ordinal']}] {match['role']}: {match['snippet']}"
                    )
        return
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def bounded_int(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def add_shared_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days", type=bounded_int("days", 1, 36_500), help="rolling window in days"
    )
    parser.add_argument("--date", help="local day: YYYY-MM-DD, today, or yesterday")
    parser.add_argument(
        "--since", help="include sessions starting on/after this local day"
    )
    parser.add_argument("--until", help="include sessions through this local day")
    parser.add_argument("--cwd", help="case-insensitive substring of the session cwd")
    parser.add_argument("--limit", type=bounded_int("limit", 1, MAX_LIMIT), default=20)
    parser.add_argument(
        "--no-refresh", action="store_true", help="query the existing index only"
    )
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-transcript-search",
        description="Search local Pi conversation history with bounded, role-aware evidence.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--db", default=str(default_db_path()), help="index database path"
    )
    parser.add_argument(
        "--sessions-dir", default=str(default_sessions_dir()), help="Pi session root"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search", help="search user and assistant conversation text"
    )
    search.add_argument("query")
    search.add_argument("--exact", action="store_true", help="match an exact phrase")
    search.add_argument(
        "--max-snippets",
        type=bounded_int("max-snippets", 1, MAX_SNIPPETS),
        default=3,
    )
    add_shared_filters(search)

    listing = subparsers.add_parser("list", help="list conversations by time")
    add_shared_filters(listing)
    listing.set_defaults(limit=50)

    show = subparsers.add_parser("show", help="read a bounded conversation slice")
    show.add_argument("session_id")
    show.add_argument(
        "--around",
        type=bounded_int("around", 0, 2_147_483_647),
        help="message ordinal to center",
    )
    show.add_argument(
        "--context",
        type=bounded_int("context", 0, MAX_CONTEXT),
        default=3,
        help="messages before and after --around",
    )
    show.add_argument("--role", choices=("user", "assistant"))
    show.add_argument(
        "--max-chars",
        type=bounded_int("max-chars", 1, MAX_SHOW_CHARS),
        default=20_000,
    )
    show.add_argument("--no-refresh", action="store_true")
    show.add_argument("--json", action="store_true")

    index = subparsers.add_parser("index", help="refresh the disposable local index")
    index.add_argument("--rebuild", action="store_true")
    index.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="show local index status")
    status.add_argument("--json", action="store_true")

    install = subparsers.add_parser(
        "install-skill", help="install the bundled Pi skill"
    )
    install.add_argument("--force", action="store_true")
    install.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install-skill":
            destination = install_skill(force=args.force)
            _print({"installed": str(destination)}, args.json)
            return 0

        with ConversationIndex(Path(args.db)) as index:
            if args.command == "status":
                _print(index.status(), args.json)
                return 0
            if args.command == "index":
                sessions_dir = Path(args.sessions_dir)
                if not sessions_dir.is_dir():
                    raise FileNotFoundError(
                        f"Pi sessions directory not found: {sessions_dir}"
                    )
                refresh = index.refresh(sessions_dir, rebuild=args.rebuild)
                _print(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "refresh": refresh.record(),
                        **index.status(),
                    },
                    args.json,
                )
                return 0

            refresh = _refresh(index, args)
            if args.command == "search":
                since_ms, until_ms = _date_bounds(args)
                results = index.search(
                    args.query,
                    since_ms=since_ms,
                    until_ms=until_ms,
                    cwd=args.cwd,
                    limit=args.limit,
                    max_snippets=args.max_snippets,
                    exact=args.exact,
                )
                _print(_payload(results, refresh), args.json)
                return 0
            if args.command == "list":
                since_ms, until_ms = _date_bounds(args)
                results = index.list_sessions(
                    since_ms=since_ms,
                    until_ms=until_ms,
                    cwd=args.cwd,
                    limit=args.limit,
                )
                _print(_payload(results, refresh), args.json)
                return 0
            if args.command == "show":
                result = index.show(
                    args.session_id,
                    around=args.around,
                    context=args.context,
                    role=args.role,
                    max_chars=args.max_chars,
                )
                if result is None:
                    print(
                        f"Pi conversation not found: {args.session_id}", file=sys.stderr
                    )
                    return 1
                _print(result, args.json)
                return 0
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
