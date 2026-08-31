from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import message, write_session

from pi_transcript_search import __version__
from pi_transcript_search.cli import build_parser, main


def test_search_json_contract(tmp_path: Path, sessions_dir: Path, capsys) -> None:
    write_session(
        sessions_dir, entries=[message("user0001", "user", "searchable conversation")]
    )
    db = tmp_path / "index.sqlite"
    code = main(
        [
            "--db",
            str(db),
            "--sessions-dir",
            str(sessions_dir),
            "search",
            "searchable",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["meta"] == {"returned": 1}
    assert payload["results"][0]["matches"][0]["role"] == "user"
    assert "resume" not in payload["results"][0]
    assert "path" not in payload["results"][0]


def test_missing_sessions_directory_is_a_clean_error(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--db",
            str(tmp_path / "index.sqlite"),
            "--sessions-dir",
            str(tmp_path / "missing"),
            "list",
            "--json",
        ]
    )
    assert code == 2
    assert "Pi sessions directory not found" in capsys.readouterr().err


def test_invalid_date_filter_combination_is_rejected(
    tmp_path: Path, sessions_dir: Path, capsys
) -> None:
    write_session(sessions_dir, entries=[message("user0001", "user", "hello")])
    code = main(
        [
            "--db",
            str(tmp_path / "index.sqlite"),
            "--sessions-dir",
            str(sessions_dir),
            "list",
            "--days",
            "7",
            "--date",
            "today",
            "--json",
        ]
    )
    assert code == 2
    assert "choose only one" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["list", "--limit", "-1"],
        ["search", "needle", "--max-snippets", "100"],
        ["show", "session", "--context", "-1"],
        ["show", "session", "--max-chars", "1000000"],
    ],
)
def test_numeric_output_bounds_are_enforced(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(arguments)


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        build_parser().parse_args(["--version"])
    assert capsys.readouterr().out.strip() == f"pi-transcript-search {__version__}"


def test_python_and_npm_versions_match() -> None:
    root = Path(__file__).parents[1]
    package = json.loads((root / "package.json").read_text())
    assert package["version"] == __version__


def test_bundled_and_repository_skills_match() -> None:
    root = Path(__file__).parents[1]
    assert (root / "skills/pi-transcript-search/SKILL.md").read_bytes() == (
        root / "src/pi_transcript_search/bundled_skill/SKILL.md"
    ).read_bytes()
