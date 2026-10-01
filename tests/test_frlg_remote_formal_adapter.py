import hashlib
import json

import pytest

from pokeldn.frlg.link import linkplayer, trade
from pokeldn.frlg.link.host_trade import H_ANIM, H_CONFIRM, H_SELECT, HostTradeEngine
from pokeldn.frlg.remote.config import RemoteTradeConfig, RemoteTradeMode
from pokeldn.frlg.remote.coordinator import snapshot_digest
from pokeldn.frlg.remote.formal_adapter import FormalTradeAdapter
from pokeldn.frlg.remote.protocol import PHASE_ORDER, PHASE_SIZES
from pokeldn.frlg.remote.trade_coordinator import TradeCoordinator
from pokeldn.frlg.save import mon


RUN = "a1" * 16
SNAP_A = "b2" * 16
SNAP_B = "c3" * 16


class MemoryJournal:
    def __init__(self, run_id):
        self.run_id = run_id
        self.records = []

    def append_durable(self, event, **fields):
        record = {"sequence": len(self.records), "event": event, **fields}
        self.records.append(record)
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def _mon(marker):
    return mon.Mon(bytes([marker & 0xFF]) + b"\0" * 99)


def _blocks(seed, party):
    result = {
        phase: bytes([seed + index + 1]) * PHASE_SIZES[phase]
        for index, phase in enumerate(PHASE_ORDER)
    }
    result["link_player"] = linkplayer.build_block(
        linkplayer.LinkPlayer(name=f"P{seed}")).ljust(PHASE_SIZES["link_player"], b"\0")
    raw_party = b"".join(item.raw for item in party).ljust(600, b"\0")
    for index in range(3):
        result[f"party_{index}"] = raw_party[index * 200:(index + 1) * 200]
    return result


def _coordinator_and_snapshots(party_a, party_b):
    journal = MemoryJournal(RUN)
    coordinator = TradeCoordinator(RUN, journal)
    blocks_a, blocks_b = _blocks(1, party_a), _blocks(20, party_b)
    digest_a, digest_b = snapshot_digest(blocks_a), snapshot_digest(blocks_b)
    coordinator.snapshot_ready("A", SNAP_A, blocks_a)
    coordinator.snapshot_ready("B", SNAP_B, blocks_b)
    coordinator.acknowledge_peer_snapshot("A", SNAP_B, digest_b)
    coordinator.acknowledge_peer_snapshot("B", SNAP_A, digest_a)
    for side, snapshot_id, slot in (("A", SNAP_A, 0), ("B", SNAP_B, 1)):
        slot_digest = coordinator.snapshot_summary(side)["slot_digests"][slot]
        coordinator.select(side, coordinator.trade_id, coordinator.selection_epoch,
                           snapshot_id, slot, slot_digest)
    return coordinator


def _start_release(coordinator):
    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    coordinator.confirm("A", trade_id, epoch, True)
    coordinator.confirm("B", trade_id, epoch, True)
    coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash)
    coordinator.prepared("B", trade_id, epoch, coordinator.selection_hash)
    decision = coordinator.create_decision("start")
    for side in ("A", "B"):
        coordinator.decision_stored(side, "start", trade_id, epoch,
                                    decision.decision_hash)
    return coordinator.create_release("start")


def _finish_release(coordinator, trade_id, epoch):
    for side, digest in (("A", "11" * 32), ("B", "22" * 32)):
        coordinator.animation_finished(side, trade_id, epoch, digest)
    decision = coordinator.create_decision("finish")
    for side in ("A", "B"):
        coordinator.decision_stored(side, "finish", trade_id, epoch,
                                    decision.decision_hash)
    return coordinator.create_release("finish")


