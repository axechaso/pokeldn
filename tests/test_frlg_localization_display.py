"""Exercise the uploaded ARM instructions and the desktop's NS-only launch path."""
import hashlib
import json
from pathlib import Path
import struct

import pytest

from pokeldn.app import localization
from pokeldn.app.catalog import FRLG, RSE
from pokeldn.app.command import build
from pokeldn.app.settings import Settings
from pokeldn.frlg.localization import LocalizationError
from pokeldn.frlg.localization import cli
from pokeldn.frlg.localization.ns_display import (
    BITMAP_OFFSET, COPY_WINDOW_FNV, DISPLAY_DONE, DISPLAY_FRAMES,
    LocalizationDisplayOperation, build_display_payload,
)
from pokeldn.frlg.gift import mg_script
from test_frlg_localization_gift import drive
from test_mystery_gift_flow import ConsoleClientModel


def fnv(data):
    result = 0x811C9DC5
    for byte in data:
        result = ((result ^ byte) * 0x1000193) & 0xFFFFFFFF
    return result


class ScreenMachine:
    """A synthetic window/ROM-call stub; never described as NS or full GBA validation."""
    def __init__(self, *, bad_header=False, bad_code=False, bad_window=False, destination=0x02004000):
        unicorn = pytest.importorskip("unicorn")
        from unicorn import arm_const as arm
        self.arm, self.uc = arm, unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        self.uc.mem_map(0x02000000, 0x40000)
        self.uc.mem_map(0x03000000, 0x8000)
        self.uc.mem_map(0x08000000, 0x10000)
        stub = bytes.fromhex("7047") + bytes(62)  # Thumb bx lr; native call arguments are recorded.
        self.uc.mem_write(0x08003EA0, stub)
        self.uc.mem_write(0x080000AC, b"BPRE" if not bad_header else b"BPGE")
        self.uc.mem_write(0x080000BC, b"\x0a")
        self.uc.mem_write(0x020204C0, bytes.fromhex("00010f1c040f4f00") + struct.pack("<I", destination))
        if bad_window:
            self.uc.mem_write(0x020204C3, b"\x1b")
        self.code = bytearray(build_display_payload())
        struct.pack_into("<I", self.code, 8, fnv(stub) ^ int(bad_code))
        self.uc.mem_write(0x0201C000, bytes(self.code))
        self.writes, self.calls = [], []
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE,
                         lambda uc, access, addr, size, value, data: self.writes.append((addr, size)))
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.trace)

    def trace(self, uc, addr, size, data):
        if addr == 0x08003EA0:
            self.calls.append((uc.reg_read(self.arm.UC_ARM_REG_R0), uc.reg_read(self.arm.UC_ARM_REG_R1)))
        if addr == 0x03007F00:
            uc.emu_stop()

    def frame(self):
        uc, arm = self.uc, self.arm
        uc.reg_write(arm.UC_ARM_REG_CPSR, 0x10)
        uc.reg_write(arm.UC_ARM_REG_SP, 0x03007D00)
        uc.reg_write(arm.UC_ARM_REG_LR, 0x03007F00)
        uc.reg_write(arm.UC_ARM_REG_R0, 0x0203F000)
        for index in range(4, 12):
            uc.reg_write(getattr(arm, f"UC_ARM_REG_R{index}"), 0x10101010 + index)
        uc.emu_start(0x0201C000, 0x03007F04, count=20000)
        assert uc.reg_read(arm.UC_ARM_REG_PC) == 0x03007F00
        assert uc.reg_read(arm.UC_ARM_REG_SP) == 0x03007D00
        for index in range(4, 12):
            assert uc.reg_read(getattr(arm, f"UC_ARM_REG_R{index}")) == 0x10101010 + index
        return uc.reg_read(arm.UC_ARM_REG_R0), struct.unpack("<I", uc.mem_read(0x0203F000, 4))[0]


