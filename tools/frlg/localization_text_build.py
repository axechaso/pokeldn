#!/usr/bin/env python3
"""Build a Chinese dialogue font subset, Unicode IR and offline preview. No game bytes emitted."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, read_json
from pokeldn.frlg.localization.dialogue import ASSETS, DEMO_DIALOGUE, compile_dialogue, write_artifacts
from pokeldn.frlg.localization.font import BitmapFont


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", type=Path, help="UTF-8 source; defaults to the authored demo dialogue")
    parser.add_argument("--font", type=Path, default=ASSETS / "demo-font.json")
    parser.add_argument("--player-name", default="PLAYER")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        font = BitmapFont.parse(read_json(args.font))
        text = args.text.read_text(encoding="utf-8") if args.text else DEMO_DIALOGUE
        subset, report = compile_dialogue(text, font, player_name=args.player_name)
        write_artifacts(args.output_dir, subset, report)
        print(json.dumps({"software_only": True, "game_encoding_assigned": False,
                          "chinese_characters": report["chinese_character_count"],
                          "pages": report["worst_name_layout"]["pages"],
                          "max_line_width": report["worst_name_layout"]["max_line_width"],
                          "glyphs": report["glyph_count"], "font_bytes": report["font_bytes"],
                          "output": str(args.output_dir)}, indent=2))
        return 0
    except (LocalizationError, OSError) as exc:
        parser.exit(2, f"dialogue build refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
