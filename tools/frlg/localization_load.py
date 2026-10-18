#!/usr/bin/env python3
"""Check localization packages and exercise the complete software loading/recovery path.

No CLI command writes to a real Switch or claims a hardware-verified localization.
"""

import argparse
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, read_json
from pokeldn.frlg.localization.examples import synthetic_example
from pokeldn.frlg.localization.patch import Patch
from pokeldn.frlg.localization.profiles import Profile
from pokeldn.frlg.localization.session import Session
from pokeldn.frlg.localization.transport import ModelTransport


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "simulate"):
        command = commands.add_parser(name)
        for flag in ("profile", "base", "patch"):
            command.add_argument("--" + flag, required=True, type=Path)
        if name == "simulate":
            command.add_argument("--journal", required=True, type=Path)
    smoke = commands.add_parser("self-test", help="synthetic ARM load/readback/restore, no game files")
    smoke.add_argument("--output-dir", required=True, type=Path)
    return parser


def exercise(profile, base, patch, journal):
    import unicorn
    transport = ModelTransport(profile, base)
    session = Session(profile, patch, base, transport, journal)
    loaded = session.apply()
    writes = len(transport.machine.rom_writes)
    repeated = session.apply()
    if len(transport.machine.rom_writes) != writes:
        raise LocalizationError("repeat load performed an unexpected write")
    restored = session.restore()
    return {"backend": "synthetic-unicorn-model", "software_only": True,
            "python": platform.python_version(), "unicorn": unicorn.__version__,
            "profile_id": profile.profile_id, "patch_id": patch.patch_id,
            "loaded": loaded, "repeated": repeated, "restored": restored,
            "rom_page_writes": len(transport.machine.rom_writes),
            "ryubing_verified": False, "retail_verified": False, "chinese_rendering_verified": False}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            # Never reuse an old evidence directory or clobber another journal/report.
            args.output_dir.mkdir(parents=True, exist_ok=False)
            profile, base, patch = synthetic_example()
            result = exercise(profile, base, patch, args.output_dir / "recovery.local.json")
            for name, value in (("profile.json", profile.as_dict()), ("patch.json", patch.as_dict()),
                                ("report.json", result)):
                (args.output_dir / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        else:
            profile = Profile.parse(read_json(args.profile))
            patch = Patch.parse(read_json(args.patch))
            base = args.base.read_bytes()
            patch.validate(profile, base)
            if args.command == "check":
                result = {"state": "OFFLINE_VALIDATED", "patch_id": patch.patch_id,
                          "profile_id": profile.profile_id, "pages": len(patch.pages),
                          "retail_verified": False, "write_eligible": False}
            else:
                if args.journal.exists():
                    raise LocalizationError("simulation requires a new journal path")
                result = exercise(profile, base, patch, args.journal)
        print(json.dumps(result, indent=2))
        return 0
    except (LocalizationError, OSError, TypeError, KeyError, ImportError) as exc:
        parser.exit(2, f"localization load refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
