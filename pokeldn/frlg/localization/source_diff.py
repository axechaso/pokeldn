"""Align two complete FRLG source catalogs without modifying either checkout."""

from collections import Counter
import re

from . import LocalizationError
from .catalog import report, scan_source


_PLACEHOLDER = re.compile(r"\{[^{}]+\}")


def compare_sources(base_root, translated_root):
    """Return deterministic source alignment and translation-map diagnostics.

    Matching is by the catalog key (relative path plus symbol/line identity).
    Extra translated entries are retained in the report because a fork may
    carry additional language data. Missing base keys are never silently
    dropped; callers may use strict=True in translation_map to reject them.
    """
    base_entries = scan_source(base_root)
    translated_entries = scan_source(translated_root)
    base = {entry.key: entry for entry in base_entries}
    translated = {entry.key: entry for entry in translated_entries}
    common = sorted(base.keys() & translated.keys())
    missing = sorted(base.keys() - translated.keys())
    extra = sorted(translated.keys() - base.keys())
    changed = sorted(key for key in common if base[key].text != translated[key].text)
    placeholder_mismatches = sorted(
        key for key in common
        if Counter(_PLACEHOLDER.findall(base[key].text))
        != Counter(_PLACEHOLDER.findall(translated[key].text)))
    terminator_mismatches = sorted(
        key for key in common
        if base[key].text.endswith("$") != translated[key].text.endswith("$"))
    empty_translations = sorted(key for key in common if not translated[key].text)
    return {
        "format_version": 1,
        "base": {"entries": len(base_entries), "report": report(base_entries)},
        "translated": {"entries": len(translated_entries), "report": report(translated_entries)},
        "common_entries": len(common),
        "changed_entries": len(changed),
        "unchanged_entries": len(common) - len(changed),
        "missing_base_keys": missing,
        "extra_translated_keys": extra,
        "placeholder_mismatches": placeholder_mismatches,
        "terminator_mismatches": terminator_mismatches,
        "empty_translations": empty_translations,
        "translation_compatible": not (
            missing or placeholder_mismatches or terminator_mismatches),
        "translations": {key: translated[key].text for key in common},
    }


def translation_map(comparison, *, strict=False):
    """Extract the base-keyed translation object from a source comparison."""
    required = ("translations", "missing_base_keys", "placeholder_mismatches",
                "terminator_mismatches")
    if not all(name in comparison for name in required):
        raise LocalizationError("invalid source comparison")
    if strict and not comparison["translation_compatible"]:
        raise LocalizationError(
            "source translations have missing keys or incompatible controls")
    return dict(comparison["translations"])
