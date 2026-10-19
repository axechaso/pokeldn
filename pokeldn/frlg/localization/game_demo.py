"""Source-only FRLG rendering experiment. This is not a retail runtime patch.

Use the existing F9 extra-symbol decoder with a bounded, Latin-only glyph bank.
The source pin and original-ROM hash are checked by the build tool; the bank is
deliberately limited to the authored demo, not a full Chinese text encoding.
"""

from contextlib import contextmanager
import os
from pathlib import Path

from pokeldn.frlg.text import charmap
from . import LocalizationError, sha256
from .dialogue import tokenize, layout

SOURCE_COMMIT = "037335f4c725d7c9aecdac87066f2002b4bd7e14"
BASELINE_SHA1 = "baa452d0b24629dd7782cfc07a8984085dde1311"
SOURCE_HASHES = {
    "src/text.c": "5696f49443eeaaac74b530421ac7f0cbfc71996c557399583788111c322dd80c",
    "data/text/new_game_intro.inc": "052620086bb18c91bb2ceae680baceb00d2f0a24f789e449dcf8ecba7c022491",
    "include/characters.h": "6787db76f83c4f8426c5adbfa7aa061c46585db9522f1fef00ad888616199b98",
    "charmap.txt": "4da662317b3b5109a52064f9012d85b644d1dbf0f3f24243374aeef4cf25f061",
    "Makefile": "51960ea72311e53c272ebcc5b481021efba52fcc7db7f0ded4bfcebaf8bbdf47",
    "ld_script_rev10.ld": "3497e5c8450504bb06b62f490a3f07958adf89ebee6ed6cdab0075ef25b31adf",
}
INTRO_DIALOGUE = "你好！\n欢迎来到宝可梦的世界。{CLEAR}和伙伴一起探索，\n展开新的冒险吧！"
FIRST_SELECTOR, LAST_SELECTOR = 0x40, 0xCF
HEADER_PATH = "include/zh_demo_generated.h"


def encode_demo(text, font):
    tokens = tokenize(text)
    layout_report = layout(tokens, font)
    chars = sorted({t["char"] for t in tokens
                    if t["kind"] == "glyph" and t["char"] not in charmap._ENC})
    if not chars or len(chars) > LAST_SELECTOR - FIRST_SELECTOR + 1:
        raise LocalizationError("demo needs 1..144 extra glyphs")
    subset = font.subset(chars)
    selectors = {char: FIRST_SELECTOR + index for index, char in enumerate(chars)}
    encoded = bytearray()
    for token in tokens:
        if token["kind"] == "glyph":
            char = token["char"]
            if char in selectors:
                encoded.extend((0xF9, selectors[char]))
            else:
                encoded.append(charmap._ENC[char])
        else:
            encoded.extend(bytes.fromhex(token["native_control"]))
    # Oak's original message waits before proceeding to his next sentence.
    encoded.extend((0xFB, 0xFF))
    return bytes(encoded), subset, selectors, layout_report


def render_header(font, selectors):
    chars = sorted(selectors, key=selectors.get)
    if [selectors[c] for c in chars] != list(range(FIRST_SELECTOR, FIRST_SELECTOR + len(chars))):
        raise LocalizationError("demo selectors must be consecutive")
    rows = []
    for char in chars:
        glyph = font.glyphs[char]
        rows.append("    {" + ",".join(f"0x{x:02x}" for x in glyph.packed) + "},")
    data = "\n".join(rows)
    advances = ",".join(str(font.glyphs[c].advance) for c in chars)
    return f"""/* Generated PokeLDN demo font; OFL-1.1, see accompanying OFL.txt.
 * Only the international normal/male fonts use this experimental bank.
 * Existing extra symbols 00..17 and emoji D0..FE retain their original path.
 */
#define ZH_DEMO_FIRST {0x100 + FIRST_SELECTOR}
#define ZH_DEMO_COUNT {len(chars)}
static const u8 sZhDemoGlyphs[ZH_DEMO_COUNT][64] = {{
{data}
}};
static const u8 sZhDemoAdvances[ZH_DEMO_COUNT] = {{{advances}}};

static bool32 ZhDemoIsGlyph(u16 glyphId, bool32 japanese)
{{
    return !japanese && glyphId >= ZH_DEMO_FIRST
        && glyphId < ZH_DEMO_FIRST + ZH_DEMO_COUNT;
}}

static bool32 ZhDemoDecompressGlyph(u16 glyphId, bool32 japanese)
{{
    const u8 *src;
    u8 colors[4];
    u32 x, y, pair, offset;
    if (!ZhDemoIsGlyph(glyphId, japanese))
        return FALSE;
    src = sZhDemoGlyphs[glyphId - ZH_DEMO_FIRST];
    colors[0] = GetLastTextColor(2);
    colors[1] = GetLastTextColor(1);
    colors[2] = colors[3] = GetLastTextColor(0);
    for (y = 0; y < 16; y++)
    {{
        for (x = 0; x < 16; x += 2)
        {{
            pair = (src[y * 4 + x / 4] >> ((x & 3) * 2)) & 15;
            offset = ((y / 8) * 2 + x / 8) * 32 + (y & 7) * 4 + (x & 7) / 2;
            gGlyphInfo.pixels[offset] = colors[pair & 3] | (colors[pair >> 2] << 4);
        }}
    }}
    gGlyphInfo.width = sZhDemoAdvances[glyphId - ZH_DEMO_FIRST];
    gGlyphInfo.height = 16;
    return TRUE;
}}
"""


