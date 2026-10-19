#!/usr/bin/env python3
"""Run a local GBA image in an explicitly supplied mGBA libretro core, without a UI.

Screenshots come from the emulator's framebuffer. This exercises the GBA game,
not the Switch wrapper, PokeLDN radio transport, or retail page writes.
The core and ROM remain external, machine-local dependencies.
"""

import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import zlib


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


class SystemInfo(C.Structure):
    _fields_ = [("name", C.c_char_p), ("version", C.c_char_p), ("extensions", C.c_char_p),
                ("need_fullpath", C.c_bool), ("block_extract", C.c_bool)]


def png_frame(data, width, height, pitch, pixel_format):
    rows = []
    if pixel_format not in (0, 1, 2):
        raise ValueError("unsupported libretro pixel format")
    for y in range(height):
        row = bytearray((0,))
        for x in range(width):
            if pixel_format == 1:
                b, g, r, _ = data[y * pitch + x * 4:y * pitch + x * 4 + 4]
            else:
                value = struct.unpack_from("<H", data, y * pitch + x * 2)[0]
                r = (value >> (11 if pixel_format == 2 else 10)) & 31
                g = (value >> 5) & (63 if pixel_format == 2 else 31)
                b = value & 31
                r, g, b = r * 255 // 31, g * 255 // (63 if pixel_format == 2 else 31), b * 255 // 31
            row.extend((r, g, b))
        rows.append(row)

    def chunk(name, payload):
        return (struct.pack(">I", len(payload)) + name + payload
                + struct.pack(">I", zlib.crc32(name + payload)))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def run(core_path, rom_path, output, frames, press_every, capture_every):
    core_path, rom_path, output = core_path.resolve(), rom_path.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    core = C.CDLL(str(core_path))
    env_type = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
    video_type = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
    audio_type = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
    batch_type = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
    poll_type = C.CFUNCTYPE(None)
    input_type = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    pixel_format, framebuffer, buttons = 0, None, set()
    directory = str(output).encode("utf-8")

    @env_type
    def environment(command, data):
        nonlocal pixel_format
        if command in (9, 30, 31):  # system/content/save directories
            C.cast(data, C.POINTER(C.c_char_p))[0] = directory
            return True
        if command == 10:
            value = C.cast(data, C.POINTER(C.c_int))[0]
            if value not in (0, 1, 2):
                return False
            pixel_format = value
            return True
        if command in (3, 17):
            C.cast(data, C.POINTER(C.c_bool))[0] = command == 3
            return True
        if command in (39, 52):
            C.cast(data, C.POINTER(C.c_uint))[0] = 0  # English; legacy option interface
            return True
        if command in (6, 11, 16, 18, 35, 36, 37):
            return True
        return False

    @video_type
    def video(data, width, height, pitch):
        nonlocal framebuffer
        if data and data != C.c_void_p(-1).value:
            framebuffer = (C.string_at(data, height * pitch), width, height, pitch, pixel_format)

    @input_type
    def input_state(port, device, index, button):
        return int(port == 0 and device == 1 and button in buttons)

    callbacks = [environment, video, audio_type(lambda left, right: None),
                 batch_type(lambda data, count: count), poll_type(lambda: None), input_state]
    setters = ("environment", "video_refresh", "audio_sample", "audio_sample_batch", "input_poll", "input_state")
    for name, callback in zip(setters, callbacks):
        setter = getattr(core, "retro_set_" + name)
        setter.argtypes = [type(callback)]
        setter(callback)
    core.retro_get_system_info.argtypes = [C.POINTER(SystemInfo)]
    system = SystemInfo()
    core.retro_get_system_info(C.byref(system))
    if b"mgba" not in system.name.lower():
        raise ValueError("expected an mGBA libretro core")
    core.retro_init()
    loaded = False
    try:
        rom = C.create_string_buffer(rom_path.read_bytes())
        info = GameInfo(str(rom_path).encode("utf-8"), C.cast(rom, C.c_void_p), len(rom) - 1, None)
        core.retro_load_game.argtypes, core.retro_load_game.restype = [C.POINTER(GameInfo)], C.c_bool
        if not core.retro_load_game(C.byref(info)):
            raise ValueError("mGBA refused the local ROM")
        loaded = True
        captures = []
        for frame in range(1, frames + 1):
            # Start leaves the title screen; A advances the original game's menus/dialogue.
            buttons.clear()
            if press_every and frame % press_every in (0, 1):
                buttons.update((3, 8))  # RetroPad START / A
            core.retro_run()
            if frame % capture_every == 0 and framebuffer is not None:
                name = f"frame-{frame:06d}.png"
                (output / name).write_bytes(png_frame(*framebuffer))
                captures.append(name)
        report = {"emulator": system.name.decode(), "version": system.version.decode(),
                  "core_sha256": hashlib.sha256(core_path.read_bytes()).hexdigest(),
                  "rom_sha256": hashlib.sha256(rom_path.read_bytes()).hexdigest(),
                  "frames": frames, "press_every": press_every, "captures": captures,
                  "switch_verified": False, "retail_write_eligible": False}
        (output / "emulator-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if loaded:
            core.retro_unload_game()
        core.retro_deinit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=3600)
    parser.add_argument("--press-every", type=int, default=120)
    parser.add_argument("--capture-every", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.frames <= 36000 or not 0 <= args.press_every <= 36000 or args.capture_every < 1:
        parser.error("invalid frame/input interval")
    run(args.core, args.rom, args.output_dir, args.frames, args.press_every, args.capture_every)


if __name__ == "__main__":
    main()
