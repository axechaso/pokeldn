"""One normal NPC sentence, with a temporary RAM font dispatch and no ROM writes.

The source-matched native functions still handle speed, input, control codes,
window upload and the English continuation. A completed upload is not visual
confirmation of the NPC dialogue on the user's Switch.
"""
from concurrent.futures import Future
from dataclasses import replace
import struct

from pokeldn.frlg.rom import buffer_script as bs
from pokeldn.frlg.rom.resident_stubs import STUBS
from . import LocalizationError, read_json, sha256
from .dialogue import ASSETS
from .font import BitmapFont, unpack_pixels
from .gift_transport import GiftBridge, GiftTransport, LocalizationGiftOperation, LocalizationGiftServer
from .probe import ProbeTarget

TEXT = "科学的力量真伟大！"
RESIDENT = 0x0203FC00
SLOT_SIZE = 1024
STAGED, INSTALLED, ALREADY_INSTALLED = 0x5A485331, 0x5A485332, 0x5A485341
REFUSALS = {0xBAD10001: "ROM header does not match English FireRed revision 10",
            0xBAD10002: "native text functions, font table or NPC message differ from the verified build",
            0xBAD10003: "resident RAM or font/interrupt pointers are occupied or changed; restart the game"}


def fnv(data):
    result = 0x811C9DC5
    for byte in data:
        result = ((result ^ byte) * 0x1000193) & 0xFFFFFFFF
    return result


def build_story_blob():
    code, _, symbols = STUBS["localization-story"]
    blob = bytearray(code)
    profile = read_json(ASSETS / "ns-story-profile.json")
    for word, target in (("p_ram_fonts", "font_table"), ("p_prefix", "prefix"),
                         ("p_prefix_end", "prefix_end"), ("p_glyphs", "glyphs")):
        struct.pack_into("<I", blob, symbols[word], RESIDENT + symbols[target])
    if profile["text_size"] != 89:
        raise LocalizationError("NPC dialogue source size changed")
    for word, value in (("p_text_hash", profile["text_fnv"]),
                        ("p_tail", 0x02021D18 + profile["tail_offset"])):
        struct.pack_into("<I", blob, symbols[word], value)
    table = bytearray.fromhex(profile["font_table"])
    for font_id in (2, 4):
        struct.pack_into("<I", table, font_id * 12, RESIDENT + symbols["story_font"] + 1)
    blob[symbols["font_table"]:symbols["font_table"] + 96] = table
    font = BitmapFont.parse(read_json(ASSETS / "story-font.json"))
    mono = bytearray()
    for char in TEXT:
        pixels = unpack_pixels(font.glyphs[char].packed)
        mono.extend(sum(int(pixels[i + bit] >= 2) << bit for bit in range(8))
                    for i in range(0, 256, 8))
    if len(mono) != 288 or len(blob) > SLOT_SIZE:
        raise LocalizationError("NPC dialogue resident layout changed")
    blob[symbols["glyphs"]:symbols["glyphs"] + 288] = mono
    return bytes(blob)


def build_story_payloads():
    blob = build_story_blob()
    profile = read_json(ASSETS / "ns-story-profile.json")
    guards = b"".join(struct.pack("<III", item["address"], item["size"], item["fnv"])
                      for item in profile["guards"])
    template = bs.payload("localization-story-stage")
    budget = (1024 - len(template) - len(guards)) & ~3
    if budget < 4:
        raise LocalizationError("NPC dialogue stage has no upload space")
    before, full = bytes(SLOT_SIZE), blob.ljust(SLOT_SIZE, b"\x00")
    payloads = []
    for at in range(0, len(blob), budget):
        chunk = blob[at:at + budget]
        after = before[:at] + chunk + before[at + len(chunk):]
        code = bytearray(template)
        struct.pack_into("<10I", code, 4, fnv(before), fnv(after), fnv(full), at, len(chunk),
                         len(template) + len(guards), len(template), len(profile["guards"]),
                         RESIDENT + STUBS["localization-story"][2]["story_hook"] + 1,
                         int(at + len(chunk) == len(blob)))
        payloads.append(bs.validate(bytes(code) + guards + chunk))
        before = after
    return tuple(payloads)


class LocalizationStoryOperation(LocalizationGiftOperation):
    def __init__(self, *, timeout=30.0, log=lambda *args: None):
        self.profile = replace(ProbeTarget(), profile_id="bpre-0a-pallet-npc-ram-demo")
        self.operation = "story"
        self.bridge = GiftBridge(timeout)
        self.transport = GiftTransport(self.bridge, self.profile)
        self.result, self.worker = Future(), None
        self.server = LocalizationGiftServer(self, log=log)

    def _perform(self):
        payloads = build_story_payloads()
        sent, answer = [], None
        for index, code in enumerate(payloads):
            answer = self.transport._run_code(code)
            sent.append(sha256(code))
            if answer == ALREADY_INSTALLED and index == 0:
                break
            expected = INSTALLED if index == len(payloads) - 1 else STAGED
            if answer != expected:
                raise LocalizationError("NS NPC dialogue test refused: " + REFUSALS.get(answer, f"reply {answer:#x}"))
        blob = build_story_blob()
        return {"state": "STORY_DEMO_READY", "game_code": "BPRE", "revision": 10,
                "text": TEXT, "target": "Pallet Town south fat man, first sentence",
                "resident_bytes": len(blob), "resident_sha256": sha256(blob),
                "payload_sha256": sent, "reply": answer,
                "visual_confirmation_required": True, "rom_written": False,
                "save_written": False, "write_eligible": False,
                "removed_by": "soft reset or full game restart"}
