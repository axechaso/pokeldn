"""Research index for the first 64 KiB of BPRE rev10; this never enables a write.

Names and English offsets come from the upstream ELF-derived address map. They remain
deductions until the captured instructions are matched to the decompilation. A window
ends at the next named symbol, not necessarily at the end of a function.
"""
from hashlib import sha256

from pokeldn.frlg.rom import english_names
from . import LocalizationError, ROM_BASE


TEXT_ENGINE_SIZE = 0x10000
FUNCTIONS = (
    "SetFontsPointer", "AddTextPrinterParameterized", "RenderFont", "DecompressGlyphTile",
    "CopyGlyphToWindow", "FontFunc_Normal", "RenderText", "GetStringWidth",
    "DecompressGlyph_Normal", "GetGlyphWidth_Normal", "StringCopy", "StringCopyN",
    "StringLength", "StringExpandPlaceholders", "StringCopyN_Multibyte",
    "StringLength_Multibyte", "GetExtCtrlCodeLength",
)


def inspect_prefix(data):
    if (len(data) != TEXT_ENGINE_SIZE or data[0xAC:0xB0] != b"BPRE"
            or data[0xBC] != 0x0A):
        raise LocalizationError("text-engine research needs a 64 KiB BPRE 0x0A prefix")
    symbols = []
    for french, name in english_names.NAMES.items():
        offsets = [delta for low, high, delta, _ in english_names.OFFSETS
                   if low <= french <= high]
        if len(offsets) == 1:
            address = french + offsets[0]
            if ROM_BASE <= address < ROM_BASE + len(data):
                symbols.append((address, name))
    symbols.sort()
    windows = []
    for index, (address, name) in enumerate(symbols[:-1]):
        if name not in FUNCTIONS:
            continue
        offset, end = address - ROM_BASE, symbols[index + 1][0] - ROM_BASE
        windows.append({"name": name, "address": f"0x{address:08x}", "offset": offset,
                        "window_size": end - offset,
                        "sha256": sha256(data[offset:end]).hexdigest()})
    if {window["name"] for window in windows} != set(FUNCTIONS):
        raise LocalizationError("upstream text-engine symbol index is incomplete")
    return {"purpose": "text-engine-research", "address": f"0x{ROM_BASE:08x}",
            "size": len(data), "address_evidence": "pokeldn/frlg/rom/english_names.py: NAMES + OFFSETS",
            "symbol_addresses_verified": False, "game_encoding_assigned": False,
            "write_eligible": False, "windows": windows}
