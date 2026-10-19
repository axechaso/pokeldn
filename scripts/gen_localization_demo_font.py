#!/usr/bin/env python3
"""Rasterize the explicitly supplied Noto source font (Pillow needed only to regenerate)."""

import argparse
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import canonical, sha256
from pokeldn.frlg.localization.dialogue import DEMO_DIALOGUE, tokenize
from pokeldn.frlg.localization.font import BitmapFont, Glyph, pack_pixels

SOURCE_COMMIT = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
SOURCE_PATH = "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
SOURCE_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"


def copyrights(data):
    count = struct.unpack_from(">H", data, 4)[0]
    for i in range(count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, 12 + i * 16)
        if tag == b"name":
            _, records, strings = struct.unpack_from(">HHH", data, offset)
            names = set()
            for n in range(records):
                platform, _, _, name, length, at = struct.unpack_from(">6H", data, offset + 6 + n * 12)
                if name == 0:
                    raw = data[offset + strings + at:offset + strings + at + length]
                    names.add(raw.decode("utf-16-be" if platform in (0, 3) else "mac_roman"))
            return sorted(names)
    raise ValueError("source font has no copyright metadata")


def main():
    from PIL import Image, ImageDraw, ImageFont, __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--text", type=Path, help="UTF-8 dialogue whose glyphs should be included")
    parser.add_argument("--output", type=Path, default=ROOT / "pokeldn/frlg/localization/assets/demo-font.json")
    args = parser.parse_args()
    data = args.source.read_bytes()
    if sha256(data) != SOURCE_SHA256:
        parser.error("source font does not match the pinned Noto source SHA-256")
    font = ImageFont.truetype(str(args.source), size=14)
    text = args.text.read_text(encoding="utf-8") if args.text else DEMO_DIALOGUE
    chars = {t["char"] for t in tokenize(text) if t["kind"] == "glyph"}
    chars.update(chr(x) for x in range(32, 127))
    glyphs = {}
    for char in sorted(chars):
        image = Image.new("L", (16, 16))
        ImageDraw.Draw(image).text((0, 13), char, font=font, fill=255, anchor="ls")
        glyphs[char] = Glyph(min(16, max(1, math.ceil(font.getlength(char)))),
                             pack_pixels(bytes((value * 3 + 127) // 255 for value in image.tobytes())))
    source = {"url": f"https://github.com/notofonts/noto-cjk/blob/{SOURCE_COMMIT}/{SOURCE_PATH}",
              "commit": SOURCE_COMMIT, "sha256": sha256(data), "license": "OFL-1.1",
              "copyright": copyrights(data), "rasterizer": f"Pillow {__version__}, 14px, baseline 13"}
    result = BitmapFont("FRLG Prototype Glyphs", source, glyphs)
    output = args.output
    output.write_bytes(canonical(result.as_dict()) + b"\n")
    print(f"wrote {len(glyphs)} glyphs, {len(result.binary())} bytes: {output}")


if __name__ == "__main__":
    main()
