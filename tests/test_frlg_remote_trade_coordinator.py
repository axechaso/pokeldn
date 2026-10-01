import hashlib
import json
from dataclasses import replace

import pytest

from pokeldn.frlg.link import linkplayer
from pokeldn.frlg.remote.coordinator import snapshot_digest
from pokeldn.frlg.remote.protocol import (
    LanMessage, PHASE_ORDER, PHASE_SIZES, ProtocolError, validate_message,
)
from pokeldn.frlg.remote.trade_coordinator import (
    CommandPermit, TradeCoordinator, TradeCoordinatorError,
)


RUN = "a1" * 16
SNAPSHOT_A = "b2" * 16
SNAPSHOT_B = "c3" * 16


class MemoryJournal:
    def __init__(self, run_id=RUN, fail_event=None):
        self.run_id = run_id
        self.records = []
        self.fail_event = fail_event

    def append_durable(self, event, **fields):
        if event == self.fail_event:
            raise OSError("injected durable write failure")
        record = {"sequence": len(self.records), "event": event, **fields}
        self.records.append(record)
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def _blocks(seed, *, empty_party=False):
    result = {
        phase: bytes([seed + index + 1]) * PHASE_SIZES[phase]
        for index, phase in enumerate(PHASE_ORDER)
    }
    player = linkplayer.build_block(linkplayer.LinkPlayer(name=f"P{seed}"))
    result["link_player"] = player.ljust(PHASE_SIZES["link_player"], b"\0")
    if empty_party:
        result["party_0"] = bytes(200)
        result["party_1"] = bytes(200)
        result["party_2"] = bytes(200)
    return result


def _coordinator(*, journal=None):
    journal = journal or MemoryJournal()
    coordinator = TradeCoordinator(RUN, journal)
    blocks_a, blocks_b = _blocks(1), _blocks(20)
    coordinator.snapshot_ready("A", SNAPSHOT_A, blocks_a)
    coordinator.snapshot_ready("B", SNAPSHOT_B, blocks_b)
    digest_a, digest_b = snapshot_digest(blocks_a), snapshot_digest(blocks_b)
    coordinator.acknowledge_peer_snapshot("A", SNAPSHOT_B, digest_b)
    coordinator.acknowledge_peer_snapshot("B", SNAPSHOT_A, digest_a)
    return coordinator, journal


def _select_both(coordinator, slot_a=0, slot_b=1):
    trade_id = coordinator.trade_id
    epoch = coordinator.selection_epoch
    digest_a = coordinator.snapshot_summary("A")["slot_digests"][slot_a]
    digest_b = coordinator.snapshot_summary("B")["slot_digests"][slot_b]
    coordinator.select("A", trade_id, epoch, SNAPSHOT_A, slot_a, digest_a)
    coordinator.select("B", trade_id, epoch, SNAPSHOT_B, slot_b, digest_b)
    return trade_id, epoch


def _prepared(coordinator):
    trade_id, epoch = _select_both(coordinator)
    coordinator.confirm("A", trade_id, epoch, True)
    coordinator.confirm("B", trade_id, epoch, True)
    coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash)
    coordinator.prepared("B", trade_id, epoch, coordinator.selection_hash)
    return trade_id, epoch


