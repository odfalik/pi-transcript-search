from __future__ import annotations

import io
import json

from pi_transcript_search.parser import (
    iter_complete_lines,
    parse_object,
    text_content,
)


def test_text_content_keeps_only_visible_text() -> None:
    content = [
        {"type": "thinking", "thinking": "private"},
        {"type": "text", "text": "visible one"},
        {"type": "toolCall", "name": "bash", "arguments": {"command": "secret"}},
        {"type": "text", "text": "visible two"},
    ]
    assert text_content(content) == "visible one\nvisible two"


def test_parser_excludes_tool_results_and_custom_messages() -> None:
    for role in ("toolResult", "custom", "branchSummary"):
        obj = {
            "type": "message",
            "id": "deadbeef",
            "message": {"role": role, "content": [{"type": "text", "text": "noise"}]},
        }
        assert parse_object(obj) is None


def test_unterminated_tail_is_not_consumed() -> None:
    first = (
        json.dumps({"type": "session", "id": "session-1", "cwd": "/tmp"}).encode()
        + b"\n"
    )
    partial = json.dumps({"type": "session_info", "name": "unfinished"}).encode()
    rows = list(iter_complete_lines(io.BytesIO(first + partial)))
    assert len(rows) == 1
    assert rows[0][0] == len(first)
    assert rows[0][1] is not None and rows[0][1].header is not None
