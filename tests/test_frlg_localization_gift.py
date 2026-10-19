"""Real host/client message state machines, synthetic ROM and persistent ARM execution."""

from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from threading import Thread
import time

import pytest

from pokeldn.frlg.gift import mg_script, mg_server
from pokeldn.frlg.gift.host_mystery_gift import HostMysteryGiftEngine, MysteryGiftTiming
from pokeldn.frlg.gift.mystery_gift import MG_LINKID_RESPONSE
from pokeldn.frlg.link import host_session
from pokeldn.frlg.localization import LocalizationError, PAGE_SIZE, read_json
from pokeldn.frlg.localization.examples import synthetic_example
from pokeldn.frlg.localization.gift_transport import (GiftBridge, GiftConnectionClosed,
                                                     LocalizationGiftOperation)
from pokeldn.frlg.localization.transport import ModelTransport, Observation
from pokeldn.frlg.rom import buffer_script as bs
from pokeldn.gba import rfu, rfu_leader
from test_mystery_gift_flow import ConsoleClientModel


@pytest.fixture
def example():
    if not bs.emulation_available():
        pytest.skip("needs unicorn")
    return synthetic_example()


class PersistentConsole(ConsoleClientModel):
    def __init__(self, model):
        super().__init__()
        self.model = model
        data = bytearray(self.game_data)
        data[mg_script.GD_OFF_GAME_CODE:mg_script.GD_OFF_GAME_CODE + 4] = model.profile.identity.game_code.encode()
        data[mg_script.GD_OFF_VERSION] = model.profile.identity.revision
        self.game_data = bytes(data)

    def _run_buffer_script(self, payload):
        machine, uc = self.model.machine, self.model.machine.uc
        param_at = bs._CLIENT_ADDRESS + bs.CLIENT_PARAM
        uc.mem_write(param_at, int(self.param).to_bytes(4, "little"))
        armed = self._pending_send
        ident, size = (armed[0], armed[2]) if armed else (MG_LINKID_RESPONSE, 4)
        machine.armed_size = size
        link = bs._CLIENT_ADDRESS + bs.CLIENT_LINK
        uc.mem_write(link + bs.LINK_SEND_BUFFER, bs._SEND_BUFFER_ADDRESS.to_bytes(4, "little"))
        uc.mem_write(link + bs.LINK_SEND_SIZE, size.to_bytes(2, "little"))
        uc.mem_write(link + bs.LINK_SEND_IDENT, ident.to_bytes(2, "little"))
        uc.mem_write(bs.GDECOMPRESSION_BUFFER, bytes(payload).ljust(1024, b"\x00"))
        uc.ctl_remove_cache(bs.GDECOMPRESSION_BUFFER, bs.GDECOMPRESSION_BUFFER + 1024)
        run = machine.call()
        self.param = run.param
        if run.client.send_changed:
            self._pending_send = (run.client.send_ident, run.pending_send, run.client.send_size)


def operation_for(example, tmp_path, model, *, operation="load", observer=None, timeout=10):
    profile, base, patch = example
    return LocalizationGiftOperation(profile, patch, base, tmp_path / "recovery.local.json",
                                     observer or (lambda data: model.observation),
                                     operation=operation, timeout=timeout)


def drive(operation, console, *, max_frames=180000, on_frame=None):
    engine = operation.make_engine(timing=MysteryGiftTiming(client_ready_idle_frames=10))
    echo = rfu_leader.ChildEcho()
    close_rows = 0
    try:
        for frame in range(max_frames):
            parent = rfu.serialize(engine.tick())
            child = console.step(parent, echo.next_row())
            if console.func == "done" and close_rows < 4:
                child = rfu.serialize(rfu.close_link_words(1))
                close_rows += 1
            echo.append(rfu_leader._normalize_child_cmd(child))
            engine.feed_child_slot(child)
            if on_frame is not None:
                on_frame(frame, engine, console)
            if engine._server_waiting:
                # The real host yields in network.wait_readable. Virtual RFU frames must
                # also give the disk/RPC worker scheduler time instead of spinning through its budget.
                time.sleep(0.001)
            if engine.disconnect_requested:
                engine.mark_disconnect_sent()
                return engine, frame
        raise AssertionError(f"wire session did not close: {engine.state}, {console.func}; "
                             f"server={engine.server.action}, sent={engine.server.messages_sent}, "
                             f"received={engine.server.messages_received}, "
                             f"rpc={engine.server.trace[-8:]}, result={operation.result.done()}")
    finally:
        operation.close()


def test_pages_load_and_restore_through_one_continuous_gift_session(example, tmp_path):
    profile, base, patch = example
    model = ModelTransport(profile, base)
    console = PersistentConsole(model)
    operation = operation_for(example, tmp_path, model)
    engine, _ = drive(operation, console)
    assert operation.result.result()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]
    assert engine.close_confirmed and not engine.gift_sent
    assert console.result == mg_script.CLI_MSG_BUFFER_FAILURE  # no-save dynamic-message exit
    assert console.saved_card is None and console.saved_ram_script is None
    assert not console.dropped_inits and not console.dropped_fragments
    assert len([x for x in engine.server.trace if x == mg_server.SVR_COPY_GAME_DATA]) == 1
    responses = [x for x in engine.server.trace if isinstance(x, tuple) and x[0] == "localization_response"]
    blocks = [x for x in engine.server.trace if isinstance(x, tuple) and x[0] == "localization_block"]
    assert len(blocks) > 100 and len(responses) > 40
    assert any(x[3] == 32 * 1024 for x in responses)
    assert [x[1] for x in responses] == sorted({x[1] for x in responses})
    restoring = operation_for(example, tmp_path, model, operation="restore")
    drive(restoring, PersistentConsole(model))
    assert restoring.result.result()["state"] == "RESTORED"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000, 0x3000, 0x2000, 0x1000]


