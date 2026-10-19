#!/usr/bin/env python3
"""Regenerate the small NPC demo's guards from the verified, local source-built ROM.

The ROM is never copied into this repository. Addresses below are from the ELF
of the pinned compare_firered_switch source build; full function spans are hashed.
"""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pokeldn.frlg.localization import canonical  # noqa: E402
from pokeldn.frlg.localization.game_demo import BASELINE_SHA1, SOURCE_COMMIT  # noqa: E402

SPANS = (
    ("VBlankIntr", 0x08000734, 0xA4),
    ("CopyGlyphToWindow", 0x08002F94, 0x600),
    ("FontFunc_Normal_to_Male", 0x08008D80, 0xA6),
    ("RenderText", 0x08009128, 0x5E0),
    ("RenderFont", 0x08002DFC, 0x2C),
    ("RunTextPrinters", 0x08002D68, 0x7C),
    ("gFontInfos", 0x0841CF08, 0x60),
)


def fnv(data):
    result = 0x811C9DC5
    for byte in data:
        result = ((result ^ byte) * 0x1000193) & 0xFFFFFFFF
    return result


def profile(rom):
    if hashlib.sha1(rom).hexdigest() != BASELINE_SHA1:
        raise ValueError("ROM does not match the pinned original FireRed Switch rev10 build")

    def read(address, size):
        return rom[address - 0x08000000:address - 0x08000000 + size]

    text_address = 0x08180F15
    tail = rom[text_address - 0x08000000:]
    message = tail[:tail.index(0xFF) + 1]
    guards = [{"name": name, "address": addr, "size": size, "fnv": fnv(read(addr, size))}
              for name, addr, size in SPANS]
    guards.append({"name": "PalletTown_Text_CanStoreItemsAndMonsInPC", "address": text_address,
                   "size": len(message), "fnv": fnv(message)})
    return {"source_commit": SOURCE_COMMIT, "baseline_sha1": BASELINE_SHA1,
            "baseline_sha256": hashlib.sha256(rom).hexdigest(), "guards": guards,
            "font_table": read(0x0841CF08, 96).hex(),
            "text_size": len(message), "text_fnv": fnv(message),
            "tail_offset": message.index(0xFB) + 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    args = parser.parse_args()
    data = profile(args.rom.read_bytes())
    path = ROOT / "pokeldn/frlg/localization/assets/ns-story-profile.json"
    path.write_bytes(canonical(data) + b"\n")
    print(path)


if __name__ == "__main__":
    main()
