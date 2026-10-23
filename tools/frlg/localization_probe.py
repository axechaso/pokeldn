#!/usr/bin/env python3
"""Read the FRLG runtime through PokeLDN; the packaged client shares the same entry point."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pokeldn.frlg.localization.cli import build_parser, host_config, main  # noqa: E402,F401
from pokeldn.frlg.localization.host import LocalizationHostApplication  # noqa: E402,F401


if __name__ == "__main__":
    sys.exit(main())
