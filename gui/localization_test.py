"""Open the existing desktop client directly at its localization test."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["POKELDN_START_GAME"] = "frlg"
os.environ["POKELDN_START_TOOL"] = "frlg-localization-story"
os.environ["POKELDN_GUI_TITLE"] = "PokeLDN · NS 剧情汉化测试"

from gui.main import run  # noqa: E402


if __name__ == "__main__":
    run()
