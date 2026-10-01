"""Socket-free P1 state machine for one human-approved FRLG trade."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets

from pokeldn.frlg.remote.protocol import (
    PHASE_ORDER, ProtocolError, trade_decision_hash, trade_release_hash,
)
from pokeldn.frlg.remote.coordinator import snapshot_digest
from pokeldn.frlg.link import linkplayer
from pokeldn.frlg.save import mon as monmod


PEERS = ("A", "B")
TRADE_STATES = {
    "WAITING_SNAPSHOTS", "SELECTING", "CONFIRMING", "PREPARING",
    "START_DECISION_READY", "START_DECIDED", "START_RELEASE_READY", "START_RELEASED",
    "ANIMATING", "FINISH_DECISION_READY", "FINISH_DECIDED", "FINISH_RELEASE_READY",
    "FINISH_RELEASED", "SAVING", "RESULT_PENDING", "RESETTING", "CANCELLED", "IN_DOUBT",
}
_HEX_128 = re.compile(r"[0-9a-fA-F]{32}\Z")
_HEX_256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_DECISION_DOMAIN = b"pokeldn/frlg/remote/trade-decision/v1\0"
_CANCEL_REASONS = {"user_cancel", "game_rejected", "selection_changed", "peer_exit"}
_SAVE_MILESTONES = ("save_started", "barrier", "party_refresh_started", "save_complete")


class TradeCoordinatorError(RuntimeError):
    """A trade transition is invalid or the durable decision path failed."""


@dataclass(frozen=True)
class TradeDecision:
    run_id: str
    trade_id: str
    selection_epoch: int
    kind: str
    decision_hash: str
    selection_hash: str
    prepared_sides: tuple = ()
    finished_sides: tuple = ()
    event_digests: tuple = ()

    def as_payload(self):
        payload = {
            "kind": self.kind,
            "trade_id": self.trade_id,
            "selection_epoch": self.selection_epoch,
            "decision_hash": self.decision_hash,
            "selection_hash": self.selection_hash,
        }
        if self.kind == "start":
            payload["prepared_sides"] = list(self.prepared_sides)
        else:
            payload["finished_sides"] = list(self.finished_sides)
            payload["event_digests"] = dict(self.event_digests)
        return payload


@dataclass(frozen=True)
class TradeRelease:
    run_id: str
    trade_id: str
    selection_epoch: int
    kind: str
    decision_hash: str
    release_hash: str

    def as_payload(self):
        return {
            "kind": self.kind,
            "trade_id": self.trade_id,
            "selection_epoch": self.selection_epoch,
            "decision_hash": self.decision_hash,
            "release_hash": self.release_hash,
        }


@dataclass(frozen=True)
class CommandPermit:
    """One-shot command authorization returned only after its journal record is durable."""

    side: str
    run_id: str
    trade_id: str
    selection_epoch: int
    kind: str
    command: str
    release_hash: str


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _valid_hex(value, pattern, field):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        size = 32 if pattern is _HEX_128 else 64
        raise ProtocolError(f"{field} must be {size} hexadecimal characters")
    return value.lower()


class TradeCoordinator:
    """Coordinate one trade without sockets or access to mutable game-engine state.

    All methods are called from the coordinator/control path. Each state-changing event is
    synchronously journaled before memory state changes or a command permit is returned.
    A permit is at-most-once within this process; an interrupted run is never replayed.
    """

    def __init__(self, run_id, journal):
        if not isinstance(run_id, str) or _HEX_128.fullmatch(run_id) is None:
            raise ValueError("run_id must be a 128-bit hex identifier")
        if journal is None or not callable(getattr(journal, "append_durable", None)):
            raise ValueError("a durable trade journal is required")
        if getattr(journal, "run_id", run_id).lower() != run_id.lower():
            raise ValueError("trade journal run_id does not match the coordinator")
        self.run_id = run_id.lower()
        self.journal = journal
        self.state = "WAITING_SNAPSHOTS"
        self.selection_epoch = 0
        self.trade_id = None
        self._snapshots = {}
        self._snapshot_acks = set()
        self._selections = {}
        self._selection_hash = None
        self._confirmations = {}
        self._prepared = set()
        self._decisions = {}
        self._decision_stored = {"start": set(), "finish": set()}
        self._releases = {}
        self._command_permits = {}
        self._animation_finished = {}
        self._save_progress = {"A": set(), "B": set()}
        self._post_trade_snapshots = {}
        self._reset_ready = set()
        self._cancel_reasons = {}
        self._exit_requested = False
        self._fatal_error = None
        self._irreversible = False

    @staticmethod
    def _peer(side):
        if side not in PEERS:
            raise ProtocolError("side must be A or B")
        return side

    def _ensure_available(self):
        if self._fatal_error is not None:
            raise TradeCoordinatorError(self._fatal_error)

    def _record(self, event, **fields):
        self._ensure_available()
        try:
            return self.journal.append_durable(event, **fields)
        except Exception as exc:
            self._fatal_error = f"durable trade journal failed: {exc}"
            self.state = "IN_DOUBT" if self._irreversible else "CANCELLED"
            raise TradeCoordinatorError(self._fatal_error) from exc

    def _stale(self, event, side, trade_id, selection_epoch):
        self._record("stale_event", event_type=event, side=side,
                     trade_id=trade_id, selection_epoch=selection_epoch)
        return {"type": "STALE", "event_type": event,
                "trade_id": trade_id, "selection_epoch": selection_epoch}

    def _check_epoch(self, event, side, trade_id, selection_epoch):
        self._peer(side)
        if type(selection_epoch) is not int or selection_epoch < 0:
            raise ProtocolError("selection_epoch must be a non-negative integer")
        trade_id = _valid_hex(trade_id, _HEX_128, "trade_id")
        if selection_epoch > self.selection_epoch:
            raise ProtocolError("event belongs to a future selection epoch")
        if selection_epoch < self.selection_epoch or trade_id != self.trade_id:
            return self._stale(event, side, trade_id, selection_epoch)
        return None

    def _open_epoch(self, epoch):
        trade_id = secrets.token_hex(16)
        self._record("trade_epoch_opened", trade_id=trade_id, selection_epoch=epoch)
        self.selection_epoch = epoch
        self.trade_id = trade_id
        self._selections = {}
        self._selection_hash = None
        self._confirmations = {}
        self._prepared = set()
        self._decisions = {}
        self._decision_stored = {"start": set(), "finish": set()}
        self._releases = {}
        self._command_permits = {}
        self._animation_finished = {}
        self._save_progress = {"A": set(), "B": set()}
        self._post_trade_snapshots = {}
        self._reset_ready = set()
        self.state = "SELECTING"

    @property
    def selection_hash(self):
        return self._selection_hash

    @property
    def snapshots(self):
        return {side: self.snapshot_summary(side) for side in self._snapshots}

    def snapshot_summary(self, side):
        side = self._peer(side)
        snapshot = self._snapshots.get(side)
        if snapshot is None:
            return None
        return {"snapshot_id": snapshot["snapshot_id"],
                "digest": snapshot["digest"],
                "slot_digests": dict(snapshot["slot_digests"])}

    def snapshot_ready(self, side, snapshot_id, blocks):
        self._ensure_available()
        side = self._peer(side)
        snapshot_id = _valid_hex(snapshot_id, _HEX_128, "snapshot_id")
        if not isinstance(blocks, dict) or set(blocks) != set(PHASE_ORDER):
            raise ProtocolError("snapshot must contain exactly the seven ordered data phases")
        if any(not isinstance(blocks[phase], (bytes, bytearray, memoryview))
               for phase in PHASE_ORDER):
            raise ProtocolError("snapshot phase values must be byte buffers")
        normalized_blocks = {phase: bytes(blocks[phase]) for phase in PHASE_ORDER}
        try:
            digest = snapshot_digest(normalized_blocks)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError(f"snapshot blocks are invalid: {exc}") from exc
        _link_player, valid_magic = linkplayer.parse_block(normalized_blocks["link_player"])
        if not valid_magic:
            raise ProtocolError("snapshot LinkPlayer block has invalid GameFreak magic")
        party = b"".join(normalized_blocks[f"party_{index}"] for index in range(3))
        normalized = {}
        for slot in range(6):
            start = slot * monmod.PARTY_MON_SIZE
            raw_mon = party[start:start + monmod.PARTY_MON_SIZE]
            if not monmod.Mon(raw_mon).is_empty:
                normalized[slot] = hashlib.sha256(raw_mon).hexdigest()
        if not normalized:
            raise ProtocolError("snapshot party contains no selectable Pokemon")
        value = {"snapshot_id": snapshot_id, "digest": digest,
                 "slot_digests": normalized}
        if side in self._snapshots:
            if self._snapshots[side] != value:
                raise ProtocolError(f"side {side} published conflicting snapshot data")
            return {"type": "SNAPSHOT_ACK", "side": side,
                    "snapshot_id": snapshot_id, "digest": digest}
        if self.state != "WAITING_SNAPSHOTS":
            raise TradeCoordinatorError("snapshots are immutable after trade selection opens")
        self._record("snapshot_ready", side=side, snapshot_id=snapshot_id, digest=digest,
                     valid_slots=sorted(normalized), slot_digests={
                         str(slot): item_digest for slot, item_digest in sorted(normalized.items())})
        self._snapshots[side] = value
        del normalized_blocks, party
        return {"type": "SNAPSHOT_ACK", "side": side,
                "snapshot_id": snapshot_id, "digest": digest}

    def acknowledge_peer_snapshot(self, side, snapshot_id, digest):
        self._ensure_available()
        side = self._peer(side)
        snapshot_id = _valid_hex(snapshot_id, _HEX_128, "peer snapshot_id")
        digest = _valid_hex(digest, _HEX_256, "snapshot digest")
        if set(self._snapshots) != set(PEERS):
            raise TradeCoordinatorError("both local snapshots must exist before cross-acknowledging")
        peer_snapshot = self._snapshots["B" if side == "A" else "A"]
        if (snapshot_id != peer_snapshot["snapshot_id"]
                or not hmac.compare_digest(digest, peer_snapshot["digest"])):
            raise ProtocolError("peer snapshot acknowledgement does not match the published snapshot")
        if side in self._snapshot_acks:
            return {"type": "SNAPSHOT_ACK_ACK", "side": side,
                    "snapshot_id": snapshot_id, "digest": digest}
        self._record("peer_snapshot_acknowledged", side=side,
                     peer_snapshot_id=snapshot_id, digest=digest)
        self._snapshot_acks.add(side)
        if self._snapshot_acks == set(PEERS):
            self._open_epoch(0)
        return {"type": "SNAPSHOT_ACK_ACK", "side": side,
                "snapshot_id": snapshot_id, "digest": digest}

    def prepare_payload(self):
        if (self.state != "PREPARING" or self._selection_hash is None
                or set(self._selections) != set(PEERS)):
            raise TradeCoordinatorError("PREPARE requires both selected and confirmed slots")
        a, b = self._selections["A"], self._selections["B"]
        snapshot_a, snapshot_b = self._snapshots["A"], self._snapshots["B"]
        return {
            "trade_id": self.trade_id,
            "selection_epoch": self.selection_epoch,
            "selection_hash": self._selection_hash,
            "snapshot_a_id": snapshot_a["snapshot_id"],
            "snapshot_a_digest": snapshot_a["digest"],
            "snapshot_b_id": snapshot_b["snapshot_id"],
            "snapshot_b_digest": snapshot_b["digest"],
            "slot_a": a["slot"],
            "slot_a_digest": a["slot_digest"],
            "slot_b": b["slot"],
            "slot_b_digest": b["slot_digest"],
        }

    def select(self, side, trade_id, selection_epoch, snapshot_id, slot, slot_digest):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("SELECT", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        selected_snapshot = self._snapshots[side]
        slot_digest = _valid_hex(slot_digest, _HEX_256, "selected slot digest")
        if snapshot_id != selected_snapshot["snapshot_id"]:
            raise ProtocolError("selection is not bound to the current local snapshot")
        if type(slot) is not int or not 0 <= slot <= 5:
            raise ProtocolError("selected slot must be an integer from 0 through 5")
        if selected_snapshot["slot_digests"].get(slot) != slot_digest:
            raise ProtocolError("selected slot is empty or its digest does not match the snapshot")
        selection = {"snapshot_id": snapshot_id, "snapshot_digest": selected_snapshot["digest"],
                     "slot": slot, "slot_digest": slot_digest}
        existing = self._selections.get(side)
        if existing is not None:
            if existing != selection:
                raise ProtocolError("selection changed without a new selection epoch")
            return {"type": "SELECT_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch}
        if self.state != "SELECTING":
            raise TradeCoordinatorError(f"SELECT is not allowed in {self.state}")

        candidate = {**self._selections, side: selection}
        pair_hash = None
        if set(candidate) == set(PEERS):
            pair = {
                "run_id": self.run_id,
                "trade_id": trade_id,
                "selection_epoch": selection_epoch,
                "snapshots": {key: {"snapshot_id": item["snapshot_id"],
                                    "digest": item["digest"]}
                              for key, item in sorted(self._snapshots.items())},
                "selections": candidate,
            }
            pair_hash = _digest(_DECISION_DOMAIN + _canonical(pair))
        self._record("selection", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, snapshot_id=snapshot_id,
                     snapshot_digest=selected_snapshot["digest"], slot=slot,
                     slot_digest=slot_digest)
        if pair_hash is not None:
            self._record("selection_pair_ready", trade_id=trade_id,
                         selection_epoch=selection_epoch, selection_hash=pair_hash)
        self._selections = candidate
        if pair_hash is not None:
            self._selection_hash = pair_hash
            self.state = "CONFIRMING"
        action = {"type": "SELECT_ACK", "side": side,
                  "trade_id": trade_id, "selection_epoch": selection_epoch}
        if pair_hash is not None:
            action["selection_hash"] = pair_hash
        return action

    def confirm(self, side, trade_id, selection_epoch, accepted):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("CONFIRM", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if type(accepted) is not bool:
            raise ProtocolError("confirmation must be a boolean")
        if side in self._confirmations:
            if self._confirmations[side] != accepted:
                raise ProtocolError("confirmation changed without a new selection epoch")
            return {"type": "CONFIRM_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "accepted": accepted}
        if self.state != "CONFIRMING":
            raise TradeCoordinatorError(f"CONFIRM is not allowed in {self.state}")
        self._record("game_confirmation", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, accepted=accepted,
                     selection_hash=self._selection_hash)
        self._confirmations[side] = accepted
        if not accepted:
            self.state = "RESETTING"
            self._cancel_reasons[side] = "game_rejected"
            self._exit_requested = False
            return {"type": "CANCEL", "side": side, "trade_id": trade_id,
                    "selection_epoch": selection_epoch, "reason": "game_rejected",
                    "next_selection_epoch": selection_epoch + 1,
                    "exit_room": False}
        if self._confirmations == {"A": True, "B": True}:
            self.state = "PREPARING"
        return {"type": "CONFIRM_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "accepted": accepted}

    def prepared(self, side, trade_id, selection_epoch, selection_hash):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("PREPARED", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        selection_hash = _valid_hex(selection_hash, _HEX_256, "prepared selection hash")
        if not hmac.compare_digest(selection_hash, self._selection_hash):
            raise ProtocolError("PREPARED selection digest does not match this epoch")
        if side in self._prepared:
            return {"type": "PREPARED_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "selection_hash": self._selection_hash}
        if self.state != "PREPARING" or self._confirmations != {"A": True, "B": True}:
            raise TradeCoordinatorError("PREPARED requires both current game confirmations")
        self._record("prepared", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, selection_hash=self._selection_hash)
        self._prepared.add(side)
        if self._prepared == set(PEERS):
            self.state = "START_DECISION_READY"
        return {"type": "PREPARED_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "selection_hash": self._selection_hash}

    def create_decision(self, kind):
        self._ensure_available()
        if kind not in ("start", "finish"):
            raise ProtocolError("decision kind must be start or finish")
        expected = "START_DECISION_READY" if kind == "start" else "FINISH_DECISION_READY"
        if kind in self._decisions:
            return self._decisions[kind]
        if self.state != expected:
            raise TradeCoordinatorError(f"{kind} decision is not allowed in {self.state}")
        if kind == "start":
            facts = {
                "selection_hash": self._selection_hash,
                "prepared_sides": sorted(self._prepared),
            }
        else:
            facts = {"finished_sides": sorted(self._animation_finished),
                     "event_digests": {side: digest for side, digest in sorted(
                         self._animation_finished.items())},
                     "selection_hash": self._selection_hash}
        decision_hash = trade_decision_hash(
            self.run_id, self.trade_id, self.selection_epoch, kind,
            self._selection_hash,
            prepared_sides=facts.get("prepared_sides", ()),
            finished_sides=facts.get("finished_sides", ()),
            event_digests=facts.get("event_digests"))
        self._record("decision", kind=kind, trade_id=self.trade_id,
                     selection_epoch=self.selection_epoch, decision_hash=decision_hash,
                     facts=facts)
        decision = TradeDecision(
            self.run_id, self.trade_id, self.selection_epoch, kind, decision_hash,
            self._selection_hash,
            tuple(sorted(self._prepared)) if kind == "start" else (),
            tuple(sorted(self._animation_finished)) if kind == "finish" else (),
            tuple(sorted(self._animation_finished.items())) if kind == "finish" else ())
        self._decisions[kind] = decision
        self.state = "START_DECIDED" if kind == "start" else "FINISH_DECIDED"
        return decision

    def decision_stored(self, side, kind, trade_id, selection_epoch, decision_hash):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("DECISION_STORED", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if kind not in ("start", "finish"):
            raise ProtocolError("decision kind must be start or finish")
        decision = self._decisions.get(kind)
        if decision is None:
            raise TradeCoordinatorError("DECISION_STORED has no current decision")
        decision_hash = _valid_hex(decision_hash, _HEX_256, "decision hash")
        if not hmac.compare_digest(decision_hash, decision.decision_hash):
            raise ProtocolError("DECISION_STORED hash does not match the current decision")
        if side in self._decision_stored[kind]:
            return {"type": "DECISION_STORED_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "kind": kind, "decision_hash": decision.decision_hash}
        self._record("decision_stored", side=side, kind=kind,
                     trade_id=trade_id, selection_epoch=selection_epoch,
                     decision_hash=decision.decision_hash)
        self._decision_stored[kind].add(side)
        if self._decision_stored[kind] == set(PEERS):
            self.state = "START_RELEASE_READY" if kind == "start" else "FINISH_RELEASE_READY"
        return {"type": "DECISION_STORED_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "kind": kind, "decision_hash": decision.decision_hash}

    def create_release(self, kind):
        self._ensure_available()
        if kind not in ("start", "finish"):
            raise ProtocolError("release kind must be start or finish")
        if kind in self._releases:
            return self._releases[kind]
        expected = "START_RELEASE_READY" if kind == "start" else "FINISH_RELEASE_READY"
        if (self.state != expected or self._decision_stored[kind] != set(PEERS)
                or kind not in self._decisions):
            raise TradeCoordinatorError(f"{kind} release is not authorized in {self.state}")
        decision = self._decisions[kind]
        release_hash = trade_release_hash(
            self.run_id, self.trade_id, self.selection_epoch, kind,
            decision.decision_hash)
        if kind == "start":
            # A write/fsync error at this boundary is ambiguous on disk. Fail closed.
            self._irreversible = True
        self._record("release_created", kind=kind, trade_id=self.trade_id,
                     selection_epoch=self.selection_epoch,
                     decision_hash=decision.decision_hash, release_hash=release_hash)
        release = TradeRelease(self.run_id, self.trade_id, self.selection_epoch,
                               kind, decision.decision_hash, release_hash)
        self._releases[kind] = release
        self._irreversible = True
        self.state = "START_RELEASED" if kind == "start" else "FINISH_RELEASED"
        return release

    def receive_release(self, side, release):
        """Persist a received release before returning its one-shot game-command permit."""
        self._ensure_available()
        side = self._peer(side)
        if not isinstance(release, TradeRelease):
            raise ProtocolError("release must be a validated TradeRelease")
        if release.run_id != self.run_id:
            raise ProtocolError("release belongs to a different run")
        if (release.selection_epoch < self.selection_epoch
                or release.trade_id != self.trade_id):
            return self._stale("RELEASE", side, release.trade_id, release.selection_epoch)
        if release.selection_epoch > self.selection_epoch:
            raise ProtocolError("release belongs to a future selection epoch")
        expected = self._releases.get(release.kind)
        if expected is None or release != expected:
            raise ProtocolError("release does not match the durable current decision")
        key = (release.kind, side)
        if key in self._command_permits:
            self._record("duplicate_release_ignored", side=side, kind=release.kind,
                         trade_id=release.trade_id, selection_epoch=release.selection_epoch,
                         release_hash=release.release_hash)
            return None
        command = "START_TRADE" if release.kind == "start" else "CONFIRM_FINISH_TRADE"
        self._record("command_permit_issued", side=side, kind=release.kind,
                     command=command, trade_id=release.trade_id,
                     selection_epoch=release.selection_epoch,
                     decision_hash=release.decision_hash, release_hash=release.release_hash)
        permit = CommandPermit(side, self.run_id, release.trade_id,
                               release.selection_epoch, release.kind,
                               command, release.release_hash)
        self._command_permits[key] = permit
        return permit

    def animation_finished(self, side, trade_id, selection_epoch, event_digest):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("ANIMATION_FINISHED", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        event_digest = _valid_hex(event_digest, _HEX_256, "animation event digest")
        if side in self._animation_finished:
            if self._animation_finished[side] != event_digest:
                raise ProtocolError("animation completion changed within this trade")
            return {"type": "ANIMATION_FINISHED_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "event_digest": event_digest}
        if (self.state not in ("START_RELEASED", "ANIMATING")
                or ("start", side) not in self._command_permits):
            raise TradeCoordinatorError("animation completion requires this side's start permit")
        self._record("animation_finished", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, event_digest=event_digest)
        self._animation_finished[side] = event_digest
        self.state = ("FINISH_DECISION_READY" if set(self._animation_finished) == set(PEERS)
                      else "ANIMATING")
        return {"type": "ANIMATION_FINISHED_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "event_digest": event_digest}

    def save_progress(self, side, trade_id, selection_epoch, milestone):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("SAVE_PROGRESS", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if ("finish", side) not in self._command_permits:
            raise TradeCoordinatorError("save progress requires this side's finish permit")
        if milestone not in _SAVE_MILESTONES:
            raise ProtocolError("unknown save progress milestone")
        progress_index = _SAVE_MILESTONES.index(milestone) if milestone in _SAVE_MILESTONES else -1
        if milestone in self._save_progress[side]:
            return {"type": "SAVE_PROGRESS_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "milestone": milestone, "progress_index": progress_index}
        if self.state == "RESULT_PENDING":
            raise TradeCoordinatorError("save progress cannot advance after result collection starts")
        expected_milestone = _SAVE_MILESTONES[len(self._save_progress[side])]
        if milestone != expected_milestone:
            raise ProtocolError(f"expected save milestone {expected_milestone}")
        self._record("save_progress", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, milestone=milestone)
        self._save_progress[side].add(milestone)
        self.state = "SAVING"
        return {"type": "SAVE_PROGRESS_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "milestone": milestone, "progress_index": progress_index}

    def post_trade_snapshot(self, side, trade_id, selection_epoch, snapshot_id, digest):
        """Record re-read evidence without claiming game-level result validation."""
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("POST_TRADE_SNAPSHOT", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if "save_complete" not in self._save_progress[side]:
            raise TradeCoordinatorError("post-trade snapshot requires observed local save completion")
        snapshot_id = _valid_hex(snapshot_id, _HEX_128, "post-trade snapshot_id")
        digest = _valid_hex(digest, _HEX_256, "post-trade snapshot digest")
        value = (snapshot_id, digest)
        if side in self._post_trade_snapshots:
            if self._post_trade_snapshots[side] != value:
                raise ProtocolError("post-trade snapshot changed within this run")
            return {"type": "POST_TRADE_SNAPSHOT_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "snapshot_id": snapshot_id, "digest": digest}
        self._record("post_trade_snapshot", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, snapshot_id=snapshot_id,
                     digest=digest)
        self._post_trade_snapshots[side] = value
        if set(self._post_trade_snapshots) == set(PEERS):
            self.state = "RESULT_PENDING"
        return {"type": "POST_TRADE_SNAPSHOT_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "snapshot_id": snapshot_id, "digest": digest}

    def cancel(self, side, trade_id, selection_epoch, *, reason="user_cancel", exit_room=False):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("CANCEL", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if reason not in _CANCEL_REASONS or type(exit_room) is not bool:
            raise ProtocolError("invalid cancel reason or exit_room flag")
        if self._irreversible:
            self._record("cancel_after_release", side=side, trade_id=trade_id,
                         selection_epoch=selection_epoch, reason=reason)
            self.state = "IN_DOUBT"
            return {"type": "CLOSE_REQUEST", "side": side, "trade_id": trade_id,
                    "selection_epoch": selection_epoch, "reason": "in_doubt"}
        if self.state in ("CANCELLED", "IN_DOUBT", "RESULT_PENDING"):
            raise TradeCoordinatorError(f"cancel is not allowed in {self.state}")
        if side in self._cancel_reasons:
            previous = self._cancel_reasons[side]
            if previous != reason:
                raise ProtocolError("cancel reason changed within the current reset")
            return {"type": "CANCEL_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "reason": reason,
                    "next_selection_epoch": selection_epoch + 1}
        self._record("cancel_requested", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch, reason=reason, exit_room=exit_room)
        self._cancel_reasons[side] = reason
        self._exit_requested = self._exit_requested or exit_room or reason == "peer_exit"
        self.state = "RESETTING"
        return {"type": "CANCEL_ACK", "side": side, "trade_id": trade_id,
                "selection_epoch": selection_epoch,
                "reason": reason,
                "next_selection_epoch": selection_epoch + 1}

    def menu_ready(self, side, trade_id, selection_epoch):
        self._ensure_available()
        side = self._peer(side)
        stale = self._check_epoch("MENU_READY", side, trade_id, selection_epoch)
        if stale is not None:
            return stale
        if self.state != "RESETTING":
            raise TradeCoordinatorError("MENU_READY is only accepted while resetting a selection")
        if side in self._reset_ready:
            return {"type": "MENU_READY_ACK", "side": side,
                    "trade_id": trade_id, "selection_epoch": selection_epoch,
                    "next_selection_epoch": selection_epoch + 1}
        self._record("menu_ready", side=side, trade_id=trade_id,
                     selection_epoch=selection_epoch)
        candidate = self._reset_ready | {side}
        if candidate == set(PEERS):
            if self._exit_requested:
                self._record("session_cancelled", trade_id=trade_id,
                             selection_epoch=selection_epoch,
                             reasons={key: value for key, value in sorted(
                                 self._cancel_reasons.items())})
                self._reset_ready = candidate
                self.state = "CANCELLED"
            else:
                self._record("selection_epoch_reset", trade_id=trade_id,
                             selection_epoch=selection_epoch,
                             next_selection_epoch=selection_epoch + 1)
                # _open_epoch writes the unique ID durably before exposing SELECTING.
                self._reset_ready = candidate
                self._cancel_reasons = {}
                self._exit_requested = False
                self._open_epoch(selection_epoch + 1)
        else:
            self._reset_ready = candidate
        return {"type": "MENU_READY_ACK", "side": side,
                "trade_id": trade_id, "selection_epoch": selection_epoch,
                "next_selection_epoch": selection_epoch + 1}

    def disconnect(self, side, reason):
        self._ensure_available()
        side = self._peer(side)
        if not isinstance(reason, str) or not reason or len(reason) > 80:
            raise ProtocolError("disconnect reason must be a short string")
        self._record("peer_disconnected", side=side, reason=reason,
                     trade_id=self.trade_id, selection_epoch=self.selection_epoch)
        self.state = "IN_DOUBT" if self._irreversible else "CANCELLED"
        return self.state

    def report(self):
        """Return only scalar/hash diagnostics; never return party or Pokemon bytes."""
        return {
            "run_id": self.run_id,
            "state": self.state,
            "trade_id": self.trade_id,
            "selection_epoch": self.selection_epoch,
            "snapshots": {side: {"snapshot_id": value["snapshot_id"],
                                  "digest": value["digest"]}
                          for side, value in sorted(self._snapshots.items())},
            "snapshot_acks": sorted(self._snapshot_acks),
            "selection_hash": self._selection_hash,
            "selected_slots": {side: value["slot"]
                               for side, value in sorted(self._selections.items())},
            "prepared": sorted(self._prepared),
            "decision_stored": {kind: sorted(sides)
                                for kind, sides in self._decision_stored.items()},
            "command_permits": sorted(
                f"{kind}:{side}" for kind, side in self._command_permits),
            "animation_finished": sorted(self._animation_finished),
            "save_progress": {side: sorted(items)
                              for side, items in self._save_progress.items()},
            "post_trade_snapshots": sorted(self._post_trade_snapshots),
            "result_verified": False,
            "resumable": False,
        }
