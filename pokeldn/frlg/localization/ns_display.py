"""A bounded Mystery Gift screen demo, separate from installing a ROM localization.

The source-matched English FireRed rev10 CopyWindowToVram and gWindows are
checked by the uploaded code before it touches window 1's allocated pixels.
Only UI RAM is changed. Neither the source-rebuilt ROM nor a full ROM read is
needed for this test; a successful reply still needs the user's visual check.
"""

from concurrent.futures import Future
from dataclasses import replace
import struct

from . import LocalizationError, read_json, sha256
from .dialogue import ASSETS
from .font import BitmapFont, unpack_pixels
from .gift_transport import GiftBridge, GiftTransport, LocalizationGiftOperation, LocalizationGiftServer
from .probe import ProbeTarget
from pokeldn.frlg.rom import buffer_script as bs

TEXT = "你好！"
BITMAP_OFFSET = 0x1A8
COPY_WINDOW_FNV = 0x1558E0A6
DISPLAY_DONE = 0x5A48444D
DISPLAY_FRAMES = 600
REFUSALS = {0xBAD00001: "ROM header does not match English FireRed revision 10",
            0xBAD00002: "CopyWindowToVram differs from the verified source build",
            0xBAD00003: "Mystery Gift message window or pixel allocation differs"}


def demo_bitmap():
    """48x16, 4bpp GBA tiles, using the current Mystery Gift message palette."""
    font = BitmapFont.parse(read_json(ASSETS / "demo-font.json"))
    pixels = [unpack_pixels(font.glyphs[c].packed) for c in TEXT]
    result = bytearray()
    colors = (1, 3, 2, 2)  # white background, shadow, dark foreground
    for tile_y in range(2):
        for tile_x in range(6):
            for y in range(8):
                for x in range(0, 8, 2):
                    xx, yy = tile_x * 8 + x, tile_y * 8 + y
                    char, gx = divmod(xx, 16)
                    p = pixels[char]
                    result.append(colors[p[yy * 16 + gx]] | (colors[p[yy * 16 + gx + 1]] << 4))
    return bytes(result)


def build_display_payload():
    code = bytearray(bs.payload("localization-display"))
    if len(code) != BITMAP_OFFSET + 384:
        raise LocalizationError("Chinese display payload layout changed")
    struct.pack_into("<II", code, 8, COPY_WINDOW_FNV, DISPLAY_FRAMES)
    code[BITMAP_OFFSET:] = demo_bitmap()
    return bs.validate(bytes(code))


class LocalizationDisplayOperation(LocalizationGiftOperation):
    def __init__(self, *, timeout=30.0, log=lambda *args: None):
        self.profile = replace(ProbeTarget(), profile_id="bpre-0a-temporary-screen-demo")
        self.operation = "display"
        self.bridge = GiftBridge(timeout)
        self.transport = GiftTransport(self.bridge, self.profile)
        self.result, self.worker = Future(), None
        self.server = LocalizationGiftServer(self, log=log)

    def _perform(self):
        code = build_display_payload()
        answer = self.transport._run_code(code)
        if answer != DISPLAY_DONE:
            raise LocalizationError("NS Chinese display refused: " + REFUSALS.get(answer, f"reply {answer:#x}"))
        return {"state": "DISPLAY_DEMO_EXECUTED", "game_code": "BPRE", "revision": 10,
                "text": TEXT, "display_frames": DISPLAY_FRAMES, "payload_bytes": len(code),
                "payload_sha256": sha256(code), "reply": answer,
                "visual_confirmation_required": True, "rom_written": False,
                "save_written": False, "write_eligible": False}
