"""Extract and validate the complete FireRed/LeafGreen text source.

The runtime demo only knows one message.  A full translation has to cover the
``.string``/``.braille`` records in ``data`` as well as the ``_()`` literals in
the C sources.  This module keeps that inventory lossless and deliberately
does not assign game bytes: the source build's charmap and renderer remain the
authority for the final ROM.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re

from pokeldn.frlg.text import charmap
from . import LocalizationError, canonical, sha256


_LABEL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)::\s*$")
_ASM_LITERAL = re.compile(r'^\s*\.(string|braille)\s+"((?:\\.|[^"\\])*)"\s*$')
_C_CALL = re.compile(
    r'(?<![A-Za-z0-9_])(?:__|_)\(\s*((?:"(?:\\.|[^"\\])*"\s*)+)\)',
    re.MULTILINE)
_C_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')
_C_SYMBOL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=\s*(?:__|_)")
_PLACEHOLDER = re.compile(r"\{[^{}]+\}")

_ROOTS = ("data", "src", "include")
_SUFFIXES = frozenset((".c", ".h", ".inc", ".s"))
_ESCAPES = {"n": "\n", "l": "\x0b", "p": "\x0c", "\\": "\\", '"': '"'}


@dataclass(frozen=True)
class TextEntry:
    """One source literal, identified independently of its translated text."""

    key: str
    path: str
    line: int
    symbol: str | None
    kind: str
    text: str

    def as_dict(self):
        return {"key": self.key, "path": self.path, "line": self.line,
                "symbol": self.symbol, "kind": self.kind, "text": self.text}


def decode_literal(body):
    """Decode assembler/C escapes while preserving the three FRLG controls.

    ``\\n`` is a native newline, ``\\l`` is the scroll control and ``\\p`` is
    the clear/page control.  They are represented by newline/vertical/form-feed
    characters in the inventory so JSON and layout tools can handle them
    without pretending that they are ordinary glyphs.
    """
    out, pos = [], 0
    while pos < len(body):
        char = body[pos]
        if char != "\\" or pos + 1 >= len(body):
            out.append(char)
            pos += 1
            continue
        escaped = body[pos + 1]
        if escaped == "x" and pos + 3 < len(body):
            digits = body[pos + 2:pos + 4]
            if all(c in "0123456789abcdefABCDEF" for c in digits):
                out.append(chr(int(digits, 16)))
                pos += 4
                continue
        out.append(_ESCAPES.get(escaped, "\\" + escaped))
        pos += 2
    return "".join(out)


def _key(path, symbol, line, ordinal):
    if symbol:
        return f"{path}::{symbol}"
    return f"{path}:{line}:{ordinal}"


def _finish_asm(entries, path, symbol, start_line, parts, ordinal):
    if symbol is None or not parts:
        return ordinal
    ordinal += 1
    entries.append(TextEntry(_key(path, symbol, start_line, ordinal), path, start_line,
                             symbol, "asm", "".join(parts)))
    return ordinal


def _scan_c_literals(source_text, path):
    """Return C string calls, including adjacent literals split across lines."""
    for match in _C_CALL.finditer(source_text):
        body = "".join(decode_literal(part.group(1))
                       for part in _C_STRING.finditer(match.group(1)))
        line = source_text.count("\n", 0, match.start()) + 1
        line_start = source_text.rfind("\n", 0, match.start()) + 1
        declaration = _C_SYMBOL.search(source_text[line_start:match.end()])
        yield line, declaration.group(1) if declaration else None, body


def scan_source(root, *, roots=_ROOTS):
    """Return every text literal under a decomp checkout in source order.

    The scanner intentionally understands only the textual forms used by the
    decompilation.  Unknown data directives are ignored rather than guessed,
    so a future source change cannot silently become a corrupt translation.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise LocalizationError(f"FRLG source directory does not exist: {root}")
    paths = []
    for name in roots:
        directory = root / name
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*")
                         if path.is_file() and path.suffix.lower() in _SUFFIXES)
    entries, seen, key_counts = [], Counter(), Counter()
    for source in sorted(paths):
        relative = source.relative_to(root).as_posix()
        try:
            source_text = source.read_text(encoding="utf-8")
            lines = source_text.splitlines()
        except (OSError, UnicodeError) as exc:
            raise LocalizationError(f"cannot read FRLG source text {source}: {exc}") from exc
        file_entries = []
        symbol, parts, start_line, asm_ordinal = None, [], 0, 0
        for line_number, line in enumerate(lines, 1):
            label = _LABEL.match(line)
            if label:
                asm_ordinal = _finish_asm(file_entries, relative, symbol, start_line, parts,
                                          asm_ordinal)
                symbol, parts, start_line = label.group(1), [], line_number
                continue
            literal = _ASM_LITERAL.match(line)
            if literal and symbol:
                parts.append(decode_literal(literal.group(2)))
                continue
            if symbol and parts and line.strip() and not line.lstrip().startswith("@"):
                asm_ordinal = _finish_asm(file_entries, relative, symbol, start_line, parts,
                                          asm_ordinal)
                symbol, parts, start_line = None, [], 0

        asm_ordinal = _finish_asm(file_entries, relative, symbol, start_line, parts,
                                  asm_ordinal)

        # C literals are scanned over the complete file so adjacent strings
        # split across lines remain one translation entry.  The line number
        # and occurrence make anonymous/local strings deterministic, while a
        # declaration name makes the manifest pleasant to translate.
        for line_number, c_symbol, text in _scan_c_literals(source_text, relative):
            seen_key = (relative, c_symbol or "", line_number)
            seen[seen_key] += 1
            base_key = _key(relative, c_symbol, line_number, seen[seen_key])
            key_counts[base_key] += 1
            suffix = "" if key_counts[base_key] == 1 else f"#{key_counts[base_key]}"
            key = base_key + suffix
            file_entries.append(TextEntry(key, relative, line_number, c_symbol, "c", text))

        file_entries.sort(key=lambda entry: (entry.line, entry.kind))
        entries.extend(file_entries)
    return tuple(entries)


