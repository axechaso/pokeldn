"""Interoperate with BlueRetro v25.10-beta's actual Nintendo receiver, not a mirrored handshake."""
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from pokeldn.pad import macro


@pytest.fixture(scope="module")
def blue(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/blueretro"
    folder = tmp_path_factory.mktemp("blueretro")
    for name in ("esp_timer.h", "bluetooth/host.h", "bluetooth/hci.h", "bluetooth/mon.h",
                 "tools/util.h", "tests/cmds.h"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "host.h"\n', encoding="utf-8")
    library = folder / "receiver.so"
    subprocess.run([cc, "-shared", "-fPIC", "-O1", "-I", str(folder), "-I", str(fixture),
                    "-I", str(fixture / "upstream"), "-I", str(root / "firmware/pad/main"),
                    str(fixture / "upstream/sw.c"), str(fixture / "host.c"),
                    str(root / "firmware/pad/main/pro_report.c"), "-o", str(library)], check=True)
    so = ctypes.CDLL(str(library))
    so.pro_reply.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_void_p)
    so.pro_reply.restype = ctypes.c_bool
    so.blue_name.restype = ctypes.c_char_p
    return so


def test_blueretro_completes_initialization_calibrates_and_receives_buttons_and_sticks(blue):
    assert blue.blue_name() == b"Pro Controller"
    blue.blue_begin()
    request = ctypes.create_string_buffer(48)
    response = ctypes.create_string_buffer(48)
    commands, reads = [], []
    mac = bytes.fromhex("102030405060")
    while blue.blue_next(request):
        commands.append(request.raw[9])
        if request.raw[9] == 0x10:
            reads.append((int.from_bytes(request.raw[10:14], "little"), request.raw[14]))
        assert blue.pro_reply(request, 48, mac, macro.NEUTRAL, len(commands), response)
        assert response.raw[13] == request.raw[9]
        blue.blue_deliver(0x21, response)
    assert commands == [0x02, 0x10, 0x10, 0x10, 0x10, 0x03, 0x48, 0x30]
    assert reads == [(0x603D, 18), (0x6086, 18), (0x6098, 18), (0x8010, 22)]
    assert blue.blue_ready()
    calibration = (ctypes.c_uint16 * 14)()
    assert blue.blue_calibration(calibration) == 14
    assert list(calibration[:12]) == [0x700, 0x800, 0x700] * 4
    assert list(calibration[12:]) == [0x96, 0x96]

    state = macro.state(["A", "PLUS", "LEFT"], left=(-1, 1), right=(1, -1))
    report = ctypes.create_string_buffer(48)
    blue.pro_state(state, 7, report)
    blue.blue_deliver(0x30, report)
    received = ctypes.create_string_buffer(48)
    assert blue.blue_last_report(received) == 1
    assert received.raw[2:5] == bytes.fromhex("080208")

    def stick(data):
        return data[0] | (data[1] & 15) << 8, data[1] >> 4 | data[2] << 4
    assert stick(received.raw[5:8]) == (0x100, 0xF00)
    assert stick(received.raw[8:11]) == (0xEF2, 0x10E)
    blue.pro_state(macro.NEUTRAL, 8, report)
    blue.blue_deliver(0x30, report)
    assert blue.blue_last_report(received) == 2
    assert received.raw[2:5] == bytes(3)
