"""Constrained builders; the existing Flash/Sloop builders retain their restrictions."""

import struct

from pokeldn.frlg.rom import buffer_script as bs
from . import LocalizationError, PAGE_SIZE, ROM_BASE, digest, integer

CHUNK_SIZE = 512
STAGE_OK = 0x5A480001
COMMIT_OK = 0x5A480002
REFUSED = 0x5A48FFFF
STAGE_HEADER = 20
STAGE_DATA = 84
COMMIT_HEADER = 24


def fnv1a(data):
    value = 0x811C9DC5
    for byte in data:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def header(session_id, patch_id, page_offset, target):
    if not isinstance(session_id, str) or len(session_id) != 32 or any(
            c not in "0123456789abcdef" for c in session_id):
        raise LocalizationError("session_id must be 16 bytes of lowercase hexadecimal")
    digest(patch_id, "patch_id")
    if len(target) != PAGE_SIZE:
        raise LocalizationError("staging requires a complete 4 KiB page")
    return (bytes.fromhex(session_id) + bytes.fromhex(patch_id)
            + struct.pack("<4I", page_offset, 0, 0, fnv1a(target)))


def _profile(profile, offset):
    # Reparse the public model so direct constructors cannot bypass layout checks.
    from .profiles import Profile
    checked = Profile.parse(profile.as_dict())
    checked.check_page(offset)
    return checked


def build_stage(profile, session_id, patch_id, offset, target, *, chunk=None):
    profile = _profile(profile, offset)
    if chunk is not None:
        integer(chunk, "chunk", 0, PAGE_SIZE // CHUNK_SIZE - 1)
    code = bytearray(bs.payload("rom-page-stage"))
    struct.pack_into("<4I", code, 4, int(chunk is not None), profile.stage_address,
                     profile.state_address, chunk or 0)
    code[STAGE_HEADER:STAGE_HEADER + 64] = header(session_id, patch_id, offset, target)
    if chunk is not None:
        at = chunk * CHUNK_SIZE
        code[STAGE_DATA:STAGE_DATA + CHUNK_SIZE] = target[at:at + CHUNK_SIZE]
    return bytes(bs.validate(code))


def build_commit(profile, session_id, patch_id, offset, before, target):
    profile = _profile(profile, offset)
    if len(before) != PAGE_SIZE:
        raise LocalizationError("commit requires a complete original page")
    if len(target) != PAGE_SIZE:
        raise LocalizationError("commit requires a complete target page")
    for i, (old, new) in enumerate(zip(before, target)):
        if old != new:
            profile.check_edit(offset + i, 1)
    code = bytearray(bs.payload("rom-page-commit"))
    struct.pack_into("<5I", code, 4, profile.stage_address, profile.state_address,
                     profile.rom_size, ROM_BASE + offset, fnv1a(before))
    code[COMMIT_HEADER:COMMIT_HEADER + 64] = header(session_id, patch_id, offset, target)
    return bytes(bs.validate(code))
