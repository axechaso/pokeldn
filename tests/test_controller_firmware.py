"""Build identity, installation boundaries and the classic controller's serial protocol."""
import asyncio
import struct
from collections import deque

import pytest

from pokeldn import firmware
from pokeldn.pad import link, macro, serial_link, service


def image(tmp_path):
    data = bytearray(0x10200)
    for offset in (0x1000, 0x10000):
        data[offset] = 0xE9
    struct.pack_into("<HBBII", data, 0x8000, 0x50AA, 0, 0, 0x10000, 0x100000)
    desc = 0x10000 + 32
    struct.pack_into("<I", data, desc, 0xABCD5432)
    for offset, text in ((16, firmware.VERSION), (48, firmware.PROJECT), (112, "v6.1")):
        data[desc + offset:desc + offset + len(text)] = text.encode()
    path = tmp_path / "pokeldn-pad.bin"
    path.write_bytes(data)
    firmware.write_manifest(path, "test-commit")
    return path


def test_unsupported_controller_chip_is_rejected_before_opening_an_image(tmp_path):
    for chip in ("esp32s3", "esp32c3", "esp32c6"):
        with pytest.raises(ValueError, match="requires a classic ESP32"):
            firmware.controller_image(chip)
        with pytest.raises(ValueError, match="requires a classic ESP32"):
            firmware.flash_args("COM9", chip, tmp_path / "absent.bin")


def test_installation_checks_build_identity_and_hash(tmp_path):
    path = image(tmp_path)
    info = firmware.verify_image(path)
    assert info["chip"] == "esp32" and info["source_commit"] == "test-commit"
    args = firmware.flash_args("COM9", "esp32", path)
    assert args[args.index("--chip") + 1] == "esp32"
    assert args[-2] == "0x0" and "--force" not in args
    assert firmware.flash_args("COM9", "esp32", path, True)[7] == "no-reset"
    changed = bytearray(path.read_bytes())
    changed[-1] ^= 1
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="SHA-256"):
        firmware.flash_args("COM9", "esp32", path)


@pytest.mark.parametrize("offset,value", [(0x1000 + 12, 9), (0x10000 + 12, 9), (0x8000, 0),
                                           (0x10000 + 32 + 48, ord("X"))])
def test_another_chip_or_project_cannot_be_packaged_as_a_classic_controller(tmp_path, offset, value):
    path = image(tmp_path)
    changed = bytearray(path.read_bytes())
    changed[offset] = value
    path.write_bytes(changed)
    with pytest.raises(ValueError):
        firmware.write_manifest(path)


def test_serial_selection_never_probes_a_wireless_radio(monkeypatch):
    ports = []

    async def connect(port):
        ports.append(port)
        return object()
    monkeypatch.setattr(service.SerialPad, "connect", connect)
    with pytest.raises(ConnectionError, match="choose"):
        asyncio.run(service.connect_any())
    assert not ports
    asyncio.run(service.connect_any("COM9"))
    assert ports == ["COM9"]


def test_classic_controller_round_trip_without_platform_specific_pty(monkeypatch):
    """Exercise the real serial client against the firmware's frames on Windows too."""
    import serial
    reports, commands = [], []
    status = struct.pack("<BIBIHH", 1, 12, 0, 2, 1, 4) + b"1.3.0\0" + struct.pack("<BBH", 1, 7, 2)

    class Wire:
        is_open = False

        def __init__(self):
            self.pending = deque()
            self.reader = serial_link.Reader()

        def open(self):
            self.is_open = True

        def reset_input_buffer(self):
            self.pending.clear()

        def write(self, data):
            for kind, body in self.reader.feed(data):
                if kind == serial_link.STATUS:
                    reply = serial_link.frame(serial_link.STATUS_REPLY, status)
                else:
                    (reports if kind == serial_link.REPORT else commands).append(body)
                    reply = serial_link.frame(serial_link.RESULT, b"\0")
                # Boot noise and fragmentation must not hide a valid reply.
                self.pending.extend([b"boot\r\n" + reply[:3], reply[3:7], reply[7:]])

        def read(self, size):
            return self.pending.popleft() if self.pending else b""

        def close(self):
            self.is_open = False

    monkeypatch.setattr(serial, "Serial", Wire)

    async def exercise():
        pad = await serial_link.SerialPad.connect("COM9")
        program = macro.compile_macro(macro.Macro(loop=[{"press": "A"}]))
        await pad.send(macro.state(["B"]))
        await pad.load(program)
        await pad.play()
        state = await pad.status()
        await pad.stop()
        await pad.pair()
        await pad.close()
        assert not pad.connected
        return state, program

    state, program = asyncio.run(exercise())
    assert state.mounted and state.version == "1.3.0" and state.boots == 2
    assert reports == [macro.state(["B"]), macro.NEUTRAL]
    assert commands == [*link.program_writes(program), bytes([link.PLAY]), bytes([link.STOP]), bytes([link.PAIR])]
