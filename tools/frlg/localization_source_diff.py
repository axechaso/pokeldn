#!/usr/bin/env python3
"""Align an external English FRLG source with a translated source checkout."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, canonical
from pokeldn.frlg.localization.chinese import (coverage_texts, encode_text,
                                                parse_definitions)
from pokeldn.frlg.localization.source_diff import compare_sources, translation_map


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, type=Path,
                        help="external English pokefirered source checkout")
    parser.add_argument("--translated-dir", required=True, type=Path,
                        help="external translated source checkout")
    parser.add_argument("--output", required=True, type=Path,
                        help="base-keyed JSON translation map")
    parser.add_argument("--charmap", type=Path,
                        help="optional translated source charmap.txt")
    parser.add_argument("--report", type=Path,
                        help="optional comparison report path")
    parser.add_argument("--strict", action="store_true",
                        help="refuse output when keys or controls do not align exactly")
    args = parser.parse_args(argv)
    try:
        comparison = compare_sources(args.base_dir, args.translated_dir)
        translations = translation_map(comparison, strict=args.strict)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(translations) + b"\n")
        result = {key: value for key, value in comparison.items() if key != "translations"}
        result["translation_map"] = str(args.output.resolve())
        result["translation_entries"] = len(translations)
        if args.charmap:
            mapping, macros = parse_definitions(args.charmap)
            result["translation_coverage"] = {
                "charmap_characters": len(mapping),
                **coverage_texts(translations.values(), mapping),
            }
            encoded_lengths = [len(encode_text(text, mapping, macros))
                               for text in translations.values()]
            result["encoding"] = {
                "macros": len(macros), "entries": len(encoded_lengths),
                "bytes": sum(encoded_lengths),
                "max_entry_bytes": max(encoded_lengths, default=0),
            }
        encoded = canonical(result) + b"\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_bytes(encoded)
        else:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (LocalizationError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"source diff refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
