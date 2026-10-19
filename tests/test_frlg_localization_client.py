"""The client shares the probe, preserves its read-only gates, and displays completed captures."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pokeldn.app import localization
from pokeldn.app.catalog import FRLG, RSE
from pokeldn.app.command import build
from pokeldn.app.introspect import parser_of
from pokeldn.app.settings import Settings
from pokeldn.frlg.localization import cli
from pokeldn.frlg.localization.gift_transport import GiftConnectionClosed
from pokeldn.frlg.localization.probe import LocalizationProbeOperation
from test_frlg_localization_gift import PersistentConsole, drive
from test_frlg_localization_probe import model  # noqa: F401


def capture(folder, size=4096):
    folder.mkdir()
    runtime = folder / ("runtime.gba" if size == 0x1000000 else "runtime-sample.bin")
    runtime.write_bytes(bytes(size))
    result = {"state": "READ_ONLY_CAPTURED", "read_only": True, "write_eligible": False,
              "game_code": "BPRE", "revision": 10, "rom_size": size,
              "runtime_sha256": hashlib.sha256(bytes(size)).hexdigest()}
    (folder / "report.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def test_client_uses_the_shared_probe_and_settings_without_paths_to_fill(tmp_path):
    tool = next(t for t in FRLG.tools if t.key == "frlg-localization")
    assert parser_of(tool.script).prog
    args = cli.build_parser().parse_args(build(tool, {}, {},
                                              Settings(received=str(tmp_path), ot="ASH", tid=12, sid=34),
                                              stamp="test-run"))
    assert args.rom_size == 4096 and args.report is None and args.dump_rom is None
    assert args.output_dir.parent == tmp_path and "test-run" in args.output_dir.name
    config = cli.host_config(args)
    assert config.profile.name == "ASH" and (config.profile.tid, config.profile.sid) == (12, 34)
    assert config.console_build == "BPRE"
    assert not any("localization" in t.key for t in RSE.tools)
    mode = next(field for field in tool.fields if field.flag == "--rom-size")
    assert {value for value, label in mode.choices} == {"0x1000", "0x10000", "0x1000000"}


@pytest.mark.parametrize("size", [4096, 0x10000, 0x1000000])
def test_result_distinguishes_a_runtime_sample_from_a_complete_rom(tmp_path, size):
    expected = capture(tmp_path / "output", size)
    result = localization.read_capture(str(tmp_path / "output"))
    assert result.full_rom == (size == 0x1000000)
    assert result.text_engine == (size == 0x10000)
    assert result.size == size and result.sha256 == expected["runtime_sha256"]
    assert result.runtime.is_file() and result.report.is_file()


@pytest.mark.parametrize("change", [{"read_only": False}, {"write_eligible": True},
                                    {"state": "PARTIAL"}, {"runtime_sha256": "short"}])
def test_client_never_presents_an_incomplete_or_writable_report_as_success(tmp_path, change):
    folder = tmp_path / "output"
    result = capture(folder)
    (folder / "report.json").write_text(json.dumps({**result, **change}), encoding="utf-8")
    with pytest.raises(ValueError):
        localization.read_capture(str(folder))


def test_client_refuses_a_truncated_binary_even_with_a_complete_report(tmp_path):
    folder = tmp_path / "output"
    capture(folder)
    (folder / "runtime-sample.bin").write_bytes(b"partial")
    with pytest.raises(ValueError, match="incomplete"):
        localization.read_capture(str(folder))


def test_client_checks_the_actual_binary_fingerprint(tmp_path):
    folder = tmp_path / "output"
    capture(folder)
    (folder / "runtime-sample.bin").write_bytes(b'x' * 4096)
    with pytest.raises(ValueError, match="fingerprint"):
        localization.read_capture(str(folder))


def test_client_output_folder_is_created_and_reported_by_the_real_entry_point(tmp_path, monkeypatch):
    def complete(app):
        output = app.operation.dump_path
        output.write_bytes(bytes(app.operation.rom_size))
        app.operation_result = {"state": "READ_ONLY_CAPTURED", "read_only": True,
                                "write_eligible": False, "game_code": "BPRE", "revision": 10,
                                "rom_size": app.operation.rom_size,
                                "runtime_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        app.operation.close()
        return True

    monkeypatch.setattr(cli.LocalizationHostApplication, "run", complete)
    folder = tmp_path / "output"
    assert cli.main(["--output-dir", str(folder), "--rom-size", "0x1000", "--no-local-config"]) == 0
    assert localization.read_capture(str(folder)).size == 4096
    with pytest.raises(SystemExit) as error:
        cli.main(["--output-dir", str(folder), "--rom-size", "0x1000", "--no-local-config"])
    assert error.value.code == 2 and localization.read_capture(str(folder)).size == 4096


def test_probe_reports_progress_and_reads_only_the_quick_sample(model, tmp_path):
    shown = []
    operation = LocalizationProbeOperation(rom_size=4096, dump_path=tmp_path / "sample.bin",
                                           progress=lambda done, total: shown.append((done, total)))
    drive(operation, PersistentConsole(model))
    assert operation.result.result()["rom_size"] == 4096
    assert shown == [(done, 4096) for done in range(0, 4097, 1024)]
    assert not model.machine.rom_writes


def test_progress_callback_failure_closes_the_pending_read(model, tmp_path):
    def fail_on_chunk(done, total):
        if done:
            raise RuntimeError("progress display failed")

    operation = LocalizationProbeOperation(rom_size=4096, dump_path=tmp_path / "sample.bin",
                                           progress=fail_on_chunk)
    drive(operation, PersistentConsole(model))
    with pytest.raises(GiftConnectionClosed, match="progress display failed"):
        operation.result.result()
    assert operation.bridge.closed and not model.machine.rom_writes


@pytest.mark.parametrize("size,title", [(4096, "Quick connection test passed"),
                                       (65536, "Text engine captured")])
def test_session_card_shows_the_result_and_keeps_its_files_accessible(tmp_path, monkeypatch, size, title):
    pytest.importorskip("flet")
    import flet as ft
    from dataclasses import fields
    from gui.views.games import SessionPanel
    from gui.views import games

    folder = tmp_path / "output"
    result = capture(folder, size)
    result["capture_elapsed_seconds"] = 42.5
    (folder / "report.json").write_text(json.dumps(result), encoding="utf-8")
    panel = SessionPanel.__new__(SessionPanel)
    panel.app = SimpleNamespace(copy=lambda value: None)
    panel.capture_output, panel.received = str(folder), ft.Container()
    panel.received.update = lambda: None
    panel.show_localization_result()

    def controls(control):
        yield control
        for field in fields(control):
            if field.name.startswith("_"):
                continue
            value = getattr(control, field.name)
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ft.Control):
                    yield from controls(child)

    shown = list(controls(panel.received))
    assert panel.received.visible
    assert any(isinstance(c, ft.Text) and c.value == title for c in shown)
    assert any(isinstance(c, ft.Text) and c.value == "Reading took 42.5 seconds." for c in shown)
    opened = []
    monkeypatch.setattr(games, "open_folder", opened.append)
    button = next(c for c in shown if isinstance(c, ft.TextButton) and c.content == "Open output folder")
    button.on_click(None)
    assert opened == [str(folder)]
