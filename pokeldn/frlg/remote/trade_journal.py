"""Durable, hash-chained journal for P1 FRLG trade decisions."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading

from pokeldn.app.paths import DATA


_ZERO_HASH = "0" * 64
_DOMAIN = b"pokeldn/frlg/remote/trade-journal/v1\0"
_RESERVED_FIELDS = {
    "version", "sequence", "time", "run_id", "event", "prev_hash", "record_hash",
}
_FORBIDDEN_KEYS = {
    "data", "raw", "rawbytes", "block", "blockbytes", "party", "pokemon",
    "roomkey", "secret", "prodkeys", "nickname", "trainername", "trainerid", "sid",
}


class TradeJournalError(RuntimeError):
    """The decision journal could not provide a durable record."""


class TradeJournal:
    """Append-only journal that fsyncs each state-changing event before returning.

    The synchronous API belongs on the coordinator/control path, never in an RFU tick.
    A run directory is exclusive: an old journal is evidence, not a resume point.
    """

    def __init__(self, run_id, *, root=None):
        if (not isinstance(run_id, str) or re.fullmatch(r"[0-9a-fA-F]{32}", run_id) is None):
            raise ValueError("run_id must be a 128-bit hex identifier")
        self.run_id = run_id.lower()
        self.directory = Path(root or DATA) / "remote" / self.run_id
        self.path = self.directory / "trade-events.jsonl"
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self._stream = self.path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as exc:
            raise TradeJournalError(
                "trade journal already exists; do not resume or replay this run") from exc
        self._lock = threading.Lock()
        self._sequence = 0
        self._previous_hash = _ZERO_HASH
        self._failure = None
        self._closed = False

    @property
    def failure(self):
        return self._failure

    @staticmethod
    def _canonical(record):
        return json.dumps(record, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    @classmethod
    def _record_hash(cls, record):
        previous = bytes.fromhex(record["prev_hash"])
        return hashlib.sha256(_DOMAIN + previous + cls._canonical(record)).hexdigest()

    @classmethod
    def _reject_sensitive_fields(cls, value, location="record"):
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"journal field names must be strings at {location}")
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if (normalized in _FORBIDDEN_KEYS
                        or any(secret in normalized for secret in ("pokemon", "prodkey", "roomkey"))):
                    raise ValueError(f"journal cannot store sensitive field {location}.{key}")
                cls._reject_sensitive_fields(nested, f"{location}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                cls._reject_sensitive_fields(nested, f"{location}[{index}]")

    def append_durable(self, event, **fields):
        if not isinstance(event, str) or not event or len(event) > 80:
            raise ValueError("journal event must be a short non-empty string")
        if _RESERVED_FIELDS.intersection(fields):
            raise ValueError("journal fields may not override record metadata")
        self._reject_sensitive_fields(fields)
        try:
            # Validate JSON types before taking the lock or changing the chain state.
            self._canonical(fields)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("journal fields must be JSON-safe diagnostics") from exc

        with self._lock:
            if self._closed:
                raise TradeJournalError("trade journal is closed")
            if self._failure is not None:
                raise TradeJournalError(self._failure)
            record = {
                "version": 1,
                "sequence": self._sequence,
                "time": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "event": event,
                "prev_hash": self._previous_hash,
                **fields,
            }
            record_hash = self._record_hash(record)
            record["record_hash"] = record_hash
            encoded = self._canonical(record).decode("utf-8")
            try:
                self._stream.write(encoded + "\n")
                self._stream.flush()
                os.fsync(self._stream.fileno())
            except OSError as exc:
                self._failure = f"trade journal write failed: {exc}"
                raise TradeJournalError(self._failure) from exc
            self._sequence += 1
            self._previous_hash = record_hash
            return record_hash

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._stream.close()
            except OSError as exc:
                self._failure = f"trade journal close failed: {exc}"
                raise TradeJournalError(self._failure) from exc

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    @classmethod
    def verify(cls, path):
        """Read and verify a complete journal; torn or edited files are rejected."""
        path = Path(path)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise TradeJournalError(f"cannot read trade journal: {exc}") from exc
        if content and not content.endswith(b"\n"):
            raise TradeJournalError("trade journal has an incomplete final record")
        records = []
        previous_hash = _ZERO_HASH
        run_id = None
        for sequence, line in enumerate(content.splitlines(), 1):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TradeJournalError(f"trade journal record {sequence} is invalid") from exc
            if not isinstance(record, dict):
                raise TradeJournalError(f"trade journal record {sequence} is not an object")
            try:
                cls._reject_sensitive_fields(record)
            except ValueError as exc:
                raise TradeJournalError(f"trade journal record {sequence} is unsafe: {exc}") from exc
            if (record.get("version") != 1 or record.get("sequence") != sequence - 1
                    or not isinstance(record.get("run_id"), str)
                    or re.fullmatch(r"[0-9a-f]{32}", record["run_id"]) is None
                    or record.get("prev_hash") != previous_hash
                    or not isinstance(record.get("event"), str)
                    or not record["event"] or len(record["event"]) > 80):
                raise TradeJournalError(f"trade journal chain metadata failed at record {sequence}")
            if run_id is None:
                run_id = record["run_id"]
            elif record["run_id"] != run_id:
                raise TradeJournalError("trade journal contains multiple run IDs")
            supplied_hash = record.get("record_hash")
            unsigned = dict(record)
            unsigned.pop("record_hash", None)
            if (not isinstance(supplied_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", supplied_hash) is None
                    or not hmac.compare_digest(supplied_hash, cls._record_hash(unsigned))):
                raise TradeJournalError(f"trade journal hash failed at record {sequence}")
            previous_hash = supplied_hash
            records.append(record)
        return records

    @staticmethod
    def classify_recovery(records):
        """Classify evidence only; this method never returns a replay/resume action."""
        risky = {
            ("release_created", "start"),
            ("release_received", "start"),
            ("command_permit_issued", "START_TRADE"),
            ("release_created", "finish"),
            ("release_received", "finish"),
            ("command_permit_issued", "CONFIRM_FINISH_TRADE"),
        }
        if any((record.get("event"), record.get("kind")) in risky
               or (record.get("event"), record.get("command")) in risky
               for record in records):
            return "IN_DOUBT"
        return "CANCELLED_BEFORE_RELEASE"
