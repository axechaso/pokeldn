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


def parse_charmap(path):
    """Parse quoted single-character entries from a decomp ``charmap.txt``.

    Macro definitions such as ``PLAYER = FD 01`` are intentionally ignored;
    text controls are represented by the catalog's placeholders and are not
    glyphs.  Duplicate aliases are accepted when they resolve to the same
    character/code pair, while conflicting definitions are rejected.
    """
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LocalizationError(f"cannot read charmap {path}: {exc}") from exc

    mapping = {}
    entries = 0
    for line_number, line in enumerate(lines, 1):
        match = _ENTRY.match(line)
        if not match:
            continue
        raw_character = match.group(1)[1:-1]
        if raw_character in (r"\n", r"\l", r"\p"):
            continue
        try:
            character = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError) as exc:
            raise LocalizationError(f"invalid charmap character at {path}:{line_number}") from exc
        # ``'\\n'``, ``'\\l'`` and ``'\\p'`` are quoted control aliases in
        # the upstream file.  They are handled by the catalog controls, so
        # they do not belong in the glyph map.
        if not isinstance(character, str) or len(character) != 1:
            continue
        code = int(match.group(2), 16)
        if code > 0xFFFF:
            raise LocalizationError(f"charmap code exceeds two bytes at {path}:{line_number}")
        previous = mapping.get(character)
        if previous is not None and previous != code:
            raise LocalizationError(f"conflicting charmap code for {character!r} at {path}:{line_number}")
        mapping[character] = code
        entries += 1
    if not mapping:
        raise LocalizationError(f"charmap contains no quoted character entries: {path}")
    return mapping


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
