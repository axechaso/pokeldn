"""Missing glyphs, licensed subset identity, control IR and worst-name pixel layout."""

import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

from pokeldn.frlg.localization import LocalizationError, canonical, read_json
from pokeldn.frlg.localization.dialogue import (ASSETS, DEMO_DIALOGUE, compile_dialogue,
                                                preview_png, tokenize, write_artifacts)
from pokeldn.frlg.localization.font import BitmapFont, pack_pixels, unpack_pixels

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def font():
    return BitmapFont.parse(read_json(ASSETS / "demo-font.json"))


def test_all_committed_glyphs_roundtrip_and_preserve_source_license(font):
    assert font.source["license"] == "OFL-1.1"
    assert font.source["copyright"] == ["\u00a9 2014-2021 Adobe (http://www.adobe.com/)."]
    assert len(font.binary()) == len(font.glyphs) * 64
    for glyph in font.glyphs.values():
        assert pack_pixels(unpack_pixels(glyph.packed)) == glyph.packed
    assert BitmapFont.parse(font.as_dict()) == font


def test_pixel_pack_refuses_truncation_and_invalid_colors():
    for pixels in (bytes(255), bytes([4]) * 256):
        with pytest.raises(LocalizationError):
            pack_pixels(pixels)
    with pytest.raises(LocalizationError):
        unpack_pixels(bytes(63))


def test_unreviewed_font_changes_do_not_keep_the_same_identifier(font):
    value = font.as_dict()
    value["glyphs"]["A"]["pixels"] = "00" * 64
    with pytest.raises(LocalizationError, match="identifier"):
        BitmapFont.parse(value)


def test_minimal_chinese_dialogue_has_two_pages_and_name_bound(font):
    subset, report = compile_dialogue(DEMO_DIALOGUE, font)
    assert report["chinese_character_count"] == 26
    assert report["worst_name_layout"]["pages"] == 2
    assert report["worst_name_layout"]["max_line_width"] <= 224
    assert report["font_bytes"] == 64 * report["glyph_count"]
    assert report["game_encoding_assigned"] is False and report["software_only"]
    assert [t["native_control"] for t in report["tokens"] if "native_control" in t] == [
        "fd01", "fe", "fb", "fe"]
    assert subset.font_id == report["font_id"]
    assert compile_dialogue(DEMO_DIALOGUE, font)[1] == report


@pytest.mark.parametrize("text", ["{PLAYER}" + "你" * 9, "你\n好\n！", "{BOGUS}", "{PLAYER", "你好}\n", "\x01"])
def test_unsafe_layout_or_unknown_controls_are_rejected(font, text):
    with pytest.raises(LocalizationError):
        compile_dialogue(text, font)


@pytest.mark.parametrize("name", ["ABCDEFGH", "玩家", "NAME_", "", None])
def test_preview_cannot_change_original_name_field_encoding(font, name):
    with pytest.raises(LocalizationError):
        compile_dialogue(DEMO_DIALOGUE, font, player_name=name)


def test_missing_glyph_is_not_silently_dropped(font):
    with pytest.raises(LocalizationError, match="missing glyph"):
        compile_dialogue("龘", font)


def test_preview_is_a_valid_two_page_png_and_artifacts_include_license(font, tmp_path):
    subset, report = compile_dialogue(DEMO_DIALOGUE, font)
    png = preview_png(subset, report)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack_from(">II", png, 16) == (960, 320)
    pos, compressed = 8, bytearray()
    while pos < len(png):
        size = struct.unpack_from(">I", png, pos)[0]
        kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + size]
        assert zlib.crc32(kind + body) == struct.unpack_from(">I", png, pos + 8 + size)[0]
        if kind == b"IDAT":
            compressed.extend(body)
        pos += size + 12
    assert len(zlib.decompress(compressed)) == 320 * (1 + 960 * 3)
    output = tmp_path / "demo"
    write_artifacts(output, subset, report)
    assert {p.name for p in output.iterdir()} == {"font.bin", "font.json", "dialogue.ir.json", "preview.png", "OFL.txt"}
    assert (output / "OFL.txt").read_bytes() == (ASSETS / "OFL.txt").read_bytes()
    assert (output / "font.bin").read_bytes() == subset.binary()
    with pytest.raises(FileExistsError):
        write_artifacts(output, subset, report)


def test_text_cli_runs_without_pillow_or_game_files(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools/frlg/localization_text_build.py"),
                             "--output-dir", str(tmp_path / "demo")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["chinese_characters"] == 26 and report["pages"] == 2
    assert report["software_only"] and not report["game_encoding_assigned"]
