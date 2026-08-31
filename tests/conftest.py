from __future__ import annotations

import json
from pathlib import Path

import pytest


def write_session(
    root: Path,
    *,
    session_id: str = "01900000-0000-7000-8000-000000000001",
    cwd: str = "/workspace/example",
    timestamp: str = "2026-08-29T10:00:00.000Z",
    entries: list[dict] | None = None,
    terminated: bool = True,
) -> Path:
    directory = root / "--workspace-example--"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"2026-08-29T10-00-00.000Z_{session_id}.jsonl"
    lines = [
        {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
        },
        *(entries or []),
    ]
    payload = "\n".join(json.dumps(line) for line in lines)
    if terminated:
        payload += "\n"
    path.write_text(payload)
    return path


def message(
    entry_id: str, role: str, text: str, *, timestamp: str = "2026-08-29T10:01:00.000Z"
) -> dict:
    return {
        "type": "message",
        "id": entry_id,
        "parentId": None,
        "timestamp": timestamp,
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    path = tmp_path / "sessions"
    path.mkdir()
    return path
