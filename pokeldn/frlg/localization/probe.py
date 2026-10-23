"""P0 bootstrap: obtain guest header and runtime fingerprint through PokeLDN itself.

No base ROM/profile or wrapper claim is required for these bounded ROM reads. This
does not grant write eligibility or normalize a live dump into an original cartridge.
"""

from concurrent.futures import Future
from dataclasses import dataclass
import hashlib
from pathlib import Path
from time import monotonic

from . import LocalizationError, ROM_BASE, integer
from .gift_transport import GiftBridge, GiftTransport, LocalizationGiftOperation, LocalizationGiftServer
from .renderer import TEXT_ENGINE_SIZE, inspect_prefix


@dataclass(frozen=True)
class ProbeIdentity:
    game_code: str = "BPRE"
    revision: int = 0x0A


@dataclass(frozen=True)
class ProbeTarget:
    profile_id: str = "bpre-0a-read-only-bootstrap"
    identity: ProbeIdentity = ProbeIdentity()


class LocalizationProbeOperation(LocalizationGiftOperation):
    """The established English FireRed target, read-only, without an original ROM file."""

    def __init__(self, *, rom_size=0x1000000, dump_path=None, timeout=30.0,
                 log=lambda *args: None, progress=lambda done, total: None):
        integer(rom_size, "probe ROM size", 0x1000, 0x1000000)
        if rom_size & (rom_size - 1):
            raise LocalizationError("probe ROM size must be a power of two")
        self.profile, self.operation = ProbeTarget(), "probe"
        self.rom_size, self.dump_path = rom_size, None if dump_path is None else Path(dump_path)
        self.progress = progress
        if self.dump_path is not None and self.dump_path.exists():
            raise LocalizationError("probe dump output already exists")
        if self.dump_path is not None and not self.dump_path.parent.is_dir():
            raise LocalizationError("probe dump parent directory does not exist")
        self.bridge = GiftBridge(timeout)
        self.transport = GiftTransport(self.bridge, self.profile)
        self.read_batch_size = self.transport.max_dump_blocks * 1024
        self.result, self.worker = Future(), None
        self.server = LocalizationGiftServer(self, log=log)

    def _perform(self):
        header = self.transport.read_memory(ROM_BASE, 0xC0)
        if (header[0xAC:0xB0] != b"BPRE" or header[0xBC] != 0x0A):
            raise LocalizationError("ROM header conflicts with the BPRE 0x0A game-data handshake")
        digest = hashlib.sha256()
        started = monotonic()
        prefix = bytearray() if self.rom_size == TEXT_ENGINE_SIZE else None
        self.progress(0, self.rom_size)
        output = None
        try:
            if self.dump_path is not None:
                output = self.dump_path.open("xb")
            for offset in range(0, self.rom_size, self.read_batch_size):
                block = self.transport.read_memory(ROM_BASE + offset,
                                                   min(self.read_batch_size, self.rom_size - offset),
                                                   progress=lambda done, total, offset=offset:
                                                   self.progress(offset + done, self.rom_size))
                digest.update(block)
                if prefix is not None:
                    prefix.extend(block)
                if output is not None:
                    output.write(block)
                    output.flush()
        finally:
            # A partial capture is retained for diagnosis. A report is emitted only after
            # completion; it must never be mistaken for a verified original cartridge.
            if output is not None:
                output.close()
        result = {"state": "READ_ONLY_CAPTURED", "read_only": True, "game_code": "BPRE",
                  "revision": 10, "rom_size": self.rom_size, "runtime_sha256": digest.hexdigest(),
                  "read_batch_bytes": self.read_batch_size,
                  "capture_elapsed_seconds": monotonic() - started,
                  "header_hex": header.hex(), "dump_path": str(self.dump_path) if self.dump_path else None,
                  "original_rom_verified": False, "wrapper_identity_verified": False,
                  "write_eligible": False}
        if prefix is not None:
            result["text_engine"] = inspect_prefix(prefix)
        return result
