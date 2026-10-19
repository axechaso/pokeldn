"""Actual ARM/Thumb instructions, synthetic native calls, and the no-save RFU path."""
import json
import struct

import pytest

from pokeldn.frlg.localization import LocalizationError, read_json
from pokeldn.frlg.localization import ns_story as story
from pokeldn.frlg.rom.resident_stubs import STUBS
from pokeldn.app import localization
from pokeldn.app.catalog import FRLG, RSE
from pokeldn.app.command import build
from pokeldn.app.settings import Settings
from pokeldn.frlg.localization import cli
from pokeldn.frlg.gift import mg_script
from test_frlg_localization_gift import drive
from test_mystery_gift_flow import ConsoleClientModel


class StoryMachine:
    def __init__(self, monkeypatch):
        unicorn = pytest.importorskip("unicorn")
        from unicorn import arm_const as arm
        self.arm, self.uc = arm, unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        for address, size in ((0x02000000, 0x40000), (0x03000000, 0x8000),
                              (0x04000000, 0x1000), (0x08000000, 0x1000000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0x080000AC, b"BPRE")
        self.uc.mem_write(0x080000BC, b"\x0a")
        self.uc.mem_write(0x030027E0, struct.pack("<I", 0x08000735))
        self.uc.mem_write(0x03003020, struct.pack("<I", 0x0841CF08))
        self.uc.mem_write(0x04000208, b"\x01\x00")
        profile = read_json(story.ASSETS / "ns-story-profile.json")
        self.uc.mem_write(0x0841CF08, bytes.fromhex(profile["font_table"]))
        self.message = b"X" * 88 + b"\xff"
        self.uc.mem_write(0x02021D18, self.message)
        profile["text_fnv"] = story.fnv(self.message)
        for guard in profile["guards"]:
            guard["fnv"] = story.fnv(bytes(self.uc.mem_read(guard["address"], guard["size"])))
        monkeypatch.setattr(story, "read_json", lambda path: profile if path.name == "ns-story-profile.json"
                            else read_json(path))
        self.blob, self.payloads = story.build_story_blob(), story.build_story_payloads()
        self.symbols = STUBS["localization-story"][2]
        self.writes, self.draws, self.native_calls = [], [], []
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE,
                         lambda uc, access, addr, size, value, data: self.writes.append((addr, size)))
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.trace)

    def word(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def put_word(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value))

    def trace(self, uc, addr, size, data):
        arm = self.arm
        if addr == 0x03007F00:
            uc.emu_stop()
        if addr in (0x08008DF0, 0x08008D80, 0x08002F94, 0x08000734):
            assert uc.reg_read(arm.UC_ARM_REG_SP) % 8 == 0
            pointer = uc.reg_read(arm.UC_ARM_REG_R0)
            if addr in (0x08008DF0, 0x08008D80):
                self.native_calls.append(addr)
                # This is deliberately a model of native pacing/control behavior.
                # Full GBA validation uses the untouched local source build separately.
                text = self.word(pointer)
                state = uc.mem_read(pointer + 0x1C, 1)[0]
                delay = uc.mem_read(pointer + 0x1E, 1)[0]
                char = uc.mem_read(text, 1)[0]
                if state:
                    uc.mem_write(pointer + 0x1C, b"\x00")
                    result = 3
                elif delay:
                    uc.mem_write(pointer + 0x1E, bytes([delay - 1]))
                    result = 3
                elif char == 0xFC:
                    assert bytes(uc.mem_read(text, 2)) == b"\xfc\x07"
                    self.put_word(pointer, text + 2)
                    result = 2
                elif char == 0xFB:
                    self.put_word(pointer, text + 1)
                    uc.mem_write(pointer + 0x1C, b"\x02")
                    result = 3
                else:
                    self.put_word(pointer, text + 1)
                    result = 1 if char == 0xFF else 0
                uc.reg_write(arm.UC_ARM_REG_R0, result)
            elif addr == 0x08002F94:
                self.draws.append((bytes(uc.mem_read(0x03003030, 132)),
                                   uc.mem_read(pointer + 8, 1)[0]))
            uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))

    def call(self, address, *, thumb=False, arg=0x0203F000):
        uc, arm = self.uc, self.arm
        uc.reg_write(arm.UC_ARM_REG_CPSR, 0x30 if thumb else 0x10)
        uc.reg_write(arm.UC_ARM_REG_SP, 0x03007D00)
        uc.reg_write(arm.UC_ARM_REG_LR, 0x03007F01 if thumb else 0x03007F00)
        uc.reg_write(arm.UC_ARM_REG_R0, arg)
        for index in range(4, 12):
            uc.reg_write(getattr(arm, f"UC_ARM_REG_R{index}"), 0x10101010 + index)
        uc.emu_start(address | int(thumb), 0x03007F04, count=200000)
        assert uc.reg_read(arm.UC_ARM_REG_PC) == 0x03007F00
        assert uc.reg_read(arm.UC_ARM_REG_SP) == 0x03007D00
        for index in range(4, 12):
            assert uc.reg_read(getattr(arm, f"UC_ARM_REG_R{index}")) == 0x10101010 + index
        return uc.reg_read(arm.UC_ARM_REG_R0)

    def upload(self, index):
        self.uc.mem_write(0x0201C000, self.payloads[index])
        assert self.call(0x0201C000) == 1
        return self.word(0x0203F000)

    def install(self):
        assert self.upload(0) == story.STAGED
        assert self.upload(1) == story.INSTALLED

    def render(self, *, state=None):
        if state is not None:
            self.uc.mem_write(0x02020034 + 0x1C, bytes([state]))
        return self.call(story.RESIDENT + self.symbols["story_font"], thumb=True, arg=0x02020034)

    def printer(self, *, text=0x02021D18, font=4, delay=0):
        record = bytearray(36)
        struct.pack_into("<I", record, 0, text)
        record[5], record[12], record[13], record[0x1E] = font, 0x20, 0x31, delay
        self.uc.mem_write(0x02020034, bytes(record))


