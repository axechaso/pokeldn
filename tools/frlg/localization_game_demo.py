#!/usr/bin/env python3
"""Build an offline Chinese Oak-intro experiment from a verified Switch rev10 decomp.

The resulting GBA image is for local emulator research. It cannot be installed
through PokeLDN as a retail runtime patch. No ROM or external tool is downloaded.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeldn.frlg.localization import LocalizationError, read_json
from pokeldn.frlg.localization.dialogue import ASSETS
from pokeldn.frlg.localization.font import BitmapFont
from pokeldn.frlg.localization.game_demo import BASELINE_SHA1, prepare_sources, temporary_sources


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--make", default="make")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        source = args.source_dir.resolve()
        baseline = source / "pokefirered_switch.gba"
        if hashlib.sha1(baseline.read_bytes()).hexdigest() != BASELINE_SHA1:
            raise LocalizationError("original Switch rev10 baseline SHA1 differs")
        if not 1 <= args.jobs <= 32:
            raise LocalizationError("build jobs must be 1..32")
        font = BitmapFont.parse(read_json(ASSETS / "demo-font.json"))
        replacements, report = prepare_sources(source, font)
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
        (output / "zh_demo_generated.h").write_bytes(replacements["include/zh_demo_generated.h"])
        shutil.copyfile(ASSETS / "OFL.txt", output / "OFL.txt")
        command = [args.make, f"-j{args.jobs}", "GAME_VERSION=FIRERED", "GAME_REVISION=10",
                   "COMPARE=0", "BUILD_NAME=firered_switch_zh_demo"]
        if os.name == "nt":
            bash = shutil.which("bash")
            if not bash:
                raise LocalizationError("the Windows decomp build needs bash on PATH")
            command.extend(["SHELL=" + Path(bash).as_posix(), ".SHELLFLAGS=-o pipefail -c"])
            # Native Windows process argument limits can truncate the full link command.
            database = subprocess.run(command + ["-pn", "SETUP_PREREQS=0", "NODEP=1"], cwd=source,
                                      capture_output=True, text=True, check=True).stdout
            line = next((x for x in database.splitlines() if x.startswith("OBJS_REL := ")), None)
            if line is None:
                raise LocalizationError("decomp did not expose its ordered link objects")
            response = source / "build/firered_switch_zh_demo/localization-link.rsp"
            response.parent.mkdir(parents=True, exist_ok=True)
            response.write_text("\n".join(line.split(":=", 1)[1].split()) + "\n", encoding="utf-8")
            command.append("OBJS_REL=@localization-link.rsp")
        with temporary_sources(source, replacements):
            with (output / "build.log").open("w", encoding="utf-8") as log:
                subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
        for suffix in ("gba", "elf", "map"):
            shutil.copyfile(source / f"pokefirered_switch_zh_demo.{suffix}", output / f"demo.{suffix}")
        rom = (output / "demo.gba").read_bytes()
        if len(rom) != 16 * 1024 * 1024 or rom[0xAC:0xB0] != b"BPRE" or rom[0xBC] != 10:
            raise LocalizationError("unexpected built ROM identity")
        report.update({"baseline_sha1": BASELINE_SHA1, "demo_sha256": hashlib.sha256(rom).hexdigest(),
                       "rom_bytes": len(rom), "build_command": command, "emulator_verified": False})
        (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        print(json.dumps({"output": str(output), "glyphs": len(report["glyph_selectors"]),
                          "retail_write_eligible": False, "emulator_verified": False}, indent=2))
        return 0
    except (LocalizationError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"game demo build refused: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
