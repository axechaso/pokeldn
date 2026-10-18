#!/usr/bin/env python3
"""Build and validate ROM-free localization differential packages offline."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, read_json, sha256
from pokeldn.frlg.localization.patch import Edit, build_patch
from pokeldn.frlg.localization.profiles import Profile


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fingerprint = commands.add_parser("fingerprint", help="read a local base ROM, without writing")
    fingerprint.add_argument("rom", type=Path)
    build = commands.add_parser("build", help="build a package from an evidenced profile and exact edits")
    for flag in ("profile", "base", "edits", "output"):
        build.add_argument("--" + flag, type=Path, required=True)
    build.add_argument("--resources", type=Path)
    build.add_argument("--compatibility", type=Path)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fingerprint":
            rom = args.rom.read_bytes()
            if len(rom) < 0xC0:
                raise LocalizationError("not a complete ROM header")
            result = {"game_code": rom[0xAC:0xB0].decode("ascii", "replace"),
                      "revision": rom[0xBC], "rom_size": len(rom), "base_sha256": sha256(rom),
                      "wrapper_identity_verified": False, "write_eligible": False}
        else:
            profile = Profile.parse(read_json(args.profile))
            patch = build_patch(profile, args.base.read_bytes(),
                                [Edit.parse(x) for x in read_json(args.edits)],
                                resources=read_json(args.resources) if args.resources else None,
                                compatibility=read_json(args.compatibility) if args.compatibility else None)
            # Exclusive output: a mistyped path must not overwrite the base ROM or recovery file.
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(patch.as_dict(), handle, ensure_ascii=True, indent=2)
                handle.write("\n")
            result = {"patch_id": patch.patch_id, "pages": len(patch.pages),
                      "changed_bytes": sum(len(e.after) for p in patch.pages for e in p.edits),
                      "output": str(args.output), "retail_verified": False}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (LocalizationError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"localization build refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
