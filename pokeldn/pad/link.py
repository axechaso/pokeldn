"""Shared status and macro commands for the classic ESP32 controller."""
import struct
from dataclasses import dataclass

from pokeldn.pad.macro import Program, encode_entry

LOAD, DATA, PLAY, STOP, RESTART = 0x10, 0x11, 0x12, 0x13, 0xB0
CHUNK = 18      # keep the upstream program chunk size for serial protocol compatibility


@dataclass(frozen=True)
class Status:
    mounted: bool           # the Switch has assigned the Bluetooth controller player lights
    writes: int
    playing: bool = False
    loops_done: int = 0
    index: int = 0
    count: int = 0
    version: str = ""
    reset_reason: int = -1      # esp_reset_reason() of this boot
    stage_before: int = -1      # how far the previous boot got (firmware/pad/main/bt_esp32.c)
    boots: int = 0              # boots since power-on


def parse_status(raw: bytes) -> Status:
    mounted, writes = struct.unpack_from("<BI", raw)
    if len(raw) < 14:
        return Status(bool(mounted), writes)
    playing, done, index, count = struct.unpack_from("<BIHH", raw, 5)
    version, _, extra = raw[14:].partition(b"\0")
    reason, stage, boots = struct.unpack("<BBH", extra) if len(extra) == 4 else (-1, -1, 0)
    return Status(bool(mounted), writes, bool(playing), done, index, count,
                  version.decode(errors="replace"), reason, stage, boots)


def program_writes(program: Program) -> list[bytes]:
    """The CONTROL writes that load a program: LOAD, then DATA chunks."""
    out = [struct.pack("<BHHI", LOAD, len(program.entries), program.loop_start, program.loops)]
    for first in range(0, len(program.entries), CHUNK):
        body = b"".join(encode_entry(r, ms) for r, ms in program.entries[first:first + CHUNK])
        out.append(struct.pack("<BH", DATA, first) + body)
    return out