def test_arm_draws_only_the_current_window_and_yields_until_the_last_frame():
    machine = ScreenMachine()
    for _ in range(DISPLAY_FRAMES - 1):
        assert machine.frame()[0] == 0
    assert machine.frame() == (1, DISPLAY_DONE)
    assert machine.calls == [(1, 2)]
    pixels = bytes(machine.uc.mem_read(0x02004000, 3584))
    bitmap = build_display_payload()[BITMAP_OFFSET:]
    assert pixels[0x3A0:0x3A0 + 192] == bitmap[:192]
    assert pixels[0x720:0x720 + 192] == bitmap[192:]
    assert pixels[:0x3A0] == b"\x11" * 0x3A0
    allowed = ((0x0201C004, 0x0201C008), (0x02004000, 0x02004E00),
               (0x0203F000, 0x0203F004), (0x03007C00, 0x03007D00))
    assert all(any(low <= address and address + size <= high for low, high in allowed)
               for address, size in machine.writes)


@pytest.mark.parametrize("kwargs,reply", [({"bad_header": True}, 0xBAD00001),
                                         ({"bad_code": True}, 0xBAD00002),
                                         ({"bad_window": True}, 0xBAD00003),
                                         ({"destination": 0x0201C000}, 0xBAD00003),
                                         ({"destination": 0x02028000}, 0xBAD00003)])
def test_arm_refuses_mismatch_or_buffer_outside_heap_before_any_window_write(kwargs, reply):
    machine = ScreenMachine(**kwargs)
    assert machine.frame() == (1, reply)
    assert machine.calls == []
    assert not any(0x02000000 <= address < 0x0201C000 for address, size in machine.writes)


def test_desktop_ns_entry_sends_one_small_script_without_a_capture_mode(tmp_path):
    tool = next(t for t in FRLG.tools if t.key == "frlg-zh-display")
    args = cli.build_parser().parse_args(build(tool, {}, {}, Settings(received=str(tmp_path)), stamp="test"))
    assert args.display_demo and args.dump_rom is None
    assert not any(t.key == "frlg-zh-display" for t in RSE.tools)
    operation = LocalizationDisplayOperation()
    sent = []
    operation.transport = type("OneRun", (), {"_run_code": lambda self, code: sent.append(code) or DISPLAY_DONE})()
    result = operation._perform()
    assert len(sent) == 1 and len(sent[0]) == 808
    assert struct.unpack_from("<II", sent[0], 8) == (COPY_WINDOW_FNV, 600)
    assert result["visual_confirmation_required"] and not result["rom_written"] and not result["save_written"]
    tmp_path.joinpath("report.json").write_text(json.dumps(result), encoding="utf-8")
    assert localization.read_display(str(tmp_path))["reply"] == DISPLAY_DONE
    result["payload_sha256"] = hashlib.sha256(b"other code").hexdigest()
    tmp_path.joinpath("report.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError):
        localization.read_display(str(tmp_path))


def test_cli_display_writes_a_report_without_a_rom_capture(tmp_path, monkeypatch):
    def complete(app):
        app.operation_result = {"state": "DISPLAY_DEMO_EXECUTED"}
        app.operation.close()
        return True
    monkeypatch.setattr(cli.LocalizationHostApplication, "run", complete)
    folder = tmp_path / "display"
    assert cli.main(["--display-demo", "--output-dir", str(folder), "--no-local-config"]) == 0
    assert [p.name for p in folder.iterdir()] == ["report.json"]


def test_ns_demo_uses_one_rpc_then_a_no_save_message_and_clean_disconnect():
    class DisplayConsole(ConsoleClientModel):
        def __init__(self):
            super().__init__()
            data = bytearray(self.game_data)
            data[mg_script.GD_OFF_GAME_CODE:mg_script.GD_OFF_GAME_CODE + 4] = b"BPRE"
            data[mg_script.GD_OFF_VERSION] = 10
            self.game_data, self.uploads = bytes(data), []

        def _run_buffer_script(self, payload):
            self.uploads.append(bytes(payload))
            self.param = DISPLAY_DONE  # ARM/window behavior is tested separately above.

    console = DisplayConsole()
    operation = LocalizationDisplayOperation()
    engine, _ = drive(operation, console)
    assert operation.result.result()["state"] == "DISPLAY_DEMO_EXECUTED"
    assert console.uploads == [build_display_payload().ljust(1024, b"\x00")]
    assert engine.close_confirmed and not engine.gift_sent
    assert console.result == mg_script.CLI_MSG_BUFFER_FAILURE  # dynamic message, no save
    assert console.saved_card is None and console.saved_ram_script is None
    assert console.saved_trainer is None and console.saved_news is None
