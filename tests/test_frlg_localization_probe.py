"""Read-only bootstrap obtains runtime material over PokeLDN, without a base ROM input."""

from pathlib import Path
import os
import subprocess
import sys

import pytest

import localization_probe
from pokeldn.frlg.localization import LocalizationError, sha256
from pokeldn.frlg.localization.examples import synthetic_example
from pokeldn.frlg.localization.probe import LocalizationProbeOperation
from pokeldn.frlg.localization.transport import ModelTransport
from pokeldn.frlg.rom import buffer_script as bs
from test_frlg_localization_gift import PersistentConsole, drive

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def model():
    if not bs.emulation_available():
        pytest.skip("needs unicorn")
    profile, base, _ = synthetic_example()
    return ModelTransport(profile, base)


def test_probe_captures_runtime_rom_without_a_base_input(model, tmp_path):
    output = tmp_path / "runtime.gba"
    operation = LocalizationProbeOperation(rom_size=model.profile.rom_size, dump_path=output)
    engine, _ = drive(operation, PersistentConsole(model))
    result = operation.result.result()
    expected = model.profile.runtime_base(model.base)
    assert output.read_bytes() == expected
    assert result["runtime_sha256"] == sha256(expected)
    assert result["state"] == "READ_ONLY_CAPTURED" and result["read_only"]
    assert not result["write_eligible"] and not result["wrapper_identity_verified"]
    assert not result["original_rom_verified"] and not model.machine.rom_writes
    assert result["capture_elapsed_seconds"] >= 0
    responses = [item for item in operation.server.trace
                 if isinstance(item, tuple) and item[0] == "localization_response"]
    assert [item[3] for item in responses] == [192] + [4096] * 16
    scripts = [item for item in engine.trace if item[0] == "send_message" and item[1] == 16]
    assert max(item[2] for item in scripts) == 2
    assert result["read_batch_bytes"] == 4096
    text = result["text_engine"]
    assert text["size"] == 65536 and not text["symbol_addresses_verified"]
    assert not text["game_encoding_assigned"] and not text["write_eligible"]
    renderer = next(window for window in text["windows"] if window["name"] == "RenderText")
    assert renderer["address"] == "0x08009128"
    offset, size = renderer["offset"], renderer["window_size"]
    assert renderer["sha256"] == sha256(expected[offset:offset + size])


def test_probe_rejects_conflicting_memory_header(model, tmp_path):
    model.machine.uc.mem_write(0x080000AC, b"BPGE")
    output = tmp_path / "runtime.gba"
    operation = LocalizationProbeOperation(rom_size=model.profile.rom_size, dump_path=output)
    drive(operation, PersistentConsole(model))
    with pytest.raises(LocalizationError, match="header conflicts"):
        operation.result.result()
    assert not output.exists() and not model.machine.rom_writes


def test_probe_does_not_overwrite_existing_capture(tmp_path):
    output = tmp_path / "runtime.gba"
    output.write_bytes(b"existing")
    with pytest.raises(LocalizationError, match="already exists"):
        LocalizationProbeOperation(dump_path=output)
    assert output.read_bytes() == b"existing"
    with pytest.raises(LocalizationError, match="parent"):
        LocalizationProbeOperation(dump_path=tmp_path / "missing" / "runtime.gba")


def test_probe_cli_uses_existing_host_config_and_fixed_read_payload():
    args = localization_probe.build_parser().parse_args(["--report", "probe.json", "--no-local-config"])
    config = localization_probe.host_config(args)
    assert config.payload.script == bs.MEMORY_DUMP
    assert config.payload.dump_address == 0x08000000 and config.payload.dump_size == 192
    assert config.console_build == "BPRE"


def test_probe_host_initialization_keeps_its_server_and_supports_a_cardless_screen(model, monkeypatch):
    from pokeldn.app import screen
    from pokeldn.frlg.localization.host import LocalizationHostApplication

    class FakeTransport:
        NEEDS_RADIO = False

        def __init__(self, **kwargs):
            pass

    args = localization_probe.build_parser().parse_args(["--report", "probe.json", "--no-local-config"])
    operation = LocalizationProbeOperation(rom_size=model.profile.rom_size)
    app = LocalizationHostApplication(localization_probe.host_config(args), operation,
                                      transport_factory=FakeTransport, log=lambda *args: None)
    shown = []
    monkeypatch.setattr(screen, "gift", lambda *args, **kwargs: shown.append((args, kwargs)))
    try:
        app._build_components()
        assert app.session.activity.server is operation.server
        assert app.card is None
        app._show_screen()
        assert shown == [(("FRLG localization", "Read-only ROM capture"), {"species": None})]
    finally:
        operation.close()


def test_probe_cli_help_has_no_original_rom_requirement():
    result = subprocess.run([sys.executable, str(ROOT / "tools/frlg/localization_probe.py"), "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--base" not in result.stdout and "--write" not in result.stdout


def test_probe_interrupt_exit_code_and_radio_environment_are_preserved(tmp_path, monkeypatch):
    from pokeldn.frlg.localization.gift_transport import GiftConnectionClosed
    monkeypatch.setenv("POKELDN_RADIO", "esp32:COM7")
    def interrupted(app):
        app.interrupted = True
        raise GiftConnectionClosed("interrupted")
    monkeypatch.setattr(localization_probe.LocalizationHostApplication, "run", interrupted)
    assert localization_probe.main(["--report", str(tmp_path / "report.json"),
                                    "--no-local-config", "--radio", "esp32:auto"]) == 130
    assert os.environ["POKELDN_RADIO"] == "esp32:COM7"
    assert not (tmp_path / "report.json").exists()


def test_probe_timeout_exits_with_an_error_and_no_success_report(tmp_path, monkeypatch, capsys):
    from pokeldn.frlg.link.host_app import HostApplication
    from pokeldn.frlg.localization.gift_transport import GiftConnectionClosed

    captured = []

    def timeout(app):
        captured.append(app.operation)
        app.operation.result.set_exception(GiftConnectionClosed("Mystery Gift RPC timed out"))
        app._log_activity_progress()
        raise AssertionError("worker failure was swallowed")

    monkeypatch.setattr(HostApplication, "run", timeout)
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit) as error:
        localization_probe.main(["--report", str(report), "--no-local-config"])
    assert error.value.code == 2 and not report.exists()
    assert "localization probe stopped: Mystery Gift RPC timed out" in capsys.readouterr().err
    assert captured[0].bridge.closed
