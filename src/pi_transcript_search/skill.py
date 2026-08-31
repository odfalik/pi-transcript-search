"""Install the bundled Agent Skill into Pi's global skill directory."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def install_skill(force: bool = False) -> Path:
    source = files("pi_transcript_search").joinpath("bundled_skill", "SKILL.md")
    destination = (
        Path.home() / ".pi" / "agent" / "skills" / "pi-transcript-search" / "SKILL.md"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() == content:
            return destination
        if not force:
            raise ValueError(
                f"skill already exists with different content: {destination}; pass --force"
            )
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return destination