def test_production_payloads_are_two_small_ram_only_stages():
    payloads = story.build_story_payloads()
    assert [len(code) for code in payloads] == [1024, 1024]
    assert len(story.build_story_blob()) == 784


def test_activation_is_last_and_reinstall_is_idempotent(monkeypatch):
    machine = StoryMachine(monkeypatch)
    assert machine.upload(0) == story.STAGED
    assert machine.word(0x030027E0) == 0x08000735
    assert machine.word(0x03003020) == 0x0841CF08
    assert machine.upload(1) == story.INSTALLED
    assert bytes(machine.uc.mem_read(story.RESIDENT, len(machine.blob))) == machine.blob
    assert machine.word(0x030027E0) == story.RESIDENT + 1
    assert machine.uc.mem_read(0x04000208, 2) == b"\x01\x00"
    machine.call(story.RESIDENT, thumb=True)
    assert machine.word(0x03003020) == story.RESIDENT + machine.symbols["font_table"]
    # VBlank leaves every other font table untouched.
    machine.put_word(0x03003020, 0x0841CF0C)
    machine.call(story.RESIDENT, thumb=True)
    assert machine.word(0x03003020) == 0x0841CF0C
    assert machine.upload(0) == story.ALREADY_INSTALLED
    assert machine.upload(1) == story.ALREADY_INSTALLED
    allowed = ((story.RESIDENT, 0x02040000), (0x0203F000, 0x0203F004),
               (0x030027E0, 0x030027E4), (0x03003020, 0x03003024),
               (0x03007C00, 0x03007D00), (0x04000208, 0x0400020A))
    assert all(any(lo <= address and address + size <= hi for lo, hi in allowed)
               for address, size in machine.writes)


@pytest.mark.parametrize("change,reply", [("header", 0xBAD10001), ("native", 0xBAD10002),
                                        ("resident", 0xBAD10003), ("interrupt", 0xBAD10003),
                                        ("font", 0xBAD10003)])
def test_mismatch_is_refused_before_any_resident_write(monkeypatch, change, reply):
    machine = StoryMachine(monkeypatch)
    address, value = {"header": (0x080000AC, b"BPGE"), "native": (0x08009128, b"\x01"),
                      "resident": (story.RESIDENT + 1000, b"\x01"),
                      "interrupt": (0x030027E0, struct.pack("<I", story.RESIDENT + 1)),
                      "font": (0x03003020, struct.pack("<I", 0x02030000))}[change]
    machine.uc.mem_write(address, value)
    assert machine.upload(0) == reply
    assert not any(story.RESIDENT <= address < 0x02040000 for address, _ in machine.writes)


def test_second_stage_requires_the_exact_first_stage(monkeypatch):
    machine = StoryMachine(monkeypatch)
    assert machine.upload(1) == 0xBAD10003
    assert machine.upload(0) == story.STAGED
    machine.uc.mem_write(story.RESIDENT + 100, b"\x01")
    assert machine.upload(1) == 0xBAD10003
    assert machine.word(0x030027E0) == 0x08000735