def _replace_once(source, before, after):
    if source.count(before) != 1:
        raise LocalizationError("source anchor changed; refusing demo adaptation")
    return source.replace(before, after, 1)


def prepare_sources(root, font):
    """Return reviewed file replacements without modifying the supplied source tree."""
    root = Path(root)
    originals = {}
    for name, expected in SOURCE_HASHES.items():
        originals[name] = (root / name).read_bytes()
        if sha256(originals[name]) != expected:
            raise LocalizationError(f"source differs from pinned decomp: {name}")
    if (root / HEADER_PATH).exists():
        raise LocalizationError("demo generated header already exists")
    encoded, subset, selectors, layout_report = encode_demo(INTRO_DIALOGUE, font)
    source = originals["src/text.c"].decode("utf-8")
    source = _replace_once(source, '#define TAG_CURSOR 0x8000',
                           '#include "zh_demo_generated.h"\n\n#define TAG_CURSOR 0x8000')
    for signature in ("void DecompressGlyph_Normal", "static void DecompressGlyph_Male"):
        anchor = signature + "(u16 glyphId, bool32 isJapanese)\n{\n"
        # agbcc accepts declarations before statements: insert the guard below its local declaration.
        start = source.index(anchor) + len(anchor)
        declaration = "    const u16 *glyphs;\n    int i;\n    u8 lastColor;\n"
        if not source[start:].startswith(declaration):
            raise LocalizationError("glyph decompressor local declaration changed")
        position = start + len(declaration)
        source = source[:position] + ("\n    if (ZhDemoDecompressGlyph(glyphId, isJapanese))\n"
                                     "        return;\n") + source[position:]
    for signature in ("static s32 GetGlyphWidth_Normal", "static s32 GetGlyphWidth_Male"):
        anchor = signature + "(u16 glyphId, bool32 isJapanese)\n{\n"
        source = _replace_once(source, anchor, anchor +
                               "    if (ZhDemoIsGlyph(glyphId, isJapanese))\n"
                               "        return sZhDemoAdvances[glyphId - ZH_DEMO_FIRST];\n")
    intro = originals["data/text/new_game_intro.inc"].decode("utf-8")
    start = intro.index("gOakSpeech_Text_WelcomeToTheWorld::\n")
    end = intro.index("gOakSpeech_Text_ThisWorld::\n", start)
    lines = ["gOakSpeech_Text_WelcomeToTheWorld::"]
    for pos in range(0, len(encoded), 16):
        lines.append("    .byte " + ", ".join(f"0x{x:02X}" for x in encoded[pos:pos + 16]))
    intro = intro[:start] + "\n".join(lines) + "\n\n" + intro[end:]
    replacements = {"src/text.c": source.encode("utf-8"),
                    "data/text/new_game_intro.inc": intro.encode("utf-8"),
                    HEADER_PATH: render_header(subset, selectors).encode("ascii")}
    report = {"source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_HASHES,
              "encoding": "frlg-f9-demo-bank-v1", "text": INTRO_DIALOGUE,
              "game_bytes": encoded.hex(), "glyph_selectors": selectors,
              "font_id": subset.font_id, "layout": layout_report,
              "retail_write_eligible": False, "switch_verified": False}
    return replacements, report


@contextmanager
def temporary_sources(root, replacements):
    """Restore exact original bytes and timestamps, including on compiler failure."""
    root = Path(root)
    originals = {}
    try:
        for name, data in replacements.items():
            path = root / name
            if path.exists():
                stat = path.stat()
                originals[path] = (path.read_bytes(), stat.st_atime_ns, stat.st_mtime_ns)
            else:
                originals[path] = None
            path.write_bytes(data)
        yield
    finally:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                data, atime, mtime = original
                path.write_bytes(data)
                os.utime(path, ns=(atime, mtime))