def _start_release(coordinator):
    _prepared(coordinator)
    decision = coordinator.create_decision("start")
    coordinator.decision_stored("A", "start", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    coordinator.decision_stored("B", "start", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    return coordinator.create_release("start")


def _finish_release(coordinator):
    release = _start_release(coordinator)
    permits = [coordinator.receive_release(side, release) for side in ("A", "B")]
    for side, digest in (("A", "11" * 32), ("B", "22" * 32)):
        coordinator.animation_finished(side, release.trade_id,
                                      release.selection_epoch, digest)
    decision = coordinator.create_decision("finish")
    coordinator.decision_stored("A", "finish", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    coordinator.decision_stored("B", "finish", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    return coordinator.create_release("finish"), permits


def _assert_wire_reply(reply):
    payload = {key: value for key, value in reply.items()
               if key not in {"type", "side"}}
    message = LanMessage(1, "d4" * 16, RUN, "host", 1, 0,
                         reply["type"], None, payload)
    validate_message(message.as_dict())


def test_snapshots_must_be_complete_valid_and_acknowledged_before_selection():
    coordinator, _journal = _coordinator()
    assert coordinator.state == "SELECTING"
    assert coordinator.trade_id is not None
    assert coordinator.snapshot_summary("A")["digest"] == snapshot_digest(_blocks(1))
    assert len(coordinator.snapshot_summary("A")["slot_digests"]) == 6

    with pytest.raises(ProtocolError, match="seven ordered"):
        TradeCoordinator(RUN, MemoryJournal()).snapshot_ready(
            "A", SNAPSHOT_A, {"party_0": bytes(200)})
    with pytest.raises(ProtocolError, match="no selectable"):
        TradeCoordinator(RUN, MemoryJournal()).snapshot_ready(
            "A", SNAPSHOT_A, _blocks(2, empty_party=True))


def test_snapshot_ack_must_match_the_peer_id_and_digest():
    journal = MemoryJournal()
    coordinator = TradeCoordinator(RUN, journal)
    coordinator.snapshot_ready("A", SNAPSHOT_A, _blocks(1))
    coordinator.snapshot_ready("B", SNAPSHOT_B, _blocks(20))
    with pytest.raises(ProtocolError, match="does not match"):
        coordinator.acknowledge_peer_snapshot("A", SNAPSHOT_B, "f0" * 32)
    assert coordinator.state == "WAITING_SNAPSHOTS"


def test_select_is_bound_to_snapshot_presented_digest_and_valid_slot():
    coordinator, _journal = _coordinator()
    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    slot_hash = coordinator.snapshot_summary("A")["slot_digests"][0]
    with pytest.raises(ProtocolError, match="current local snapshot"):
        coordinator.select("A", trade_id, epoch, SNAPSHOT_B, 0, slot_hash)
    with pytest.raises(ProtocolError, match="0 through 5"):
        coordinator.select("A", trade_id, epoch, SNAPSHOT_A, 6, slot_hash)
    with pytest.raises(ProtocolError, match="empty or its digest"):
        coordinator.select("A", trade_id, epoch, SNAPSHOT_A, 0, "0" * 64)


def test_start_decision_requires_two_selections_confirmations_and_prepared_records():
    coordinator, _journal = _coordinator()
    trade_id, epoch = _select_both(coordinator)
    with pytest.raises(TradeCoordinatorError, match="not allowed"):
        coordinator.create_decision("start")
    coordinator.confirm("A", trade_id, epoch, True)
    with pytest.raises(TradeCoordinatorError, match="PREPARED requires both"):
        coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash)
    coordinator.confirm("B", trade_id, epoch, True)
    with pytest.raises(TradeCoordinatorError, match="not allowed"):
        coordinator.create_decision("start")
    coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash)
    with pytest.raises(TradeCoordinatorError, match="not allowed"):
        coordinator.create_decision("start")
    coordinator.prepared("B", trade_id, epoch, coordinator.selection_hash)
    assert coordinator.create_decision("start").kind == "start"


def test_no_start_release_before_both_decision_stored_records():
    coordinator, journal = _coordinator()
    _prepared(coordinator)
    decision = coordinator.create_decision("start")
    coordinator.decision_stored("A", "start", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    with pytest.raises(TradeCoordinatorError, match="not authorized"):
        coordinator.create_release("start")
    coordinator.decision_stored("B", "start", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    release = coordinator.create_release("start")
    record = journal.records[-1]
    assert record["event"] == "release_created"
    assert record["kind"] == "start"
    assert release.release_hash == record["release_hash"]


def test_start_command_permit_is_durable_and_returned_at_most_once():
    coordinator, journal = _coordinator()
    release = _start_release(coordinator)
    before = len(journal.records)
    permit = coordinator.receive_release("A", release)
    assert isinstance(permit, CommandPermit)
    assert permit.command == "START_TRADE"
    assert len(journal.records) == before + 1
    assert journal.records[-1]["event"] == "command_permit_issued"
    assert journal.records[-1]["command"] == "START_TRADE"
    assert coordinator.receive_release("A", release) is None
    assert coordinator.receive_release("B", release).command == "START_TRADE"
    assert coordinator.state == "START_RELEASED"


def test_permit_consumption_and_execution_are_separately_durable_and_one_shot():
    coordinator, journal = _coordinator()
    trade_id, epoch = _select_both(coordinator)
    permit = coordinator.issue_selection_permit("A")

    assert permit.command == "SET_MONS_TO_TRADE"
    assert permit.selection_side == "B"
    assert permit.slot == 1
    assert permit.slot_digest == coordinator.snapshot_summary("B")["slot_digests"][1]
    assert journal.records[permit.issued_seq]["event"] == "command_permit_issued"
    assert coordinator.consume_permit(permit) is True
    assert coordinator.is_consumed(permit)
    assert coordinator.consume_permit(permit) is False
    assert coordinator.permit_status(permit) == "consumed"

    coordinator.command_executed(permit, local_state="H_CONFIRM")
    assert coordinator.permit_status(permit) == "executed"
    assert journal.records[-1]["event"] == "command_executed"
    assert coordinator.command_executed(permit, local_state="H_CONFIRM") is False
    assert coordinator.command_observed(permit, engine_event="INIT_BLOCK") is True
    assert coordinator.command_observed(permit, engine_event="INIT_BLOCK") is False
    assert coordinator.permit_status(permit) == "observed"
    with pytest.raises(ProtocolError, match="observation changed"):
        coordinator.command_observed(permit, engine_event="READY_FINISH_TRADE")
    with pytest.raises(TradeCoordinatorError, match="unknown, stale, or has been altered"):
        coordinator.consume_permit(replace(permit, slot=0))
    assert coordinator.trade_id == trade_id
    assert coordinator.selection_epoch == epoch


def test_unconsumed_selection_permit_is_revoked_on_cancel_and_cannot_be_replayed():
    coordinator, journal = _coordinator()
    trade_id, epoch = _select_both(coordinator)
    permit = coordinator.issue_selection_permit("A")

    coordinator.cancel("A", trade_id, epoch, reason="user_cancel")
    assert coordinator.permit_status(permit) == "revoked"
    assert journal.records[-1]["event"] == "command_permit_revoked"
    assert coordinator.consume_permit(permit) is False


def test_finish_release_needs_both_real_animation_events_and_durable_acks():
    coordinator, _journal = _coordinator()
    start_release = _start_release(coordinator)
    coordinator.receive_release("A", start_release)
    coordinator.receive_release("B", start_release)
    coordinator.animation_finished("A", start_release.trade_id,
                                    start_release.selection_epoch, "11" * 32)
    with pytest.raises(TradeCoordinatorError, match="not allowed"):
        coordinator.create_decision("finish")
    coordinator.animation_finished("B", start_release.trade_id,
                                    start_release.selection_epoch, "22" * 32)
    decision = coordinator.create_decision("finish")
    coordinator.decision_stored("A", "finish", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    with pytest.raises(TradeCoordinatorError, match="not authorized"):
        coordinator.create_release("finish")
    coordinator.decision_stored("B", "finish", decision.trade_id,
                                 decision.selection_epoch, decision.decision_hash)
    finish_release = coordinator.create_release("finish")
    assert coordinator.receive_release("A", finish_release).command == "CONFIRM_FINISH_TRADE"
    assert coordinator.receive_release("A", finish_release) is None
    assert coordinator.receive_release("B", finish_release).command == "CONFIRM_FINISH_TRADE"


def test_coordinator_replies_match_the_strict_wire_schemas():
    coordinator, _journal = _coordinator()
    for side, snapshot_id in (("A", SNAPSHOT_A), ("B", SNAPSHOT_B)):
        _assert_wire_reply(coordinator.snapshot_ready(
            side, snapshot_id, _blocks(1 if side == "A" else 20)))
    _assert_wire_reply(coordinator.acknowledge_peer_snapshot(
        "A", SNAPSHOT_B, coordinator.snapshot_summary("B")["digest"]))
    _assert_wire_reply(coordinator.acknowledge_peer_snapshot(
        "B", SNAPSHOT_A, coordinator.snapshot_summary("A")["digest"]))

    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    for side, snapshot_id, slot in (("A", SNAPSHOT_A, 0), ("B", SNAPSHOT_B, 1)):
        slot_digest = coordinator.snapshot_summary(side)["slot_digests"][slot]
        _assert_wire_reply(coordinator.select(
            side, trade_id, epoch, snapshot_id, slot, slot_digest))
    for side in ("A", "B"):
        _assert_wire_reply(coordinator.confirm(side, trade_id, epoch, True))
    _assert_wire_reply({"type": "PREPARE", **coordinator.prepare_payload()})
    for side in ("A", "B"):
        _assert_wire_reply(coordinator.prepared(
            side, trade_id, epoch, coordinator.selection_hash))

    start = coordinator.create_decision("start")
    _assert_wire_reply({"type": "DECISION", **start.as_payload()})
    for side in ("A", "B"):
        _assert_wire_reply(coordinator.decision_stored(
            side, "start", trade_id, epoch, start.decision_hash))
    start_release = coordinator.create_release("start")
    _assert_wire_reply({"type": "RELEASE", **start_release.as_payload()})
    for side in ("A", "B"):
        coordinator.receive_release(side, start_release)
        _assert_wire_reply(coordinator.animation_finished(
            side, trade_id, epoch, "11" * 32 if side == "A" else "22" * 32))

    finish = coordinator.create_decision("finish")
    _assert_wire_reply({"type": "DECISION", **finish.as_payload()})
    for side in ("A", "B"):
        _assert_wire_reply(coordinator.decision_stored(
            side, "finish", trade_id, epoch, finish.decision_hash))
    finish_release = coordinator.create_release("finish")
    _assert_wire_reply({"type": "RELEASE", **finish_release.as_payload()})
    for side in ("A", "B"):
        coordinator.receive_release(side, finish_release)
        for index, milestone in enumerate((
                "save_started", "barrier", "party_refresh_started", "save_complete")):
            _assert_wire_reply(coordinator.save_progress(
                side, trade_id, epoch, milestone))
            if index == 0:
                _assert_wire_reply(coordinator.save_progress(
                    side, trade_id, epoch, milestone))
        _assert_wire_reply(coordinator.post_trade_snapshot(
            side, trade_id, epoch,
            "d4" * 16 if side == "A" else "e5" * 16,
            "f6" * 32 if side == "A" else "07" * 32))


def test_cancel_and_reset_replies_match_the_strict_wire_schemas():
    coordinator, _journal = _coordinator()
    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    _assert_wire_reply(coordinator.cancel("A", trade_id, epoch))
    _assert_wire_reply(coordinator.menu_ready("A", trade_id, epoch))
    _assert_wire_reply(coordinator.menu_ready("B", trade_id, epoch))


def test_rejection_and_post_release_cancel_replies_match_the_strict_wire_schemas():
    coordinator, _journal = _coordinator()
    trade_id, epoch = _select_both(coordinator)
    _assert_wire_reply(coordinator.confirm("A", trade_id, epoch, True))
    _assert_wire_reply(coordinator.confirm("B", trade_id, epoch, False))

    coordinator, _journal = _coordinator()
    release = _start_release(coordinator)
    _assert_wire_reply(coordinator.cancel(
        "A", release.trade_id, release.selection_epoch))


def test_save_progress_is_ordered_and_two_snapshots_do_not_claim_verified_result():
    coordinator, _journal = _coordinator()
    release, _start_permits = _finish_release(coordinator)
    coordinator.receive_release("A", release)
    coordinator.receive_release("B", release)
    with pytest.raises(ProtocolError, match="expected save milestone"):
        coordinator.save_progress("A", release.trade_id, release.selection_epoch, "save_complete")
    for side in ("A", "B"):
        for milestone in ("save_started", "barrier", "party_refresh_started", "save_complete"):
            coordinator.save_progress(side, release.trade_id, release.selection_epoch, milestone)
        coordinator.post_trade_snapshot(side, release.trade_id, release.selection_epoch,
                                        "d4" * 16 if side == "A" else "e5" * 16,
                                        "f6" * 32 if side == "A" else "07" * 32)
    assert coordinator.state == "RESULT_PENDING"
    assert coordinator.report()["result_verified"] is False


def test_game_rejection_resets_only_after_both_sides_return_to_menu_and_invalidates_old_epoch():
    coordinator, _journal = _coordinator()
    old_trade_id, old_epoch = _select_both(coordinator)
    coordinator.confirm("A", old_trade_id, old_epoch, True)
    action = coordinator.confirm("B", old_trade_id, old_epoch, False)
    assert action["type"] == "CANCEL"
    assert coordinator.state == "RESETTING"
    coordinator.menu_ready("A", old_trade_id, old_epoch)
    assert coordinator.state == "RESETTING"
    coordinator.menu_ready("B", old_trade_id, old_epoch)
    assert coordinator.state == "SELECTING"
    assert coordinator.selection_epoch == old_epoch + 1
    assert coordinator.trade_id != old_trade_id
    assert coordinator.select("A", old_trade_id, old_epoch, SNAPSHOT_A, 0,
                              "0" * 64)["type"] == "STALE"


def test_exit_request_cancels_room_after_both_menu_acks():
    coordinator, _journal = _coordinator()
    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    coordinator.cancel("A", trade_id, epoch, exit_room=True)
    coordinator.menu_ready("A", trade_id, epoch)
    assert coordinator.state == "RESETTING"
    coordinator.menu_ready("B", trade_id, epoch)
    assert coordinator.state == "CANCELLED"


def test_disconnect_before_start_release_is_cancelled_but_after_release_is_in_doubt():
    coordinator, _journal = _coordinator()
    assert coordinator.disconnect("A", "lan_lost") == "CANCELLED"

    coordinator, _journal = _coordinator()
    _start_release(coordinator)
    assert coordinator.disconnect("B", "lan_lost") == "IN_DOUBT"


def test_cancel_after_release_cannot_revoke_authorization():
    coordinator, _journal = _coordinator()
    release = _start_release(coordinator)
    result = coordinator.cancel("A", release.trade_id, release.selection_epoch)
    assert result["type"] == "CLOSE_REQUEST" and result["reason"] == "in_doubt"
    assert coordinator.state == "IN_DOUBT"


def test_journal_failure_never_returns_a_game_command_permit():
    journal = MemoryJournal(fail_event="command_permit_issued")
    coordinator, _journal = _coordinator(journal=journal)
    release = _start_release(coordinator)
    with pytest.raises(TradeCoordinatorError, match="journal failed"):
        coordinator.receive_release("A", release)
    assert coordinator.state == "IN_DOUBT"
    assert not coordinator.report()["command_permits"]


def test_old_and_duplicate_decision_events_are_stale_or_idempotent():
    coordinator, _journal = _coordinator()
    trade_id, epoch = _prepared(coordinator)
    decision = coordinator.create_decision("start")
    coordinator.cancel("A", trade_id, epoch, reason="selection_changed")
    coordinator.menu_ready("A", trade_id, epoch)
    coordinator.menu_ready("B", trade_id, epoch)
    assert coordinator.decision_stored("A", "start", trade_id, epoch,
                                        decision.decision_hash)["type"] == "STALE"

    coordinator, _journal = _coordinator()
    trade_id, epoch = _prepared(coordinator)
    decision = coordinator.create_decision("start")
    first = coordinator.decision_stored("A", "start", trade_id, epoch,
                                        decision.decision_hash)
    repeated = coordinator.decision_stored("A", "start", trade_id, epoch,
                                           decision.decision_hash)
    assert first == repeated
    with pytest.raises(ProtocolError, match="does not match"):
        coordinator.decision_stored("B", "start", trade_id, epoch, "0" * 64)


def test_selection_confirmation_and_prepared_retransmits_are_idempotent_after_peer_progress():
    coordinator, _journal = _coordinator()
    trade_id, epoch = coordinator.trade_id, coordinator.selection_epoch
    digest_a = coordinator.snapshot_summary("A")["slot_digests"][0]
    digest_b = coordinator.snapshot_summary("B")["slot_digests"][1]
    first_a = coordinator.select("A", trade_id, epoch, SNAPSHOT_A, 0, digest_a)
    coordinator.select("B", trade_id, epoch, SNAPSHOT_B, 1, digest_b)
    assert coordinator.select("A", trade_id, epoch, SNAPSHOT_A, 0, digest_a) == first_a

    first_confirm_a = coordinator.confirm("A", trade_id, epoch, True)
    coordinator.confirm("B", trade_id, epoch, True)
    assert coordinator.confirm("A", trade_id, epoch, True) == first_confirm_a
    first_prepared_a = coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash)
    coordinator.prepared("B", trade_id, epoch, coordinator.selection_hash)
    assert coordinator.prepared("A", trade_id, epoch, coordinator.selection_hash) == first_prepared_a