def test_formal_engine_waits_for_three_distinct_one_shot_permits():
    party = [_mon(1), _mon(2)]
    peer_party = [_mon(3), _mon(4)]
    coordinator = _coordinator_and_snapshots(party, peer_party)
    selection_permit = coordinator.issue_selection_permit("A")
    engine = HostTradeEngine(peer_party, remote_mode=RemoteTradeMode.FORMAL, anim_delay=0)
    adapter = FormalTradeAdapter(coordinator, engine, "A")
    engine._set_state(H_SELECT)

    engine._on_child_linkcmd(trade.READY_TO_TRADE, 3)
    assert engine.state == H_CONFIRM
    assert not [entry for entry in engine.trace
                if entry[:2] == ("queue_block", "SET_MONS_TO_TRADE")]
    set_audit = adapter.apply_permit(selection_permit)
    assert set_audit.execution_count == 1
    assert coordinator.is_consumed(selection_permit)
    assert adapter.apply_permit(selection_permit).execution_count == 0

    engine._on_child_linkcmd(trade.INIT_BLOCK, 0)
    assert adapter.take_events()[-1].kind == "INIT_BLOCK"
    adapter.observe_command(selection_permit, "INIT_BLOCK")
    start_release = _start_release(coordinator)
    start_permit_a = coordinator.receive_release("A", start_release)
    coordinator.receive_release("B", start_release)
    start_audit = adapter.apply_permit(start_permit_a)
    assert start_audit.execution_count == 1
    assert engine.state == H_ANIM

    engine._on_child_linkcmd(trade.READY_FINISH_TRADE, 0)
    adapter.take_events()
    adapter.observe_command(start_permit_a, "READY_FINISH_TRADE")
    engine._tick_anim()
    assert engine.commits == 0
    with pytest.raises(RuntimeError, match="active finish permit"):
        engine._commit()

    finish_release = _finish_release(coordinator, coordinator.trade_id,
                                     coordinator.selection_epoch)
    finish_permit_a = coordinator.receive_release("A", finish_release)
    coordinator.receive_release("B", finish_release)
    finish_audit = adapter.apply_permit(finish_permit_a)
    assert finish_audit.execution_count == 1
    assert engine.commits == 1
    assert engine.state == "H_SAVE"
    engine._on_child_standby(1)
    assert adapter.take_events()[-1].kind == "SAVE_STARTED"
    adapter.observe_command(finish_permit_a, "SAVE_STARTED")
    assert adapter.apply_permit(finish_permit_a).execution_count == 0
    assert engine.commits == 1
    assert [item.command for item in adapter.audit] == [
        "SET_MONS_TO_TRADE", "START_TRADE", "CONFIRM_FINISH_TRADE"]
    assert all(item.observed_at is not None for item in adapter.audit)


def test_formal_engine_has_no_command_path_without_adapter_or_active_permit():
    engine = HostTradeEngine([_mon(1)], remote_mode=RemoteTradeMode.FORMAL)
    with pytest.raises(RuntimeError, match="requires its active one-shot permit"):
        engine._send_linkcmd(trade.START_TRADE)


def test_state_mismatch_consumes_start_permit_and_never_replays_after_release():
    party_a, party_b = [_mon(1)], [_mon(3)]
    party_b.append(_mon(4))
    coordinator = _coordinator_and_snapshots(party_a, party_b)
    release = _start_release(coordinator)
    permit = coordinator.receive_release("A", release)
    engine = HostTradeEngine(party_a, remote_mode=RemoteTradeMode.FORMAL)
    adapter = FormalTradeAdapter(coordinator, engine, "A")

    with pytest.raises(RuntimeError, match="local Yes event"):
        adapter.apply_permit(permit)

    assert coordinator.permit_status(permit) == "failed"
    assert coordinator.state == "IN_DOUBT"
    assert [record["event"] for record in coordinator.journal.records[-2:]] == [
        "command_permit_consumed", "command_failed"]
    assert adapter.apply_permit(permit).execution_count == 0
    assert not [entry for entry in engine.trace
                if entry[:2] == ("queue_block", "START_TRADE")]


def test_remote_config_mode_is_probe_by_default_and_formal_is_still_blocked():
    config = RemoteTradeConfig("host", listen="127.0.0.1", room_key=b"k" * 16)
    assert config.mode is RemoteTradeMode.PROBE
    assert not config.is_formal
    with pytest.raises(ValueError, match="formal LAN mode is blocked"):
        RemoteTradeConfig("host", listen="127.0.0.1", room_key=b"k" * 16,
                          mode=RemoteTradeMode.FORMAL)
