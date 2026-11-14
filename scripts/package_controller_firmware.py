#!/usr/bin/env python3
"""Package a built classic ESP32 merged image with its identity and SHA-256 manifest."""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokeldn.firmware import IMAGE_NAME, image_metadata, write_manifest, verify_image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="merged firmware/pad/build/pokeldn-pad.bin")
    parser.add_argument("--output-dir", type=Path, default=Path("gui/firmware"))
    parser.add_argument("--revision", default="", help="source commit recorded in the manifest")
    args = parser.parse_args(argv)
    try:
        image_metadata(args.image)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output_dir / IMAGE_NAME
        if args.image.resolve() != destination.resolve():
            shutil.copy2(args.image, destination)
        manifest = write_manifest(destination, args.revision)
        info = verify_image(destination)
        sums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                       for path in (destination, manifest))
        (args.output_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
        print(f"Packaged {info['project']} {info['version']}: {destination.resolve()}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
