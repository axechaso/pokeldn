import socket

import pytest

from pokeldn.frlg.remote.config import RemoteTradeConfig
from pokeldn.frlg.remote.protocol import (
    LanMessage, ProtocolError, decode_payload, encode_frame, trade_decision_hash,
    trade_release_hash, validate_message,
)
from pokeldn.frlg.remote.transport import RemoteTransport, TransportError


KEY = b"p1-trade-protocol-test-key-32byte"
ROOM = "12" * 16
RUN = "34" * 16
TRADE = "56" * 16
HASH = "78" * 32
EVENT_DIGESTS = {"A": "91" * 32, "B": "a2" * 32}
START_DECISION_HASH = trade_decision_hash(
    RUN, TRADE, 2, "start", HASH, prepared_sides=["A", "B"])
FINISH_DECISION_HASH = trade_decision_hash(
    RUN, TRADE, 2, "finish", HASH, finished_sides=["A", "B"],
    event_digests=EVENT_DIGESTS)
FINISH_RELEASE_HASH = trade_release_hash(
    RUN, TRADE, 2, "finish", FINISH_DECISION_HASH)


def _message(kind, payload, *, phase=None):
    return LanMessage(1, ROOM, RUN, "host", 3, 2, kind, phase, payload)


def _base(**fields):
    return {"trade_id": TRADE, "selection_epoch": 2, **fields}


@pytest.mark.parametrize("message_type,payload,phase", [
    ("PHASE_READY", _base(digest=HASH), "party_1"),
    ("SELECT", _base(snapshot_id=ROOM, slot=3, slot_digest=HASH), None),
    ("SELECT_ACK", _base(selection_hash=HASH), None),
    ("CONFIRM", _base(accepted=True), None),
    ("REJECT", _base(reason="game_rejected"), None),
    ("CANCEL", _base(reason="selection_changed", next_selection_epoch=3,
                      exit_room=False), None),
    ("PREPARE", _base(selection_hash=HASH, snapshot_a_id=ROOM,
                       snapshot_a_digest=HASH, snapshot_b_id=RUN,
                       snapshot_b_digest=HASH, slot_a=0, slot_a_digest=HASH,
                       slot_b=5, slot_b_digest=HASH), None),
    ("PREPARED", _base(selection_hash=HASH), None),
    ("DECISION", _base(kind="start", decision_hash=START_DECISION_HASH,
                        selection_hash=HASH, prepared_sides=["A", "B"]), None),
    ("DECISION", _base(kind="finish", decision_hash=FINISH_DECISION_HASH,
                        selection_hash=HASH, finished_sides=["A", "B"],
                        event_digests=EVENT_DIGESTS), None),
    ("DECISION_STORED", _base(kind="start", decision_hash=START_DECISION_HASH), None),
    ("DECISION_STORED_ACK", _base(kind="finish", decision_hash=FINISH_DECISION_HASH), None),
    ("RELEASE", _base(kind="finish", decision_hash=FINISH_DECISION_HASH,
                       release_hash=FINISH_RELEASE_HASH), None),
    ("SELECT_ACK", _base(), None),
    ("CONFIRM_ACK", _base(accepted=True), None),
    ("PREPARED_ACK", _base(selection_hash=HASH), None),
    ("ANIMATION_FINISHED_ACK", _base(event_digest=HASH), None),
    ("SAVE_PROGRESS_ACK", _base(milestone="barrier", progress_index=1), None),
    ("POST_TRADE_SNAPSHOT_ACK", _base(snapshot_id=ROOM, digest=HASH), None),
    ("CANCEL_ACK", _base(reason="user_cancel", next_selection_epoch=3), None),
    ("MENU_READY_ACK", _base(next_selection_epoch=3), None),
    ("ANIMATION_FINISHED", _base(event_digest=HASH), None),
    ("SAVE_PROGRESS", _base(milestone="barrier", progress_index=2), None),
    ("POST_TRADE_SNAPSHOT", _base(snapshot_id=ROOM, digest=HASH), None),
    ("RESULT", _base(result_id=ROOM, digest=HASH, validator_version=1), None),
    ("RESULT_ACK", _base(result_id=ROOM, digest=HASH), None),
    ("CLOSE_REQUEST", _base(reason="in_doubt"), None),
    ("CLOSE_RESULT", _base(state="closed", close_confirmed=True), None),
])
def test_p1_message_schemas_round_trip_as_authenticated_frames(message_type, payload, phase):
    message = _message(message_type, payload, phase=phase)
    frame = encode_frame(message, KEY)
    assert decode_payload(frame[4:], KEY) == message


@pytest.mark.parametrize("message_type,payload,phase", [
    ("SELECT", _base(snapshot_id=ROOM, slot=True, slot_digest=HASH), None),
    ("SELECT", _base(snapshot_id=ROOM, slot=6, slot_digest=HASH), None),
    ("CONFIRM", _base(accepted=1), None),
    ("CANCEL", _base(reason="user_cancel", next_selection_epoch=9,
                      exit_room=False), None),
    ("PREPARED", _base(selection_hash="not-a-hash"), None),
    ("DECISION", _base(kind="resume", decision_hash=HASH,
                        selection_hash=HASH), None),
    ("DECISION", _base(kind=[], decision_hash=HASH,
                        selection_hash=HASH), None),
    ("RELEASE", _base(kind="start", decision_hash=HASH,
                       release_hash=HASH, extra="not-allowed"), None),
    ("ANIMATION_FINISHED", _base(event_digest="raw-block"), None),
    ("SAVE_PROGRESS", _base(milestone="commit", progress_index=1), None),
    ("RESULT", _base(result_id=ROOM, digest=HASH, validator_version=True), None),
    ("CLOSE_RESULT", _base(state="completed", close_confirmed=True), None),
])
def test_p1_message_schemas_reject_malformed_or_extra_fields(message_type, payload, phase):
    with pytest.raises(ProtocolError):
        validate_message(_message(message_type, payload, phase=phase).as_dict())


@pytest.mark.parametrize("message_type,payload", [
    ("DECISION", _base(kind="start", decision_hash=HASH, selection_hash=HASH,
                        prepared_sides=["A", "B"])),
    ("RELEASE", _base(kind="finish", decision_hash=FINISH_DECISION_HASH,
                       release_hash=HASH)),
])
def test_decision_and_release_hashes_bind_their_canonical_facts(message_type, payload):
    with pytest.raises(ProtocolError):
        validate_message(_message(message_type, payload).as_dict())


def test_probe_transport_cannot_send_p1_trade_control_messages():
    transport = RemoteTransport(RemoteTradeConfig(
        role="host", listen="127.0.0.1", room_key=KEY))
    left, right = socket.socketpair()
    transport._sock = left
    try:
        with pytest.raises(TransportError, match="disabled by the P0-only"):
            transport.send("RELEASE", _base(kind="finish",
                                               decision_hash=FINISH_DECISION_HASH,
                                               release_hash=FINISH_RELEASE_HASH))
        assert transport._outbound.empty()
    finally:
        transport.close()
        right.close()