def test_inspection_is_read_only_and_uses_no_recovery_file(example, tmp_path):
    profile, base, _ = example
    model = ModelTransport(profile, base)
    operation = operation_for(example, tmp_path, model, operation="inspect")
    drive(operation, PersistentConsole(model))
    result = operation.result.result()
    assert result["read_only"] and result["state"] == "IDENTIFIED"
    assert not model.machine.rom_writes
    assert not (tmp_path / "recovery.local.json").exists()


@pytest.mark.parametrize("mismatch", ["game", "revision", "wrapper", "unknown-observer", "invalid-data"])
def test_identification_gates_stop_before_any_write(example, tmp_path, mismatch):
    profile, base, _ = example
    model, observer = ModelTransport(profile, base), None
    console = PersistentConsole(model)
    data = bytearray(console.game_data)
    if mismatch == "game":
        data[mg_script.GD_OFF_GAME_CODE:mg_script.GD_OFF_GAME_CODE + 4] = b"BPGE"
    elif mismatch == "revision":
        data[mg_script.GD_OFF_VERSION] = 0
    elif mismatch == "invalid-data":
        data[0:4] = b"\x00" * 4
    elif mismatch == "wrapper":
        observer = lambda data: Observation(replace(profile.identity, wrapper_sha256="0" * 64),
                                             model.observation.executing)
    else:
        observer = lambda data: None
    console.game_data = bytes(data)
    operation = operation_for(example, tmp_path, model, observer=observer)
    drive(operation, console)
    with pytest.raises(LocalizationError):
        operation.result.result()
    assert not model.machine.rom_writes
    assert not console.buffer_scripts


def test_disconnect_cancels_pending_rpc_and_preserves_write_journal(example, tmp_path):
    profile, base, _ = example
    model = ModelTransport(profile, base)
    operation = operation_for(example, tmp_path, model)
    def disconnect(frame, engine, console):
        if model.machine.rom_writes and not operation.bridge.closed:
            host_session.HostSession(engine=engine).on_ldn_leave()
    drive(operation, PersistentConsole(model), on_frame=disconnect)
    with pytest.raises(GiftConnectionClosed):
        operation.result.result()
    assert model.machine.rom_writes == [0x1000]
    assert read_json(tmp_path / "recovery.local.json")["events"][-1]["state"] == "STOPPED"


def test_bridge_timeout_poisoning_rejects_late_response_and_future_requests():
    bridge = GiftBridge(timeout=0.02)
    result = Future()
    def ask():
        try:
            bridge.exchange("run", b"code", 4)
        except Exception as exc:
            result.set_exception(exc)
    worker = Thread(target=ask)
    worker.start()
    request = bridge.requests.get(timeout=1)
    worker.join(timeout=1)
    with pytest.raises(GiftConnectionClosed, match="timed out"):
        result.result()
    assert not bridge.complete(request, b"\x00" * 4)
    with pytest.raises(GiftConnectionClosed):
        bridge.exchange("observe")


def test_engine_server_injection_rejects_mixed_distribution(example, tmp_path):
    profile, base, _ = example
    operation = operation_for(example, tmp_path, ModelTransport(profile, base))
    with pytest.raises(ValueError, match="either"):
        HostMysteryGiftEngine(server=operation.server, card=b"x")
    operation.close()


def test_batched_dump_progress_extends_inactivity_deadline():
    bridge, result = GiftBridge(timeout=0.15), Future()
    def ask():
        try:
            result.set_result(bridge.exchange("dump", b"code", 1024, 8))
        except Exception as exc:
            result.set_exception(exc)
    worker = Thread(target=ask)
    worker.start()
    request = bridge.requests.get(timeout=1)
    for _ in range(5):
        time.sleep(0.04)
        assert bridge.progress(request)
    assert bridge.complete(request, b"done")
    worker.join(timeout=1)
    assert result.result() == b"done"


def test_short_dump_response_poisoning_wakes_the_waiting_worker(example, tmp_path):
    profile, base, _ = example
    operation = operation_for(example, tmp_path, ModelTransport(profile, base))
    result = Future()
    def ask():
        try:
            operation.bridge.exchange("dump", b"code", 1024, 2)
        except Exception as exc:
            result.set_exception(exc)
    worker = Thread(target=ask)
    worker.start()
    request = operation.bridge.requests.get(timeout=1)
    server = operation.server
    server.inflight, server._response_size = request, 1024
    server._response_blocks, server._response_chunks = 2, []
    server._received = b"short"
    server._do_svr_localization_response()
    worker.join(timeout=1)
    with pytest.raises(GiftConnectionClosed, match="short"):
        result.result()
    assert operation.bridge.closed and server.connection_lost


def test_host_application_reuses_configuration_and_closes_operation(example, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pokeldn.frlg.config import HostOptions
    from pokeldn.frlg.link.host_app import HostApplication
    from pokeldn.frlg.localization.host import LocalizationHostApplication
    profile, base, _ = example
    operation = operation_for(example, tmp_path, ModelTransport(profile, base))
    config = SimpleNamespace(role=HostOptions(), profile=None, ldn=None, trust_pia=True)
    app = LocalizationHostApplication(config, operation, log=lambda *args: None)
    assert app.config is config and app.operation is operation
    assert app._build_engine(None, None).server is operation.server
    operation.result.set_result({"state": "COMPLETE"})
    monkeypatch.setattr(HostApplication, "run", lambda self: True)
    assert app.run() is True
    assert app.operation_result == {"state": "COMPLETE"} and operation.bridge.closed