def iter_glyphs(text):
    """Yield display characters while omitting FRLG controls and placeholders."""
    without_controls = _PLACEHOLDER.sub("", text).replace("\n", "").replace("\x0b", "")
    without_controls = without_controls.replace("\x0c", "").replace("$", "")
    return without_controls


# Kept as a private alias for callers written against the first draft of this
# module.  New code should use ``iter_glyphs`` so the inventory operation is
# discoverable and testable.
_iter_glyphs = iter_glyphs


def report(entries):
    """Summarize source coverage and the glyphs that need a Chinese bank."""
    entries = tuple(entries)
    glyphs = Counter(char for entry in entries for char in _iter_glyphs(entry.text))
    placeholders = Counter(token for entry in entries for token in _PLACEHOLDER.findall(entry.text))
    controls = Counter({"newline": sum(entry.text.count("\n") for entry in entries),
                        "scroll": sum(entry.text.count("\x0b") for entry in entries),
                        "clear": sum(entry.text.count("\x0c") for entry in entries)})
    native = sorted(char for char in glyphs if char in charmap._ENC)
    extra = sorted(char for char in glyphs if char not in charmap._ENC)
    return {"format_version": 1, "entries": len(entries),
            "files": len({entry.path for entry in entries}),
            "source_characters": sum(glyphs.values()),
            "unique_characters": len(glyphs),
            "native_characters": len(native), "extra_characters": len(extra),
            "extra_glyphs": "".join(extra), "controls": dict(controls),
            "placeholders": dict(sorted(placeholders.items()))}


def validate_translations(entries, translations):
    """Validate a complete translation map without mutating source files."""
    entries = tuple(entries)
    if not isinstance(translations, dict):
        raise LocalizationError("translations must be a JSON object")
    expected = {entry.key for entry in entries}
    actual = set(translations)
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {len(missing)} entries")
        if extra:
            detail.append(f"unknown {len(extra)} entries")
        raise LocalizationError("translation catalog mismatch: " + ", ".join(detail))
    for entry in entries:
        translated = translations[entry.key]
        if not isinstance(translated, str) or not translated:
            raise LocalizationError(f"empty translation: {entry.key}")
        source_tokens = Counter(_PLACEHOLDER.findall(entry.text))
        target_tokens = Counter(_PLACEHOLDER.findall(translated))
        if source_tokens != target_tokens:
            raise LocalizationError(f"control/placeholder mismatch: {entry.key}")
        if entry.text.endswith("$") != translated.endswith("$"):
            raise LocalizationError(f"terminator mismatch: {entry.key}")
    return True


def manifest(entries, translations=None):
    """Build a deterministic JSON-ready catalog for translators or CI."""
    entries = tuple(entries)
    if translations is not None:
        validate_translations(entries, translations)
    return {"format_version": 1, "source": [
        {**entry.as_dict(), **({"translation": translations[entry.key]}
                               if translations is not None else {})}
        for entry in entries]}


def write_manifest(path, entries, translations=None):
    path = Path(path)
    path.write_bytes(canonical(manifest(entries, translations)) + b"\n")
    return sha256(path.read_bytes())
