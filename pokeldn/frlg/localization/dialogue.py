"""Unicode/control IR, worst-name pixel preflight and a software-only dialogue preview.

No Chinese byte prefix or game render hook is assigned by this module. The IR cannot be
loaded as a ROM patch: that requires the target build's complete string/render adaptation.
"""

from pathlib import Path
import struct
import zlib

from pokeldn.frlg.text import charmap
from . import LocalizationError, canonical, integer
from .font import unpack_pixels

DEMO_DIALOGUE = "{PLAYER}，你好！\n欢迎来到宝可梦的世界。{CLEAR}和伙伴一起探索，\n展开新的冒险吧！"
ASSETS = Path(__file__).with_name("assets")


def tokenize(text):
    if not isinstance(text, str) or not 0 < len(text) <= 65535:
        raise LocalizationError("dialogue must contain 1..65535 Unicode characters")
    tokens, pos = [], 0
    while pos < len(text):
        char = text[pos]
        if char == "{":
            end = text.find("}", pos)
            if end < 0:
                raise LocalizationError("unterminated dialogue control")
            control = text[pos + 1:end]
            if control == "PLAYER":
                tokens.append({"kind": "variable", "name": "PLAYER", "native_control": "fd01"})
            elif control == "CLEAR":
                tokens.append({"kind": "clear", "native_control": "fb"})
            else:
                raise LocalizationError(f"unsupported dialogue control: {control}")
            pos = end + 1
            continue
        if char == "\n":
            tokens.append({"kind": "newline", "native_control": "fe"})
        elif char == "}" or ord(char) < 32:
            raise LocalizationError("unsupported dialogue character/control")
        else:
            tokens.append({"kind": "glyph", "char": char})
        pos += 1
    return tokens


def layout(tokens, font, *, player_name=None, width=224, lines=2):
    integer(width, "window width", 1, 224)
    integer(lines, "window lines", 1, 2)
    if player_name is not None and (not isinstance(player_name, str) or not 1 <= len(player_name) <= 7
                                   or any(c not in charmap._ENC for c in player_name)):
        raise LocalizationError("preview PLAYER must fit the unchanged international 7-character field")
    page, row, x, widest, placed = 0, 0, 0, 0, []
    for token in tokens:
        kind = token["kind"]
        if kind == "clear":
            page, row, x = page + 1, 0, 0
            if page >= 64:
                raise LocalizationError("prototype dialogue exceeds 64 pages")
            continue
        if kind == "newline":
            row, x = row + 1, 0
            if row >= lines:
                raise LocalizationError("dialogue overflows window lines; insert {CLEAR}")
            continue
        if kind == "variable" and player_name is None:
            advance = 7 * 16  # Every accepted bitmap glyph advances <=16; no save encoding changes.
            placed.append({"page": page, "row": row, "x": x, "variable": "PLAYER",
                           "reserved_width": advance})
            x += advance
        else:
            chars = player_name if kind == "variable" else token["char"]
            for char in chars:
                if char not in font.glyphs:
                    raise LocalizationError(f"missing glyph: {char}")
                placed.append({"page": page, "row": row, "x": x, "char": char})
                x += font.glyphs[char].advance
        if x > width:
            raise LocalizationError(f"dialogue overflows page {page + 1}, line {row + 1}: {x}>{width}px")
        widest = max(widest, x)
    return {"pages": page + 1, "max_line_width": widest, "glyphs": placed}


def compile_dialogue(text, font, *, player_name="PLAYER"):
    if not isinstance(player_name, str):
        raise LocalizationError("preview PLAYER must be text")
    tokens = tokenize(text)
    required = {t["char"] for t in tokens if t["kind"] == "glyph"}
    # Keep the available original Latin fallback for names; the glyph set stays deterministic.
    required.update(c for c in font.glyphs if c in charmap._ENC)
    required.update("OFFLINE PROTOTYPE" + player_name)
    subset = font.subset(required)
    worst = layout(tokens, subset)
    preview = layout(tokens, subset, player_name=player_name)
    return subset, {"format_version": 1, "encoding": "unicode-control-ir-v1",
                    "game_encoding_assigned": False, "software_only": True,
                    "font_id": subset.font_id, "source": text, "tokens": tokens,
                    "glyph_order": sorted(subset.glyphs),
                    "worst_name_layout": worst, "preview_layout": preview,
                    "chinese_character_count": sum(0x4E00 <= ord(t["char"]) <= 0x9FFF
                                                   for t in tokens if t["kind"] == "glyph"),
                    "glyph_count": len(subset.glyphs), "font_bytes": len(subset.binary())}


def preview_png(font, report, *, scale=2):
    """A tiny standard-library rasterizer; this is a preview, not a game screenshot."""
    pages = report["preview_layout"]["pages"]
    width, height = 240 * pages, 160
    pixels = [(229, 239, 232)] * (width * height)

    def rect(x, y, w, h, color):
        for yy in range(y, y + h):
            pixels[yy * width + x:yy * width + x + w] = [color] * w

    def glyph(char, x, y):
        palette = ((250, 250, 242), (174, 181, 177), (87, 101, 96), (27, 46, 39))
        for i, color in enumerate(unpack_pixels(font.glyphs[char].packed)):
            xx, yy = x + i % 16, y + i // 16
            if color and 0 <= xx < width and 0 <= yy < height:
                pixels[yy * width + xx] = palette[color]

    for page in range(pages):
        base = page * 240
        rect(base + 8, 12, 224, 20, (250, 250, 242))
        x = base + 12
        for char in "OFFLINE PROTOTYPE":
            glyph(char, x, 14)
            x += font.glyphs[char].advance
        rect(base + 4, 100, 232, 56, (38, 74, 59))
        rect(base + 7, 103, 226, 50, (250, 250, 242))
    for entry in report["preview_layout"]["glyphs"]:
        glyph(entry["char"], entry["page"] * 240 + 8 + entry["x"], 109 + entry["row"] * 16)
    rows = []
    for y in range(height):
        line = b"".join(bytes(color) * scale for color in pixels[y * width:(y + 1) * width])
        rows.extend([b"\x00" + line] * scale)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width * scale, height * scale, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def write_artifacts(directory, font, report):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "font.bin").write_bytes(font.binary())
    (directory / "font.json").write_bytes(canonical(font.as_dict()) + b"\n")
    (directory / "dialogue.ir.json").write_bytes(canonical(report) + b"\n")
    (directory / "preview.png").write_bytes(preview_png(font, report))
    (directory / "OFL.txt").write_bytes((ASSETS / "OFL.txt").read_bytes())
