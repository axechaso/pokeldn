#!/usr/bin/env python3
"""The desktop client's read-only FRLG localization test."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeldn.frlg.localization.cli import build_parser, main  # noqa: E402,F401


if __name__ == "__main__":
    sys.exit(main())
