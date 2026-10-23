"""Complete-source catalog extraction and Chinese charmap coverage checks."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from pokeldn.frlg.localization import LocalizationError
from pokeldn.frlg.localization.catalog import (manifest, report, scan_source,
                                                validate_translations)
from pokeldn.frlg.localization.chinese import coverage, coverage_texts, parse_charmap


ROOT = Path(__file__).resolve().parents[1]


def _source_tree(tmp_path):
    (tmp_path / "data" / "text").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "include").mkdir(exist_ok=True)
    (tmp_path / "data" / "text" / "sample.inc").write_text(
        "Greeting::\n"
        "    .string \"你好\\n世界\\l下一页\\p{PLAYER}$\"\n"
        "    .braille \"盲\"\n"
        "Other::\n"
        "    .string \"第二页$\"\n",
        encoding="utf-8")
    (tmp_path / "src" / "strings.c").write_text(
        'const u8 Named[] = _(\"名字{PLAYER}$\");\n'
        'const char *anonymous = __(\"匿名\\n\");\n'
        'const char *escaped = _(\"引号\\\\\\\"测试\");\n'
        'const u8 Joined[] = _(\n'
        '    \"跨行\\n\"\n'
        '    \"文本$\");\n',
        encoding="utf-8")
    (tmp_path / "include" / "ignored.h").write_text("#define NO_TEXT 1\n", encoding="utf-8")
    return tmp_path


def test_scan_source_preserves_controls_and_is_deterministic(tmp_path):
    entries = scan_source(_source_tree(tmp_path))
    assert [entry.key for entry in entries] == [
        "data/text/sample.inc::Greeting",
        "data/text/sample.inc::Other",
        "src/strings.c::Named",
        "src/strings.c:2:1",
        "src/strings.c:3:1",
        "src/strings.c::Joined",
    ]
    assert entries[0].text == "你好\n世界\x0b下一页\x0c{PLAYER}$盲"
    assert entries[2].symbol == "Named" and entries[2].kind == "c"
    assert entries[4].text == '引号\\"测试'
    assert scan_source(_source_tree(tmp_path)) == entries


def test_report_counts_glyphs_controls_and_placeholders(tmp_path):
    summary = report(scan_source(_source_tree(tmp_path)))
    assert summary["entries"] == 6
    assert summary["files"] == 2
    assert summary["controls"] == {"newline": 3, "scroll": 1, "clear": 1}
    assert summary["placeholders"] == {"{PLAYER}": 2}
    assert summary["unique_characters"] == len(set(summary["extra_glyphs"])) + summary["native_characters"]


def test_validate_translations_requires_exact_controls_and_terminators(tmp_path):
    entries = scan_source(_source_tree(tmp_path))
    translations = {entry.key: entry.text for entry in entries}
    assert validate_translations(entries, translations)
    for bad in (
        {**translations, "unknown": "x"},
        {key: value for key, value in translations.items() if key != entries[0].key},
    ):
        with pytest.raises(LocalizationError, match="catalog mismatch"):
            validate_translations(entries, bad)
    bad_placeholder = dict(translations)
    bad_placeholder[entries[2].key] = "名字$"
    with pytest.raises(LocalizationError, match="placeholder"):
        validate_translations(entries, bad_placeholder)
    bad_terminator = dict(translations)
    bad_terminator[entries[2].key] = "名字{PLAYER}"
    with pytest.raises(LocalizationError, match="terminator"):
        validate_translations(entries, bad_terminator)


def test_manifest_is_canonical_json(tmp_path):
    entries = scan_source(_source_tree(tmp_path))
    value = manifest(entries)
    assert list(value) == ["format_version", "source"]
    assert json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def test_parse_charmap_and_coverage(tmp_path):
    path = tmp_path / "charmap.txt"
    path.write_text(
        "' ' = 00\n"
        "'!' = AB\n"
        "'你' = 0100\n"
        "'好' = 0101\n"
        "'’' = B4\n"
        "'\\'' = B4\n"
        "PLAYER = FD 01\n",
        encoding="utf-8")
    mapping = parse_charmap(path)
    assert mapping["你"] == 0x0100 and mapping["’"] == 0xB4
    entries = scan_source(_source_tree(tmp_path))
    result = coverage(entries, mapping)
    assert result["mapped_all"] is False
    assert result["missing"] > 0
    assert result["two_byte_characters"] == 2
    assert result["code_min"] == 0x0100 and result["code_max"] == 0x0101
    assert coverage_texts(["你好"], mapping)["mapped_all"]


def test_parse_charmap_rejects_conflicts(tmp_path):
    path = tmp_path / "bad-charmap.txt"
    path.write_text("'你' = 0100\n'你' = 0101\n", encoding="utf-8")
    with pytest.raises(LocalizationError, match="conflicting"):
        parse_charmap(path)


def test_catalog_cli_writes_manifest_and_report(tmp_path):
    source = _source_tree(tmp_path / "source")
    charmap = tmp_path / "charmap.txt"
    charmap.write_text("'你' = 0100\n'好' = 0101\n", encoding="utf-8")
    output = tmp_path / "catalog.json"
    result_path = tmp_path / "report.json"
    result = subprocess.run([
        sys.executable, str(ROOT / "tools/frlg/localization_catalog.py"),
        "--source-dir", str(source), "--output", str(output),
        "--charmap", str(charmap), "--report", str(result_path),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report_value = json.loads(result_path.read_text(encoding="utf-8"))
    assert report_value["report"]["entries"] == 6
    assert report_value["charmap"]["coverage"]["mapped"] == 2
    assert json.loads(output.read_text(encoding="utf-8"))["format_version"] == 1
