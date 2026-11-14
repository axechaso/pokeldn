"""Inspect and install the classic ESP32 controller image built by this project."""
import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path

from pokeldn.app.paths import ROOT

VERSION = "1.3.1"
PROJECT = "pokeldn_pad"
IMAGE_NAME = "pokeldn-pad.bin"


def require_classic(chip: str) -> None:
    if chip != "esp32":
        raise ValueError("this Bluetooth controller firmware requires a classic ESP32; "
                         "ESP32-S3/C3/C6 cannot emulate this Bluetooth Classic Pro Controller")


def image_metadata(path: Path) -> dict:
    data = Path(path).read_bytes()
    # The classic merged image has its bootloader at 0x1000 and its app at 0x10000.
    # Both image headers must declare the original ESP32 chip ID, which is zero.
    for offset in (0x1000, 0x10000):
        if (len(data) < offset + 24 or data[offset] != 0xE9
                or struct.unpack_from("<H", data, offset + 12)[0] != 0):
            raise ValueError("expected a merged classic ESP32 image (bootloader + partition table + app)")
    if struct.unpack_from("<H", data, 0x8000)[0] != 0x50AA or not any(
            data[offset + 2] == 0 and struct.unpack_from("<I", data, offset + 4)[0] == 0x10000
            for offset in range(0x8000, 0x8FE0, 32)
            if struct.unpack_from("<H", data, offset)[0] == 0x50AA):
        raise ValueError("expected a partition table with a controller app at 0x10000")
    app_desc = 0x10000 + 32
    if len(data) < app_desc + 144 or struct.unpack_from("<I", data, app_desc)[0] != 0xABCD5432:
        raise ValueError("controller app descriptor is missing")

    def field(offset, size):
        return data[app_desc + offset:app_desc + offset + size].split(bytes([0]), 1)[0].decode("ascii")

    version, project = field(16, 32), field(48, 32)
    if project != PROJECT or version != VERSION:
        raise ValueError(f"expected {PROJECT} {VERSION}, got {project} {version}; build firmware/pad first")
    return {"format": "pokeldn-controller", "chip": "esp32", "project": project,
            "version": version, "flash_address": 0, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "idf_version": field(112, 32)}


def write_manifest(path: Path, revision: str = "") -> Path:
    path = Path(path)
    info = image_metadata(path)
    if revision:
        info["source_commit"] = revision
    manifest = path.with_suffix(".json")
    manifest.write_text(json.dumps(info, indent=2) + chr(10), encoding="utf-8")
    return manifest


def verify_image(path: Path) -> dict:
    path = Path(path)
    actual = image_metadata(path)
    manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in actual.items()):
        raise ValueError("controller image metadata or SHA-256 does not match its build manifest")
    return manifest


def controller_image(chip: str = "esp32") -> Path:
    require_classic(chip)
    for path in (Path(ROOT) / "gui/firmware" / IMAGE_NAME,
                 Path(ROOT) / "firmware/pad/build" / IMAGE_NAME):
        if path.is_file():
            try:
                verify_image(path)
            except (OSError, ValueError):
                continue
            return path
    raise FileNotFoundError("Missing a verified classic ESP32 controller build. Build firmware/pad, "
                            "then run scripts/package_controller_firmware.py on its merged image.")


def flash_args(port: str, chip: str, path: Path, from_loader: bool = False) -> list[str]:
    require_classic(chip)
    verify_image(Path(path))
    return ["--chip", "esp32", "--port", port, "--baud", "460800",
            "--before", "no-reset" if from_loader else "default-reset",
            "--after", "hard-reset", "write-flash", "0x0", str(Path(path).resolve())]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chip", choices=("esp32", "esp32s3", "esp32c3", "esp32c6"), default="esp32")
    parser.add_argument("--image", type=Path, help="merged controller .bin with an adjacent build .json")
    parser.add_argument("--flash", action="store_true", help="install controller firmware (replaces the board's firmware)")
    parser.add_argument("--port", help="serial device, e.g. COM5")
    parser.add_argument("--from-loader", action="store_true", help="the board is already in its ROM loader")
    args = parser.parse_args(argv)
    if args.flash and not args.port:
        parser.error("--flash requires --port")
    try:
        require_classic(args.chip)
        path = args.image or controller_image()
        info = verify_image(path)
        print(f"{info['project']} {info['version']}: {path.resolve()} (SHA-256 verified)", flush=True)
        if args.flash:
            from pokeldn.app.runner import command
            return subprocess.run(command("--module", "esptool",
                                          *flash_args(args.port, args.chip, path, args.from_loader)), check=False).returncode
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"{error}" + chr(10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
