from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import message, write_session

from pi_transcript_search.index import ConversationIndex


def test_indexes_complete_user_and_assistant_text_without_tool_noise(
    tmp_path: Path, sessions_dir: Path
) -> None:
    tail = "neutral filler " * 1000 + "deep-tail-needle"
    tool = {
        "type": "message",
        "id": "tool0001",
        "parentId": "assist01",
        "timestamp": "2026-08-29T10:02:00.000Z",
        "message": {
            "role": "toolResult",
            "toolName": "bash",
            "content": [{"type": "text", "text": "tool-only-needle"}],
        },
    }
    write_session(
        sessions_dir,
        entries=[
            message("user0001", "user", "investigate the indexing pipeline"),
            message("assist01", "assistant", tail),
            tool,
        ],
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        stats = index.refresh(sessions_dir)
        assert stats.indexed_messages == 2
        assert len(index.search("deep-tail-needle")) == 1
        assert index.search("tool-only-needle") == []


def test_incremental_append_and_session_rename(
    tmp_path: Path, sessions_dir: Path
) -> None:
    path = write_session(
        sessions_dir, entries=[message("user0001", "user", "initial topic")]
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        first = index.refresh(sessions_dir)
        assert first.changed_files == 1
        second = index.refresh(sessions_dir)
        assert second.changed_files == 0

        with path.open("a") as handle:
            handle.write(
                json.dumps(
                    {"type": "session_info", "id": "name0001", "name": "Useful session"}
                )
                + "\n"
            )
            handle.write(
                json.dumps(message("assist01", "assistant", "appended evidence")) + "\n"
            )
        third = index.refresh(sessions_dir)
        assert third.changed_files == 1
        assert third.indexed_messages == 1
        result = index.search("appended evidence")[0]
        assert result["name"] == "Useful session"
        assert result["message_count"] == 2


def test_larger_rewrite_rebuilds_instead_of_appending(
    tmp_path: Path, sessions_dir: Path
) -> None:
    write_session(
        sessions_dir,
        entries=[message("user0001", "user", "stale rewrite needle")],
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        assert index.search("stale rewrite needle")
        write_session(
            sessions_dir,
            entries=[
                message("user0002", "user", "fresh rewrite needle " + "padding " * 100),
                message("assist02", "assistant", "second fresh message"),
            ],
        )
        index.refresh(sessions_dir)
        assert index.search("stale rewrite needle") == []
        assert index.search("fresh rewrite needle")[0]["message_count"] == 2


def test_partial_append_waits_for_newline(tmp_path: Path, sessions_dir: Path) -> None:
    path = write_session(sessions_dir, entries=[message("user0001", "user", "first")])
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        payload = json.dumps(message("assist01", "assistant", "eventually complete"))
        with path.open("a") as handle:
            handle.write(payload)
        index.refresh(sessions_dir)
        assert index.search("eventually complete") == []
        with path.open("a") as handle:
            handle.write("\n")
        index.refresh(sessions_dir)
        assert len(index.search("eventually complete")) == 1


def test_invalid_files_are_cached_until_they_change(
    tmp_path: Path, sessions_dir: Path
) -> None:
    invalid = sessions_dir / "unrelated.jsonl"
    invalid.write_text('{"type":"custom","id":"not-a-session"}\n')
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        first = index.refresh(sessions_dir)
        second = index.refresh(sessions_dir)
        assert first.changed_files == 1
        assert second.changed_files == 0
        assert index.status()["sessions"] == 0
        invalid.write_text(
            '{"type":"session","id":"now-valid","cwd":"/workspace/example"}\n'
            + json.dumps(message("user0001", "user", "now searchable"))
            + "\n"
        )
        third = index.refresh(sessions_dir)
        assert third.changed_files == 1
        assert index.search("now searchable")


def test_deleted_source_is_pruned(tmp_path: Path, sessions_dir: Path) -> None:
    path = write_session(
        sessions_dir, entries=[message("user0001", "user", "temporary needle")]
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        assert index.search("temporary needle")
        path.unlink()
        stats = index.refresh(sessions_dir)
        assert stats.removed_files == 1
        assert index.search("temporary needle") == []


def test_exact_phrase_and_word_prefix_have_distinct_semantics(
    tmp_path: Path, sessions_dir: Path
) -> None:
    write_session(
        sessions_dir,
        entries=[
            message("user0001", "user", "conversation indexing is useful"),
            message("assist01", "assistant", "conversation search is exact here"),
        ],
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        assert index.search("convers search")
        exact = index.search("conversation search", exact=True)
        assert exact[0]["match_count"] == 1
        assert exact[0]["matches"][0]["role"] == "assistant"


def test_cwd_filter_treats_sql_wildcards_literally(
    tmp_path: Path, sessions_dir: Path
) -> None:
    write_session(
        sessions_dir,
        session_id="01900000-0000-7000-8000-000000000001",
        cwd="/workspace/literal%_name",
        entries=[message("user0001", "user", "shared needle")],
    )
    write_session(
        sessions_dir,
        session_id="01900000-0000-7000-8000-000000000002",
        cwd="/workspace/literalXXname",
        entries=[message("user0002", "user", "shared needle")],
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        results = index.search("shared needle", cwd="literal%_name")
        assert [result["cwd"] for result in results] == ["/workspace/literal%_name"]


def test_search_snippets_have_a_hard_character_cap(
    tmp_path: Path, sessions_dir: Path
) -> None:
    write_session(
        sessions_dir,
        entries=[message("user0001", "user", "needle " + "x" * 10_000)],
    )
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        snippet = index.search("needle")[0]["matches"][0]["snippet"]
        assert len(snippet) <= 400


def test_show_is_bounded_and_role_aware(tmp_path: Path, sessions_dir: Path) -> None:
    entries = [
        message(
            f"entry{i:03d}",
            "user" if i % 2 == 0 else "assistant",
            f"message {i} " + "x" * 50,
        )
        for i in range(10)
    ]
    write_session(sessions_dir, entries=entries)
    with ConversationIndex(tmp_path / "index.sqlite") as index:
        index.refresh(sessions_dir)
        result = index.show(
            "01900000-0000-7000-8000-000000000001",
            around=5,
            context=1,
            role="assistant",
            max_chars=70,
        )
        assert result is not None
        assert [row["ordinal"] for row in result["messages"]] == [5]
        assert result["truncated"] is False

        truncated = index.show("01900000-0000-7000-8000-000000000001", max_chars=20)
        assert truncated is not None and truncated["truncated"] is True
        assert sum(len(row["text"]) for row in truncated["messages"]) <= 20


def test_index_database_is_private(tmp_path: Path) -> None:
    path = tmp_path / "private" / "index.sqlite"
    path.parent.mkdir(mode=0o755)
    path.touch(mode=0o644)
    os.chmod(path.parent, 0o755)
    os.chmod(path, 0o644)

    with ConversationIndex(path):
        pass

    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700


def test_index_fails_closed_when_permissions_cannot_be_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_chmod(_path: Path, _mode: int) -> None:
        raise OSError("denied")

    monkeypatch.setattr(os, "chmod", deny_chmod)
    with pytest.raises(PermissionError, match="cannot secure transcript index path"):
        ConversationIndex(tmp_path / "private" / "index.sqlite")
