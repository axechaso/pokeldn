import pytest

from pokeldn.frlg.localization import LocalizationError, read_json
from pokeldn.frlg.localization.dialogue import ASSETS
from pokeldn.frlg.localization.font import BitmapFont
from pokeldn.frlg.localization.game_demo import (
    FIRST_SELECTOR, HEADER_PATH, INTRO_DIALOGUE, LAST_SELECTOR,
    encode_demo, prepare_sources, temporary_sources,
)
from pokeldn.frlg.text import charmap


@pytest.fixture
def font():
    return BitmapFont.parse(read_json(ASSETS / "demo-font.json"))


def test_demo_uses_native_controls_and_preserves_latin(font):
    encoded, subset, selectors, report = encode_demo("A{PLAYER}，你好！\n探索{CLEAR}冒险", font)
    assert encoded.startswith(bytes((charmap._ENC["A"], 0xFD, 1)))
    assert encoded.count(b"\xfe") == 1
    assert encoded.count(b"\xfb") == 2
    assert encoded.endswith(b"\xfb\xff")
    assert set(selectors) == set("，你好！探索冒险")
    assert set(subset.glyphs) == set(selectors)
    assert all(FIRST_SELECTOR <= s <= LAST_SELECTOR for s in selectors.values())
    assert report["pages"] == 2
    for char, selector in selectors.items():
        assert bytes((0xF9, selector)) in encoded


def test_intro_fits_two_native_dialogue_pages(font):
    encoded, subset, selectors, report = encode_demo(INTRO_DIALOGUE, font)
    assert report["pages"] == 2
    assert report["max_line_width"] <= 224
    assert len(subset.glyphs) == 28
    assert not any(x >= 0xD0 for x in selectors.values())
    assert b"\xfd" not in encoded  # Player has not been named in Oak's first speech.


def test_extended_dialogue_covers_more_glyphs_and_controls(font):
    text = ("{PLAYER}，你好！Route 1\n欢迎来到宝可梦的世界。{CLEAR}"
            "。一世界伙伴你冒到可吧和好宝\n展开探新来梦欢迎界的索起迎险！")
    encoded, subset, selectors, report = encode_demo(text, font)
    assert report["pages"] == 2
    assert report["max_line_width"] == 218
    assert len(selectors) == 28
    assert set(selectors) == set(char for char in text if ord(char) > 127)
    assert encoded.count(b"\xfe") == 2
    assert encoded.count(b"\xfb") == 2
    assert encoded.endswith(b"\xfb\xff")
    assert len(encoded) == 102
    assert len(subset.glyphs) == 28


def test_source_difference_refuses_before_mutation(tmp_path, font):
    (tmp_path / "src").mkdir()
    path = tmp_path / "src/text.c"
    path.write_bytes(b"unreviewed source")
    with pytest.raises(LocalizationError, match="source differs"):
        prepare_sources(tmp_path, font)
    assert path.read_bytes() == b"unreviewed source"
    assert not (tmp_path / HEADER_PATH).exists()


def test_compiler_failure_restores_exact_bytes_and_timestamp(tmp_path):
    original = tmp_path / "text.c"
    original.write_bytes(b"original\r\n")
    modified = original.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="compiler failed"):
        with temporary_sources(tmp_path, {"text.c": b"demo\n", "demo.h": b"generated"}):
            assert original.read_bytes() == b"demo\n"
            raise RuntimeError("compiler failed")
    assert original.read_bytes() == b"original\r\n"
    assert original.stat().st_mtime_ns == modified
    assert not (tmp_path / "demo.h").exists()
