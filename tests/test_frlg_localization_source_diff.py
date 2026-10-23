"""Alignment of English and translated source catalogs."""

from pathlib import Path

import pytest

from pokeldn.frlg.localization import LocalizationError
from pokeldn.frlg.localization.source_diff import compare_sources, translation_map


def _tree(root, body):
    directory = root / "data" / "text"
    directory.mkdir(parents=True)
    (directory / "sample.inc").write_text(body, encoding="utf-8")
    return root


def test_compare_sources_reports_changes_and_control_mismatches(tmp_path):
    base = _tree(tmp_path / "base", 'Greeting::\n    .string "Hello {PLAYER}$"\n')
    translated = _tree(tmp_path / "translated",
                        'Greeting::\n    .string "你好 {STR_VAR_1}$"\n'
                        'Extra::\n    .string "额外$"\n')
    comparison = compare_sources(base, translated)
    assert comparison["common_entries"] == 1
    assert comparison["changed_entries"] == 1
    assert len(comparison["extra_translated_keys"]) == 1
    assert comparison["placeholder_mismatches"] == ["data/text/sample.inc::Greeting"]
    assert comparison["translation_compatible"] is False
    assert translation_map(comparison) == {
        "data/text/sample.inc::Greeting": "你好 {STR_VAR_1}$"}
    with pytest.raises(LocalizationError, match="incompatible"):
        translation_map(comparison, strict=True)


def test_compare_sources_normalizes_alias_macros_to_game_bytes(tmp_path):
    base = _tree(tmp_path / "base", 'Greeting::\n    .string "Hello {STR_VAR_1}$"\n')
    translated = _tree(tmp_path / "translated",
                        'Greeting::\n    .string "你好 {B_COPY_VAR_1}$"\n')
    comparison = compare_sources(base, translated,
                                 macros={"STR_VAR_1": b"\\xfd\\x02",
                                         "B_COPY_VAR_1": b"\\xfd\\x02"})
    assert comparison["placeholder_mismatches"] == []
    assert comparison["translation_compatible"] is True


def test_compare_sources_normalizes_split_macro_arguments(tmp_path):
    base = _tree(tmp_path / "base",
                 'Greeting::\n    .string "Ready {PLAY_BGM}{MUS_TEST}$"\n')
    translated = _tree(tmp_path / "translated",
                        'Greeting::\n    .string "准备 {PLAY_BGM 0x1B 0x01}$"\n')
    comparison = compare_sources(
        base, translated,
        macros={"PLAY_BGM": b"\xfc\x0b", "MUS_TEST": b"\x1b\x01"})
    assert comparison["placeholder_mismatches"] == []
