"""Journal-before-write loading, full readback, restart detection and reverse restoration."""

import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from time import perf_counter
from uuid import uuid4

from . import (LocalizationError, PAGE_SIZE, canonical, fields, integer, read_json, sha256)
from .payloads import CHUNK_SIZE


class Journal:
    """Original runtime pages stay in this local file; never include it in a patch package."""

    def __init__(self, path, data):
        self.path, self.data = Path(path), data

    @classmethod
    def create(cls, path, patch, profile, pages):
        data = {"format_version": 1, "session_id": uuid4().hex, "patch_id": patch.patch_id,
                "profile_sha256": profile.fingerprint,
                "originals": {str(at): original.hex() for at, (original, _) in pages.items()},
                "events": [{"state": "PREPARED", "time_utc": datetime.now(timezone.utc).isoformat()}]}
        journal = cls(path, data)
        # Exclusive creation avoids silently replacing another recovery file.
        with journal.path.open("xb") as handle:
            handle.write(canonical(data) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return journal

    @classmethod
    def open(cls, path, patch, profile, pages):
        data = read_json(Path(path))
        fields(data, ("format_version", "session_id", "patch_id", "profile_sha256", "originals", "events"))
        session = data["session_id"]
        if (type(data["format_version"]) is not int or data["format_version"] != 1
                or not isinstance(session, str) or len(session) != 32
                or any(c not in "0123456789abcdef" for c in session)
                or data["patch_id"] != patch.patch_id or data["profile_sha256"] != profile.fingerprint):
            raise LocalizationError("journal belongs to another session/profile/patch")
        expected = {str(at): original.hex() for at, (original, _) in pages.items()}
        if data["originals"] != expected or not isinstance(data["events"], list):
            raise LocalizationError("journal original pages are corrupt or do not match base ROM")
        return cls(path, data)

    def record(self, state, **details):
        self.data["events"].append({"state": state, "time_utc": datetime.now(timezone.utc).isoformat(),
                                    **details})
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.name + ".",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(canonical(self.data) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class Session:
    def __init__(self, profile, patch, base, transport, journal_path, *, retries=2):
        self.profile, self.patch, self.base, self.transport = profile, patch, bytes(base), transport
        self.pages = patch.validate(profile, self.base)
        self.journal_path = Path(journal_path)
        self.retries = integer(retries, "retries", 0, 8)
        self.journal = None

    def _retry(self, callback):
        for attempt in range(self.retries + 1):
            try:
                return callback()
            except (TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise

    def inspect(self):
        observed = self.transport.observe()
        if observed.identity != self.profile.identity:
            raise LocalizationError("observed game/wrapper identity does not match profile")
        if not observed.executing:
            raise LocalizationError("current execution ranges are unknown; read-only inspection required")
        for page in self.patch.pages:
            for start, end in observed.executing:
                if page.offset < end and start < page.offset + PAGE_SIZE:
                    raise LocalizationError("page overlaps currently executing code")
        live = self._retry(lambda: self.transport.read_rom(0, self.profile.rom_size))
        if len(live) != self.profile.rom_size:
            raise LocalizationError("incomplete live ROM fingerprint")
        normalized, states = bytearray(live), {}
        for page in self.patch.pages:
            old, target = self.pages[page.offset]
            current = live[page.offset:page.offset + PAGE_SIZE]
            if current == old:
                states[page.offset] = "original"
            elif current == target:
                states[page.offset] = "target"
            else:
                raise LocalizationError(f"page {page.offset:#x} matches neither original nor target")
            normalized[page.offset:page.offset + PAGE_SIZE] = old
        if bytes(normalized) != self.profile.runtime_base(self.base):
            raise LocalizationError("unexplained live ROM changes outside patch pages")
        resources = [p for p in self.patch.pages if p.kind != "activation"]
        if any(p.kind == "activation" and states[p.offset] == "target" for p in self.patch.pages):
            if any(states[p.offset] != "target" for p in resources):
                raise LocalizationError("active entry has missing resource/code dependencies")
        return live, states

    def _open_journal(self, *, restore=False):
        if self.journal_path.exists():
            self.journal = Journal.open(self.journal_path, self.patch, self.profile, self.pages)
        elif restore:
            raise LocalizationError("restoration requires the original local journal")
        else:
            self.journal = Journal.create(self.journal_path, self.patch, self.profile, self.pages)

    def _write_page(self, page, before, target, image):
        profile, transport = self.profile, self.transport
        session_id, patch_id = self.journal.data["session_id"], self.patch.patch_id
        # Re-observe immediately before each write; reconnects cannot inherit trust from an ID.
        observation = transport.observe()
        if observation.identity != profile.identity or not observation.executing:
            raise LocalizationError("console identity/execution context changed during loading")
        if any(page.offset < end and start < page.offset + PAGE_SIZE for start, end in observation.executing):
            raise LocalizationError("page is now executing")
        if self._retry(lambda: transport.read_rom(page.offset, PAGE_SIZE)) != before:
            raise LocalizationError("page changed since preflight")
        self.journal.record("STAGING", page=page.offset)
        self._retry(lambda: transport.begin(profile, session_id, patch_id, page.offset, target))
        for chunk in range(PAGE_SIZE // CHUNK_SIZE):
            self._retry(lambda: transport.stage(profile, session_id, patch_id, page.offset, target, chunk))
        staged = self._retry(lambda: transport.read_memory(profile.stage_address, PAGE_SIZE))
        if sha256(staged) != sha256(target):
            raise LocalizationError("staging SHA-256 mismatch; commit refused")
        self.journal.record("WRITE_INTENT", page=page.offset,
                            before_sha256=sha256(before), after_sha256=sha256(target))
        # A lost commit acknowledgement is ambiguous. Read before considering a retransmission.
        for attempt in range(self.retries + 1):
            try:
                transport.commit(profile, session_id, patch_id, page.offset, before, target)
                break
            except (TimeoutError, ConnectionError):
                current = self._retry(lambda: transport.read_rom(page.offset, PAGE_SIZE))
                if current == target:
                    break
                if current != before or attempt == self.retries:
                    raise LocalizationError("uncertain page commit; stop and inspect with journal")
        if self._retry(lambda: transport.read_rom(page.offset, PAGE_SIZE)) != target:
            raise LocalizationError("committed page readback mismatch")
        for offset in (page.offset - PAGE_SIZE, page.offset + PAGE_SIZE):
            if 0 <= offset < profile.rom_size:
                current = self._retry(lambda: transport.read_rom(offset, PAGE_SIZE))
                if current != image[offset:offset + PAGE_SIZE]:
                    raise LocalizationError("neighbor page changed during commit")
        image[page.offset:page.offset + PAGE_SIZE] = target
        self.journal.record("VERIFIED", page=page.offset, sha256=sha256(target))

    def _operate(self, restore):
        started = perf_counter()
        live, states = self.inspect()
        self._open_journal(restore=restore)
        image = bytearray(live)
        try:
            self.journal.record("IDENTIFIED", operation="restore" if restore else "load")
            ordered = reversed(self.patch.pages) if restore else self.patch.pages
            for page in ordered:
                old, target = self.pages[page.offset]
                want_state = "original" if restore else "target"
                if states[page.offset] == want_state:
                    self.journal.record("ALREADY_VERIFIED", page=page.offset, content=want_state)
                    continue
                # All resource pages must still match immediately before each activation write.
                if page.kind == "activation" and not restore:
                    _, current_states = self.inspect()
                    if any(current_states[p.offset] != "target" for p in self.patch.pages if p.kind != "activation"):
                        raise LocalizationError("activation dependencies changed")
                self._write_page(page, target if restore else old, old if restore else target, image)
                if page.kind == "activation":
                    self.journal.record("DEACTIVATED" if restore else "ACTIVATED", page=page.offset)
            final, _ = self.inspect()
            expected = bytearray(self.profile.runtime_base(self.base))
            if not restore:
                for at, (_, target) in self.pages.items():
                    expected[at:at + PAGE_SIZE] = target
            if final != bytes(expected):
                raise LocalizationError("final full ROM verification mismatch")
            self.journal.record("RESTORED" if restore else "COMPLETE", runtime_sha256=sha256(final))
            return {"state": "RESTORED" if restore else "COMPLETE", "patch_id": self.patch.patch_id,
                    "pages": len(self.patch.pages), "runtime_sha256": sha256(final),
                    "elapsed_ms": round((perf_counter() - started) * 1000)}
        except BaseException as exc:
            # Includes keyboard interruption: preserve intent and originals before returning control.
            self.journal.record("STOPPED", error=type(exc).__name__, message=str(exc))
            raise

    def apply(self):
        return self._operate(False)

    def restore(self):
        return self._operate(True)
