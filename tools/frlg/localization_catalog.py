#!/usr/bin/env python3
"""Inventory a complete FireRed/LeafGreen source and check Chinese glyph coverage.

The source checkout and charmap stay outside this repository.  The command
writes a deterministic translation catalog and prints a compact JSON report;
it does not build a ROM or modify a game file.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, canonical, read_json
from pokeldn.frlg.localization.catalog import report as catalog_report
from pokeldn.frlg.localization.catalog import scan_source, write_manifest
from pokeldn.frlg.localization.chinese import coverage, coverage_texts, parse_charmap


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path,
                        help="external pokefirered/PretFRLG source checkout")
    parser.add_argument("--output", required=True, type=Path,
                        help="deterministic JSON catalog to write")
    parser.add_argument("--charmap", type=Path,
                        help="optional source charmap.txt for glyph coverage")
    parser.add_argument("--translations", type=Path,
                        help="optional JSON object keyed by catalog entry")
    parser.add_argument("--report", type=Path,
                        help="optional JSON report path; otherwise print to stdout")
    args = parser.parse_args(argv)
    try:
        entries = scan_source(args.source_dir)
        translations = read_json(args.translations) if args.translations else None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        digest = write_manifest(args.output, entries, translations)
        summary = catalog_report(entries)
        result = {"source_dir": str(args.source_dir.resolve()),
                  "catalog": str(args.output.resolve()),
                  "catalog_sha256": digest, "report": summary}
        if translations is not None:
            result["translations"] = {"entries": len(translations), "validated": True}
        if args.charmap:
            mapping = parse_charmap(args.charmap)
            result["charmap"] = {"path": str(args.charmap.resolve()),
                                  "characters": len(mapping),
                                  "coverage": coverage(entries, mapping)}
            if translations is not None:
                result["charmap"]["translation_coverage"] = coverage_texts(
                    translations.values(), mapping)
        encoded = canonical(result) + b"\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_bytes(encoded)
        else:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (LocalizationError, OSError) as exc:
        parser.exit(2, f"catalog build refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