@pytest.mark.parametrize("font", [2, 4])
def test_sentence_uses_native_pacing_colors_and_english_continuation(monkeypatch, font):
    machine = StoryMachine(monkeypatch)
    machine.install()
    machine.printer(font=font, delay=2)
    prefix = story.RESIDENT + machine.symbols["prefix"]
    assert machine.render() == 3
    assert machine.word(0x02020034) == prefix
    assert machine.render() == 3
    assert not machine.draws
    for _ in story.TEXT:
        assert machine.render() == 0
    assert len(machine.draws) == 9
    font_data = story.build_story_blob()[machine.symbols["glyphs"]:]
    for index, (info, x) in enumerate(machine.draws):
        assert x == index * 14 and info[128:130] == bytes([14, 16])
        # Decode the native four-tile glyph buffer back into logical pixels.
        bitmap = font_data[index * 32:(index + 1) * 32]
        for y in range(16):
            bits = int.from_bytes(bitmap[y * 2:y * 2 + 2], "little")
            for xx in range(16):
                offset = ((y // 8) * 2 + xx // 8) * 32 + (y % 8) * 4 + (xx % 8) // 2
                pixel = info[offset] >> ((xx % 2) * 4) & 15
                assert pixel == (2 if bits >> xx & 1 else 1)
    assert machine.render() == 3  # original FB enters native wait/clear
    assert machine.render() == 3  # native input completes that state
    assert machine.word(0x02020034) == 0x02021D18 + 26
    assert machine.render() == 0  # original English second page
    assert len(machine.draws) == 9
    assert set(machine.native_calls) == ({0x08008D80} if font == 2 else {0x08008DF0})
    assert bytes(machine.uc.mem_read(0x02021D18, 89)) == machine.message


def test_other_dialogue_uses_the_original_font(monkeypatch):
    machine = StoryMachine(monkeypatch)
    machine.install()
    machine.uc.mem_write(0x02021D18, b"Y")
    machine.printer()
    assert machine.render() == 0
    assert machine.word(0x02020034) == 0x02021D19
    assert not machine.draws


def test_client_and_report_distinguish_upload_from_in_game_confirmation(tmp_path):
    tool = next(t for t in FRLG.tools if t.key == "frlg-localization-story")
    args = cli.build_parser().parse_args(build(tool, {}, {}, Settings(received=str(tmp_path)), stamp="test"))
    assert args.story_demo and not args.display_demo and args.dump_rom is None
    assert not any(t.key == "frlg-localization-story" for t in RSE.tools)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--story-demo", "--display-demo", "--report", "unused"])
    operation = story.LocalizationStoryOperation()
    answers = iter((story.STAGED, story.INSTALLED))
    sent = []

    def run_code(code):
        sent.append(code)
        return next(answers)

    operation.transport = type("Runs", (), {"_run_code": staticmethod(run_code)})()
    report = operation._perform()
    assert tuple(sent) == story.build_story_payloads()
    assert report["visual_confirmation_required"] and not report["rom_written"] and not report["save_written"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert localization.read_story(str(tmp_path))["text"] == story.TEXT
    report["resident_sha256"] = "0" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        localization.read_story(str(tmp_path))


def test_already_installed_does_not_upload_a_second_stage(tmp_path):
    operation = story.LocalizationStoryOperation()
    sent = []
    operation.transport = type("Runs", (), {"_run_code": staticmethod(
        lambda code: sent.append(code) or story.ALREADY_INSTALLED)})()
    report = operation._perform()
    assert len(sent) == 1
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    assert localization.read_story(str(tmp_path))["reply"] == story.ALREADY_INSTALLED


def test_refusal_stops_before_the_next_stage():
    operation = story.LocalizationStoryOperation()
    sent = []
    operation.transport = type("Runs", (), {"_run_code": staticmethod(
        lambda code: sent.append(code) or 0xBAD10003)})()
    with pytest.raises(LocalizationError, match="occupied"):
        operation._perform()
    assert len(sent) == 1


def test_two_rpc_uploads_then_no_save_message_and_clean_disconnect():
    class StoryConsole(ConsoleClientModel):
        def __init__(self):
            super().__init__()
            data = bytearray(self.game_data)
            data[mg_script.GD_OFF_GAME_CODE:mg_script.GD_OFF_GAME_CODE + 4] = b"BPRE"
            data[mg_script.GD_OFF_VERSION] = 10
            self.game_data, self.uploads = bytes(data), []

        def _run_buffer_script(self, payload):
            self.uploads.append(bytes(payload))
            self.param = story.STAGED if len(self.uploads) == 1 else story.INSTALLED

    console, operation = StoryConsole(), story.LocalizationStoryOperation()
    engine, _ = drive(operation, console)
    assert operation.result.result()["state"] == "STORY_DEMO_READY"
    assert console.uploads == list(story.build_story_payloads())
    assert engine.close_confirmed and not engine.gift_sent
    assert console.result == mg_script.CLI_MSG_BUFFER_FAILURE  # dynamic text, no save path
    assert console.saved_card is None and console.saved_ram_script is None
    assert console.saved_trainer is None and console.saved_news is None


def test_cli_story_writes_only_a_report(tmp_path, monkeypatch):
    def complete(app):
        assert isinstance(app.operation, story.LocalizationStoryOperation)
        app.operation_result = {"state": "STORY_DEMO_READY"}
        app.operation.close()
        return True

    monkeypatch.setattr(cli.LocalizationHostApplication, "run", complete)
    folder = tmp_path / "story"
    assert cli.main(["--story-demo", "--output-dir", str(folder), "--no-local-config"]) == 0
    assert [p.name for p in folder.iterdir()] == ["report.json"]
