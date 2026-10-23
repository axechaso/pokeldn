"""Coverage helpers for a two-byte Chinese FRLG source charmap.

The Chinese source build keeps the original one-byte international map and
adds two-byte entries in the ``0x0100``..``0x1E6A`` range.  This module only
reads that source definition and checks a text catalog against it; it does
not copy a font or emit a ROM patch.
"""

import ast
from collections import Counter
from pathlib import Path
import re

from . import LocalizationError
from .catalog import iter_glyphs


_ENTRY = re.compile(r"^\s*('(?:\\.|[^'\\])*')\s*=\s*([0-9A-Fa-f]{2,4})\b")
_DEFINITION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|(?:0x)?[0-9A-Fa-f]+$")
_HEX_TOKEN = re.compile(r"(?:0x)?[0-9A-Fa-f]+$")


def _parse_definitions(path):
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LocalizationError(f"cannot read charmap {path}: {exc}") from exc

    mapping = {}
    raw_macros = {}
    for line_number, original in enumerate(lines, 1):
        match = _ENTRY.match(original)
        if match:
            raw_character = match.group(1)[1:-1]
            if raw_character in (r"\n", r"\l", r"\p"):
                continue
            try:
                character = ast.literal_eval(match.group(1))
            except (SyntaxError, ValueError) as exc:
                raise LocalizationError(
                    f"invalid charmap character at {path}:{line_number}") from exc
            if not isinstance(character, str) or len(character) != 1:
                continue
            code = int(match.group(2), 16)
            if code > 0xFFFF:
                raise LocalizationError(
                    f"charmap code exceeds two bytes at {path}:{line_number}")
            previous = mapping.get(character)
            if previous is not None and previous != code:
                raise LocalizationError(
                    f"conflicting charmap code for {character!r} at {path}:{line_number}")
            mapping[character] = code
            continue

        definition = _DEFINITION.match(original.split("@", 1)[0])
        if not definition:
            continue
        name, rhs = definition.groups()
        tokens = tuple(rhs.split())
        if not tokens:
            continue
        if any(not _TOKEN.fullmatch(token) for token in tokens):
            raise LocalizationError(f"invalid charmap macro at {path}:{line_number}")
        raw_macros[name] = tokens

    if not mapping:
        raise LocalizationError(f"charmap contains no quoted character entries: {path}")

    resolved = {}

    def resolve(name, stack=()):
        if name in resolved:
            return resolved[name]
        if name in stack:
            raise LocalizationError(f"cyclic charmap macro: {' -> '.join(stack + (name,))}")
        if name not in raw_macros:
            raise LocalizationError(f"unknown charmap macro: {name}")
        value = bytearray()
        for token in raw_macros[name]:
            if _HEX_TOKEN.fullmatch(token):
                code = int(token, 0 if token.startswith("0x") else 16)
                if code > 0xFF:
                    raise LocalizationError(f"macro byte exceeds one byte: {name}={token}")
                value.append(code)
            else:
                value.extend(resolve(token, stack + (name,)))
        resolved[name] = bytes(value)
        return resolved[name]

    for name in raw_macros:
        resolve(name)
    return mapping, resolved


def parse_definitions(path):
    """Return a character map and resolved macro map from a charmap file."""
    return _parse_definitions(path)


def parse_charmap(path):
    """Return only the character map, omitting resolved control macros."""
    mapping, _macros = _parse_definitions(path)
    return mapping


def encode_text(text, mapping, macros):
    """Encode glyphs, FRLG controls and brace macros into game bytes."""
    out = bytearray()
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", text):
        _encode_glyphs(text[cursor:match.start()], mapping, out)
        expression = match.group(0)[1:-1].split()
        if not expression or expression[0] not in macros:
            raise LocalizationError(f"unknown text macro: {match.group(0)}")
        out.extend(macros[expression[0]])
        for argument in expression[1:]:
            if argument in macros:
                out.extend(macros[argument])
            else:
                try:
                    if not _HEX_TOKEN.fullmatch(argument):
                        raise ValueError(argument)
                    value = int(argument, 0 if argument.startswith("0x") else 10)
                except ValueError as exc:
                    raise LocalizationError(
                        f"unknown text macro argument: {argument}") from exc
                if not 0 <= value <= 0xFF:
                    raise LocalizationError(
                        f"text macro argument exceeds one byte: {argument}")
                out.append(value)
        cursor = match.end()
    _encode_glyphs(text[cursor:], mapping, out)
    return bytes(out)


def _encode_glyphs(text, mapping, out):
    for character in text:
        if character == "\n":
            out.append(0xFE)
        elif character == "\x0b":
            out.append(0xFA)
        elif character == "\x0c":
            out.append(0xFB)
        elif character == "$":
            out.append(0xFF)
        else:
            if character not in mapping:
                raise LocalizationError(
                    f"character is absent from charmap: {character!r}")
            code = mapping[character]
            out.extend(code.to_bytes(2 if code > 0xFF else 1, "big"))


def _coverage(glyphs, mapping):
    missing = sorted(char for char in glyphs if char not in mapping)
    mapped_codes = [mapping[char] for char in glyphs if char in mapping]
    two_byte = sorted(char for char in glyphs if char in mapping and mapping[char] > 0xFF)
    return {
        "mapped": len(glyphs) - len(missing),
        "missing": len(missing),
        "mapped_all": not missing,
        "missing_glyphs": "".join(missing),
        "two_byte_characters": len(two_byte),
        "two_byte_glyphs": "".join(two_byte),
        "code_min": min(mapped_codes) if mapped_codes else None,
        "code_max": max(mapped_codes) if mapped_codes else None,
    }


def coverage(entries, mapping):
    """Return glyph coverage and byte-range statistics for a text catalog."""
    entries = tuple(entries)
    glyphs = Counter(char for entry in entries for char in iter_glyphs(entry.text))
    return _coverage(glyphs, mapping)


def coverage_texts(texts, mapping):
    """Check translated strings directly, without manufacturing catalog entries."""
    glyphs = Counter(char for text in texts for char in iter_glyphs(text))
    return _coverage(glyphs, mapping)
