"""Run the bundled Python CLI from an npm-installed Pi package."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "src"
if str(SOURCES) not in sys.path:
    sys.path.insert(0, str(SOURCES))

main = importlib.import_module("pi_transcript_search.cli").main

if __name__ == "__main__":
    raise SystemExit(main())
