"""Completed read-only captures the desktop session can present to the player."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class Capture:
    report: Path
    runtime: Path
    size: int
    sha256: str
    elapsed: float | None = None

    @property
    def full_rom(self) -> bool:
        return self.size == 0x1000000

    @property
    def text_engine(self) -> bool:
        return self.size == 0x10000


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
    with runtime.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
            raise ValueError("The saved runtime capture fingerprint does not match its report.")
    elapsed = data.get("capture_elapsed_seconds")
    if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("The capture report has an invalid duration.")
    return Capture(report, runtime, size, digest, elapsed)


def read_display(folder: str) -> dict:
    """A code reply confirms execution; it cannot confirm what the user saw on NS."""
    from pokeldn.frlg.localization.ns_display import DISPLAY_DONE, DISPLAY_FRAMES, build_display_payload
    report = Path(folder) / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("state") != "DISPLAY_DEMO_EXECUTED"
            or data.get("game_code") != "BPRE" or data.get("revision") != 10
            or data.get("rom_written") is not False or data.get("save_written") is not False
            or data.get("write_eligible") is not False or data.get("visual_confirmation_required") is not True
            or data.get("reply") != DISPLAY_DONE or data.get("display_frames") != DISPLAY_FRAMES
            or data.get("text") != "你好！"
            or data.get("payload_bytes") != len(build_display_payload())
            or data.get("payload_sha256") != hashlib.sha256(build_display_payload()).hexdigest()):
        raise ValueError("The report does not confirm this NS screen demo completed.")
    return data


def read_story(folder: str) -> dict:
    """Validate upload evidence separately from in-game visual confirmation."""
    from pokeldn.frlg.localization.ns_story import (
        ALREADY_INSTALLED, INSTALLED, TEXT, build_story_blob, build_story_payloads,
    )
    data = json.loads((Path(folder) / "report.json").read_text(encoding="utf-8"))
    payloads = [hashlib.sha256(code).hexdigest() for code in build_story_payloads()]
    blob = build_story_blob()
    if (not isinstance(data, dict) or data.get("state") != "STORY_DEMO_READY"
            or data.get("game_code") != "BPRE" or data.get("revision") != 10
            or data.get("text") != TEXT or data.get("rom_written") is not False
            or data.get("save_written") is not False or data.get("write_eligible") is not False
            or data.get("visual_confirmation_required") is not True
            or data.get("resident_bytes") != len(blob)
            or data.get("resident_sha256") != hashlib.sha256(blob).hexdigest()
            or not ((data.get("reply") == INSTALLED and data.get("payload_sha256") == payloads)
                    or (data.get("reply") == ALREADY_INSTALLED and data.get("payload_sha256") == payloads[:1]))):
        raise ValueError("The report does not confirm this NPC dialogue demo was loaded.")
    return data
