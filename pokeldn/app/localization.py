"""Completed read-only captures the desktop session can present to the player."""
from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Capture:
    report: Path
    runtime: Path
    size: int
    sha256: str

    @property
    def full_rom(self) -> bool:
        return self.size == 0x1000000


def read_capture(folder: str) -> Capture:
    report = Path(folder) / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("state") != "READ_ONLY_CAPTURED"
            or data.get("read_only") is not True or data.get("write_eligible") is not False
            or data.get("game_code") != "BPRE" or data.get("revision") != 10):
        raise ValueError("The capture report does not confirm the read-only FireRed test.")
    size, digest = data.get("rom_size"), data.get("runtime_sha256")
    if (type(size) is not int or not 0x1000 <= size <= 0x1000000 or size & (size - 1)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("The capture report has an invalid size or fingerprint.")
    runtime = report.parent / ("runtime.gba" if size == 0x1000000 else "runtime-sample.bin")
    if runtime.stat().st_size != size:
        raise ValueError("The saved runtime capture is incomplete.")
    return Capture(report, runtime, size, digest)
