"""Bounded, authenticated JSON framing for the FRLG LAN probe.

This is a business-data protocol. It deliberately has no representation for RFU/Pia frames,
held-key rows, or arbitrary Python objects.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
from dataclasses import dataclass


PROTOCOL_VERSION = 1
_TRADE_DECISION_DOMAIN = b"pokeldn/frlg/remote/trade-decision/v1\0"
_TRADE_RELEASE_DOMAIN = b"pokeldn/frlg/remote/trade-release/v1\0"
MAX_FRAME_BYTES = 16 * 1024
AUTH_TAG_BYTES = hashlib.sha256().digest_size
MAX_BUFFER_BYTES = 4 * (MAX_FRAME_BYTES + 4 + AUTH_TAG_BYTES)
PHASE_SIZES = {
    "link_player": 200,
    "trainer_card": 100,
    "party_0": 200,
    "party_1": 200,
    "party_2": 200,
    "mail": 220,
    "ribbons": 40,
}
PHASE_ORDER = tuple(PHASE_SIZES)
MESSAGE_TYPES = {
    "HELLO", "AUTH", "ROOM_READY", "ROOM_READY_ACK", "LOCAL_LINK_STATE",
    "PEER_BLOCK", "SNAPSHOT_READY", "PING", "PONG", "ERROR", "PROBE_STOP",
    "PHASE_READY", "SELECT", "SELECT_ACK", "CONFIRM", "REJECT", "CANCEL",
    "PREPARE", "PREPARED", "DECISION", "DECISION_STORED", "RELEASE",
    "ANIMATION_FINISHED", "SAVE_PROGRESS", "POST_TRADE_SNAPSHOT", "RESULT",
    "RESULT_ACK", "CLOSE_REQUEST", "CLOSE_RESULT", "SNAPSHOT_ACK",
    "SNAPSHOT_ACK_ACK", "CONFIRM_ACK", "PREPARED_ACK", "DECISION_STORED_ACK",
    "ANIMATION_FINISHED_ACK", "SAVE_PROGRESS_ACK", "POST_TRADE_SNAPSHOT_ACK",
    "CANCEL_ACK", "MENU_READY", "MENU_READY_ACK",
}
TRADE_MESSAGE_TYPES = frozenset({
    "PHASE_READY", "SELECT", "SELECT_ACK", "CONFIRM", "REJECT", "CANCEL",
    "PREPARE", "PREPARED", "DECISION", "DECISION_STORED", "RELEASE",
    "ANIMATION_FINISHED", "SAVE_PROGRESS", "POST_TRADE_SNAPSHOT", "RESULT",
    "RESULT_ACK", "CLOSE_REQUEST", "CLOSE_RESULT", "SNAPSHOT_ACK",
    "SNAPSHOT_ACK_ACK", "CONFIRM_ACK", "PREPARED_ACK", "DECISION_STORED_ACK",
    "ANIMATION_FINISHED_ACK", "SAVE_PROGRESS_ACK", "POST_TRADE_SNAPSHOT_ACK",
    "CANCEL_ACK", "MENU_READY", "MENU_READY_ACK",
})
LINK_STATES = {"waiting", "connected", "closing", "closed", "failed"}
ERROR_CODES = {
    "protocol_mismatch", "room_mismatch", "phase_mismatch", "queue_full",
    "link_lost", "local_link_failed", "invalid_data",
}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class LanMessage:
    protocol_version: int
    room_id: str
    run_id: str
    sender_id: str
    seq: int
    ack_seq: int
    type: str
    phase: str | None
    payload: dict

    def as_dict(self):
        return {
            "protocol_version": self.protocol_version,
            "room_id": self.room_id,
            "run_id": self.run_id,
            "sender_id": self.sender_id,
            "seq": self.seq,
            "ack_seq": self.ack_seq,
            "type": self.type,
            "phase": self.phase,
            "payload": self.payload,
        }


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def trade_decision_hash(run_id, trade_id, selection_epoch, kind, selection_hash,
                        *, prepared_sides=(), finished_sides=(), event_digests=None):
    facts = {"selection_hash": selection_hash}
    if kind == "start":
        facts["prepared_sides"] = list(prepared_sides)
    elif kind == "finish":
        facts["finished_sides"] = list(finished_sides)
        facts["event_digests"] = dict(event_digests or {})
    else:
        raise ValueError("trade decision kind must be start or finish")
    body = {"run_id": run_id, "trade_id": trade_id,
            "selection_epoch": selection_epoch, "kind": kind, **facts}
    return hashlib.sha256(_TRADE_DECISION_DOMAIN + _canonical_json(body)).hexdigest()


def trade_release_hash(run_id, trade_id, selection_epoch, kind, decision_hash):
    body = {"run_id": run_id, "trade_id": trade_id,
            "selection_epoch": selection_epoch, "kind": kind,
            "decision_hash": decision_hash}
    return hashlib.sha256(_TRADE_RELEASE_DOMAIN + _canonical_json(body)).hexdigest()


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _is_hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and re.fullmatch(r"[0-9a-fA-F]+", value) is not None)


def _valid_bridge_name(value):
    return (isinstance(value, str) and 1 <= len(value) <= 7
            and value.isascii() and value.isprintable())


def _valid_trade_base(payload, *, extra=()):
    required = {"trade_id", "selection_epoch", *extra}
    if set(payload) != required:
        return False
    return (_is_hex(payload["trade_id"], 32)
            and type(payload["selection_epoch"]) is int
            and 0 <= payload["selection_epoch"] <= 0x7FFFFFFF)


def _valid_slot(value):
    return type(value) is int and 0 <= value <= 5


def _valid_hash(value):
    return _is_hex(value, 64)


def logical_bytes(phase, raw):
    """Return bytes used for the phase digest; block padding is never included."""
    if not isinstance(phase, str) or phase not in PHASE_SIZES:
        raise ProtocolError(f"unknown data phase: {phase!r}")
    data = bytes(raw)
    if len(data) != PHASE_SIZES[phase]:
        raise ProtocolError(f"{phase} block must be {PHASE_SIZES[phase]} bytes")
    return data[:60] if phase == "link_player" else data


def validate_message(value):
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object")
    required = {"protocol_version", "room_id", "run_id", "sender_id", "seq",
                "ack_seq", "type", "phase", "payload"}
    if set(value) != required:
        raise ProtocolError("message fields do not match the protocol schema")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol_version")
    if not _is_hex(value["room_id"], 32) or not _is_hex(value["run_id"], 32):
        raise ProtocolError("room_id and run_id must be 128-bit hex identifiers")
    if value["sender_id"] not in ("host", "join"):
        raise ProtocolError("sender_id must be host or join")
    for name in ("seq", "ack_seq"):
        if type(value[name]) is not int or not 0 <= value[name] <= 0x7FFFFFFF:
            raise ProtocolError(f"{name} must be a non-negative 31-bit sequence")
    message_type = value["type"]
    if not isinstance(message_type, str):
        raise ProtocolError("type must be a string")
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError(f"unknown message type: {message_type!r}")
    phase = value["phase"]
    if phase is not None and (not isinstance(phase, str) or phase not in PHASE_SIZES):
        raise ProtocolError(f"unknown phase: {phase!r}")
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")

    if message_type == "HELLO":
        if (phase is not None or set(payload) != {"challenge", "capabilities", "bridge_name"}
                or not _is_hex(payload["challenge"], 64)
                or payload["capabilities"] != ["frlg-direct-trade-probe-v1"]
                or not _valid_bridge_name(payload["bridge_name"])):
            raise ProtocolError("invalid HELLO payload")
    elif message_type == "AUTH":
        if (phase is not None or set(payload) != {
                "challenge_echo", "nonce", "capabilities", "bridge_name"}
                or not _is_hex(payload["challenge_echo"], 64)
                or not _is_hex(payload["nonce"], 64)
                or payload["capabilities"] != ["frlg-direct-trade-probe-v1"]
                or not _valid_bridge_name(payload["bridge_name"])):
            raise ProtocolError("invalid AUTH payload")
    elif message_type == "ROOM_READY":
        if (phase is not None or set(payload) != {"peer_nonce", "transcript"}
                or not _is_hex(payload["peer_nonce"], 64)
                or not _is_hex(payload["transcript"], 64)):
            raise ProtocolError("invalid ROOM_READY payload")
    elif message_type == "ROOM_READY_ACK":
        if phase is not None or set(payload) != {"transcript"} or not _is_hex(payload["transcript"], 64):
            raise ProtocolError("invalid ROOM_READY_ACK payload")
    elif message_type == "LOCAL_LINK_STATE":
        if (phase is not None or set(payload) != {"state"}
                or not isinstance(payload["state"], str)
                or payload["state"] not in LINK_STATES):
            raise ProtocolError("invalid LOCAL_LINK_STATE payload")
    elif message_type == "PEER_BLOCK":
        if (not isinstance(phase, str) or phase not in PHASE_SIZES
                or set(payload) != {"data", "digest"}
                or not _is_hex(payload["digest"], 64)
                or not isinstance(payload["data"], str)):
            raise ProtocolError("invalid PEER_BLOCK payload")
        try:
            data = base64.b64decode(payload["data"], validate=True)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise ProtocolError("PEER_BLOCK data is not valid Base64") from exc
        if len(data) != PHASE_SIZES[phase]:
            raise ProtocolError(f"{phase} data has the wrong logical length")
        expected = hashlib.sha256(logical_bytes(phase, data)).hexdigest()
        if not hmac.compare_digest(payload["digest"], expected):
            raise ProtocolError(f"{phase} data digest mismatch")
    elif message_type == "SNAPSHOT_READY":
        if (phase is not None or set(payload) != {"snapshot_id", "digest", "phases"}
                or not _is_hex(payload["snapshot_id"], 32)
                or not _is_hex(payload["digest"], 64)
                or payload["phases"] != list(PHASE_ORDER)):
            raise ProtocolError("invalid SNAPSHOT_READY payload")
    elif message_type in ("SNAPSHOT_ACK", "SNAPSHOT_ACK_ACK"):
        if (phase is not None or set(payload) != {"snapshot_id", "digest"}
                or not _is_hex(payload["snapshot_id"], 32)
                or not _valid_hash(payload["digest"])):
            raise ProtocolError(f"invalid {message_type} payload")
    elif message_type in ("PING", "PONG"):
        if phase is not None or set(payload) != {"time_ns"} or type(payload["time_ns"]) is not int or payload["time_ns"] < 0:
            raise ProtocolError(f"invalid {message_type} payload")
    elif message_type == "ERROR":
        if (phase is not None or set(payload) != {"code"}
                or not isinstance(payload["code"], str)
                or payload["code"] not in ERROR_CODES):
            raise ProtocolError("invalid ERROR payload")
    elif message_type == "PROBE_STOP":
        if (phase is not None or set(payload) != {"reason"}
                or not isinstance(payload["reason"], str) or payload["reason"] not in {
                    "cancelled", "completed", "in_doubt", "link_failed"}):
            raise ProtocolError("invalid PROBE_STOP payload")
    elif message_type == "PHASE_READY":
        if (phase not in PHASE_ORDER
                or not _valid_trade_base(payload, extra={"digest"})
                or not _valid_hash(payload["digest"])):
            raise ProtocolError("invalid PHASE_READY payload")
    elif message_type == "SELECT":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"snapshot_id", "slot", "slot_digest"})
                or not _is_hex(payload["snapshot_id"], 32)
                or not _valid_slot(payload["slot"])
                or not _valid_hash(payload["slot_digest"])):
            raise ProtocolError("invalid SELECT payload")
    elif message_type == "SELECT_ACK":
        valid = (_valid_trade_base(payload)
                 or _valid_trade_base(payload, extra={"selection_hash"}))
        if (phase is not None or not valid
                or ("selection_hash" in payload
                    and not _valid_hash(payload["selection_hash"]))):
            raise ProtocolError("invalid SELECT_ACK payload")
    elif message_type == "CONFIRM":
        if (phase is not None or not _valid_trade_base(payload, extra={"accepted"})
                or type(payload["accepted"]) is not bool):
            raise ProtocolError("invalid CONFIRM payload")
    elif message_type == "CONFIRM_ACK":
        if (phase is not None or not _valid_trade_base(payload, extra={"accepted"})
                or type(payload["accepted"]) is not bool):
            raise ProtocolError("invalid CONFIRM_ACK payload")
    elif message_type == "REJECT":
        if (phase is not None or not _valid_trade_base(payload, extra={"reason"})
                or not isinstance(payload["reason"], str) or payload["reason"] not in {
                    "empty_slot", "game_rejected", "unsupported_mon",
                    "snapshot_mismatch", "user_cancel"}):
            raise ProtocolError("invalid REJECT payload")
    elif message_type == "CANCEL":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"reason", "next_selection_epoch", "exit_room"})
                or not isinstance(payload["reason"], str) or payload["reason"] not in {
                    "user_cancel", "game_rejected", "selection_changed", "peer_exit"}
                or type(payload["next_selection_epoch"]) is not int
                or payload["next_selection_epoch"] != payload["selection_epoch"] + 1
                or payload["next_selection_epoch"] > 0x7FFFFFFF
                or type(payload["exit_room"]) is not bool):
            raise ProtocolError("invalid CANCEL payload")
    elif message_type in ("PREPARE", "PREPARED"):
        if message_type == "PREPARED":
            valid = (_valid_trade_base(payload, extra={"selection_hash"})
                     and _valid_hash(payload["selection_hash"]))
        else:
            fields = {"selection_hash", "snapshot_a_id", "snapshot_a_digest",
                      "snapshot_b_id", "snapshot_b_digest", "slot_a", "slot_a_digest",
                      "slot_b", "slot_b_digest"}
            valid = _valid_trade_base(payload, extra=fields)
            if valid:
                valid = (
                    _valid_hash(payload["selection_hash"])
                    and _is_hex(payload["snapshot_a_id"], 32)
                    and _valid_hash(payload["snapshot_a_digest"])
                    and _is_hex(payload["snapshot_b_id"], 32)
                    and _valid_hash(payload["snapshot_b_digest"])
                    and _valid_slot(payload["slot_a"])
                    and _valid_hash(payload["slot_a_digest"])
                    and _valid_slot(payload["slot_b"])
                    and _valid_hash(payload["slot_b_digest"]))
        if phase is not None or not valid:
            raise ProtocolError(f"invalid {message_type} payload")
    elif message_type == "PREPARED_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"selection_hash"})
                or not _valid_hash(payload["selection_hash"])):
            raise ProtocolError("invalid PREPARED_ACK payload")
    elif message_type in ("DECISION", "DECISION_STORED", "RELEASE"):
        kind = payload.get("kind")
        if not isinstance(kind, str) or kind not in ("start", "finish"):
            raise ProtocolError(f"invalid {message_type} decision kind")
        if message_type == "DECISION":
            extra = {"kind", "decision_hash", "selection_hash"}
            if kind == "start":
                extra.add("prepared_sides")
            else:
                extra.update({"finished_sides", "event_digests"})
            valid = _valid_trade_base(payload, extra=extra)
            if valid:
                valid = (_valid_hash(payload["decision_hash"])
                         and _valid_hash(payload["selection_hash"]))
                if kind == "start":
                    valid = valid and payload["prepared_sides"] == ["A", "B"]
                else:
                    event_digests = payload["event_digests"]
                    valid = (valid and payload["finished_sides"] == ["A", "B"]
                             and isinstance(event_digests, dict)
                             and set(event_digests) == {"A", "B"}
                             and all(_valid_hash(item) for item in event_digests.values()))
                if valid:
                    expected_decision_hash = trade_decision_hash(
                        value["run_id"], payload["trade_id"], payload["selection_epoch"],
                        kind, payload["selection_hash"],
                        prepared_sides=payload.get("prepared_sides", ()),
                        finished_sides=payload.get("finished_sides", ()),
                        event_digests=payload.get("event_digests"))
                    valid = hmac.compare_digest(payload["decision_hash"],
                                                expected_decision_hash)
        elif message_type == "DECISION_STORED":
            valid = (_valid_trade_base(payload, extra={"kind", "decision_hash"})
                     and _valid_hash(payload["decision_hash"]))
        else:
            valid = (_valid_trade_base(payload, extra={"kind", "decision_hash", "release_hash"})
                     and _valid_hash(payload["decision_hash"])
                     and _valid_hash(payload["release_hash"]))
            if valid:
                expected_release_hash = trade_release_hash(
                    value["run_id"], payload["trade_id"], payload["selection_epoch"],
                    kind, payload["decision_hash"])
                valid = hmac.compare_digest(payload["release_hash"], expected_release_hash)
        if phase is not None or not valid:
            raise ProtocolError(f"invalid {message_type} payload")
    elif message_type == "DECISION_STORED_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"kind", "decision_hash"})
                or not isinstance(payload["kind"], str)
                or payload["kind"] not in ("start", "finish")
                or not _valid_hash(payload["decision_hash"])):
            raise ProtocolError("invalid DECISION_STORED_ACK payload")
    elif message_type == "ANIMATION_FINISHED":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"event_digest"})
                or not _valid_hash(payload["event_digest"])):
            raise ProtocolError("invalid ANIMATION_FINISHED payload")
    elif message_type == "ANIMATION_FINISHED_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"event_digest"})
                or not _valid_hash(payload["event_digest"])):
            raise ProtocolError("invalid ANIMATION_FINISHED_ACK payload")
    elif message_type == "SAVE_PROGRESS":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"milestone", "progress_index"})
                or not isinstance(payload["milestone"], str) or payload["milestone"] not in {
                    "save_started", "barrier", "party_refresh_started", "save_complete"}
                or type(payload["progress_index"]) is not int
                or not 0 <= payload["progress_index"] <= 0x7FFFFFFF):
            raise ProtocolError("invalid SAVE_PROGRESS payload")
    elif message_type == "SAVE_PROGRESS_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"milestone", "progress_index"})
                or not isinstance(payload["milestone"], str)
                or payload["milestone"] not in {
                    "save_started", "barrier", "party_refresh_started", "save_complete"}
                or type(payload["progress_index"]) is not int
                or not 0 <= payload["progress_index"] <= 0x7FFFFFFF):
            raise ProtocolError("invalid SAVE_PROGRESS_ACK payload")
    elif message_type == "POST_TRADE_SNAPSHOT":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"snapshot_id", "digest"})
                or not _is_hex(payload["snapshot_id"], 32)
                or not _valid_hash(payload["digest"])):
            raise ProtocolError("invalid POST_TRADE_SNAPSHOT payload")
    elif message_type == "POST_TRADE_SNAPSHOT_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"snapshot_id", "digest"})
                or not _is_hex(payload["snapshot_id"], 32)
                or not _valid_hash(payload["digest"])):
            raise ProtocolError("invalid POST_TRADE_SNAPSHOT_ACK payload")
    elif message_type in ("RESULT", "RESULT_ACK"):
        fields = {"result_id", "digest", "validator_version"}
        if message_type == "RESULT_ACK":
            fields = {"result_id", "digest"}
        valid = _valid_trade_base(payload, extra=fields)
        if valid:
            valid = (_is_hex(payload["result_id"], 32)
                     and _valid_hash(payload["digest"]))
            if message_type == "RESULT":
                valid = valid and type(payload["validator_version"]) is int \
                    and 1 <= payload["validator_version"] <= 0x7FFFFFFF
        if phase is not None or not valid:
            raise ProtocolError(f"invalid {message_type} payload")
    elif message_type == "CLOSE_REQUEST":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"reason"})
                or not isinstance(payload["reason"], str) or payload["reason"] not in {
                    "completed", "cancelled", "in_doubt", "local_failure"}):
            raise ProtocolError("invalid CLOSE_REQUEST payload")
    elif message_type == "CLOSE_RESULT":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"state", "close_confirmed"})
                or not isinstance(payload["state"], str)
                or payload["state"] not in {"closed", "failed", "in_doubt"}
                or type(payload["close_confirmed"]) is not bool):
            raise ProtocolError("invalid CLOSE_RESULT payload")
    elif message_type == "CANCEL_ACK":
        if (phase is not None
                or not _valid_trade_base(payload, extra={"reason", "next_selection_epoch"})
                or not isinstance(payload["reason"], str)
                or payload["reason"] not in {
                    "user_cancel", "game_rejected", "selection_changed", "peer_exit"}
                or type(payload["next_selection_epoch"]) is not int
                or payload["next_selection_epoch"] != payload["selection_epoch"] + 1
                or payload["next_selection_epoch"] > 0x7FFFFFFF):
            raise ProtocolError("invalid CANCEL_ACK payload")
    elif message_type in ("MENU_READY", "MENU_READY_ACK"):
        if (phase is not None
                or not _valid_trade_base(payload, extra={"next_selection_epoch"})
                or type(payload["next_selection_epoch"]) is not int
                or payload["next_selection_epoch"] != payload["selection_epoch"] + 1
                or payload["next_selection_epoch"] > 0x7FFFFFFF):
            raise ProtocolError(f"invalid {message_type} payload")
    return LanMessage(**value)


def encode_frame(message, key):
    message = validate_message(message.as_dict() if isinstance(message, LanMessage) else message)
    body = _canonical_json(message.as_dict())
    length = len(body) + AUTH_TAG_BYTES
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds 16 KiB")
    length_prefix = struct.pack(">I", length)
    tag = hmac.new(bytes(key), length_prefix + body, hashlib.sha256).digest()
    return length_prefix + body + tag


def decode_payload(frame_payload, key):
    if len(frame_payload) < AUTH_TAG_BYTES or len(frame_payload) > MAX_FRAME_BYTES:
        raise ProtocolError("invalid frame payload length")
    body, tag = frame_payload[:-AUTH_TAG_BYTES], frame_payload[-AUTH_TAG_BYTES:]
    length_prefix = struct.pack(">I", len(frame_payload))
    expected = hmac.new(bytes(key), length_prefix + body, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ProtocolError("frame authentication failed")
    try:
        decoded = json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                             parse_constant=lambda _value: (_ for _ in ()).throw(
                                 ProtocolError("non-finite JSON number")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("frame is not valid UTF-8 JSON") from exc
    return validate_message(decoded)


class FrameDecoder:
    """Incrementally decode length-prefixed frames; handles split and coalesced TCP reads."""

    def __init__(self, key):
        self._key = bytes(key)
        self._buffer = bytearray()

    def feed(self, data):
        if len(self._buffer) + len(data) > MAX_BUFFER_BYTES:
            raise ProtocolError("receive buffer limit exceeded")
        self._buffer.extend(data)
        messages = []
        while len(self._buffer) >= 4:
            length = struct.unpack(">I", self._buffer[:4])[0]
            if length < AUTH_TAG_BYTES or length > MAX_FRAME_BYTES:
                raise ProtocolError("frame length is outside the allowed range")
            total = 4 + length
            if len(self._buffer) < total:
                break
            frame = bytes(self._buffer[4:total])
            del self._buffer[:total]
            messages.append(decode_payload(frame, self._key))
        return messages

    def eof(self):
        if self._buffer:
            raise ProtocolError("EOF inside a LAN frame")
