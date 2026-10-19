#!/usr/bin/env python3
"""BPRE 0x0A PokeLDN screen demo or read-only ROM capture; no local game file needed."""

import argparse
import functools
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "bin"):
    sys.path.insert(0, str(path))

import frlg_mg_host
from pokeldn.frlg import host_cli
from pokeldn.frlg.link import trade_runtime
from pokeldn.frlg.localization import LocalizationError
from pokeldn.frlg.localization.gift_transport import GiftConnectionClosed
from pokeldn.frlg.localization.host import LocalizationHostApplication
from pokeldn.frlg.localization.probe import LocalizationProbeOperation
from pokeldn.ldn import ldn_mitm_host, transport
from pokeldn import progress


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-config", type=Path)
    parser.add_argument("--no-local-config", action="store_true")
    parser.add_argument("--keys", type=Path)
    parser.add_argument("--channel", type=int)
    parser.add_argument("--ot", help="host trainer name from the client settings")
    parser.add_argument("--id", help="host trainer TID:SID from the client settings")
    parser.add_argument("--radio", help="defaults to existing POKELDN_RADIO or esp32:auto")
    parser.add_argument("--over-ip", nargs="?", const="auto")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--display-demo", action="store_true",
                        help="show a temporary Chinese message on the NS; wait for the English result before pressing A")
    parser.add_argument("--rom-size", type=lambda x: int(x, 0), default=0x1000000)
    parser.add_argument("--dump-rom", type=Path, help="optional local runtime capture; never an original-ROM claim")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--report", type=Path)
    output.add_argument("--output-dir", type=Path, help="new folder for the report and runtime capture")
    return parser


def host_config(args):
    forwarded = []
    for name in ("config", "local_config", "keys", "channel", "over_ip", "ot", "id"):
        value = getattr(args, name)
        if value is not None:
            forwarded += ["--" + name.replace("_", "-"), str(value)]
    for name in ("no_local_config", "verbose"):
        if getattr(args, name):
            forwarded.append("--" + name.replace("_", "-"))
    file_config, shared, local = host_cli.load_host_file_config_from_argv(forwarded)
    parser = frlg_mg_host.build_parser(file_config, shared_path=shared, local_path=local)
    parsed = parser.parse_args([*forwarded, "--live", "--buffer-script", "memory-dump",
                                "--dump-address", "0x08000000", "--dump-size", "192",
                                "--console-build", "BPRE"])
    return frlg_mg_host.build_run_config(parser, parsed)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    old_radio = os.environ.get("POKELDN_RADIO")
    try:
        if args.display_demo and args.dump_rom is not None:
            raise LocalizationError("the NS display demo does not capture a ROM")
        if args.output_dir is not None:
            if args.dump_rom is not None:
                raise LocalizationError("--output-dir already chooses the runtime capture path")
            if args.output_dir.exists():
                raise LocalizationError("probe output directory already exists")
            args.output_dir.mkdir()
            args.report = args.output_dir / "report.json"
            if not args.display_demo:
                args.dump_rom = args.output_dir / ("runtime.gba" if args.rom_size == 0x1000000
                                                  else "runtime-sample.bin")
        if args.report.exists():
            raise LocalizationError("probe report output already exists")
        if not args.report.parent.is_dir():
            raise LocalizationError("probe report parent directory does not exist")
        if args.dump_rom is not None and args.dump_rom.resolve() == args.report.resolve():
            raise LocalizationError("probe report and ROM capture need different output paths")
        os.environ["POKELDN_RADIO"] = args.radio or old_radio or "esp32:auto"
        config = host_config(args)
        factory = transport.HostTransport
        if args.over_ip is not None:
            factory = functools.partial(ldn_mitm_host.IpHostTransport,
                                         our_ip=None if args.over_ip == "auto" else args.over_ip)
            factory.NEEDS_RADIO = False
        if args.display_demo:
            from pokeldn.frlg.localization.ns_display import LocalizationDisplayOperation
            operation = LocalizationDisplayOperation()
        else:
            operation = LocalizationProbeOperation(
                rom_size=args.rom_size, dump_path=args.dump_rom,
                progress=lambda done, total: progress.show(done, total, "Reading runtime ROM"))
        app = LocalizationHostApplication(config, operation, transport_factory=factory,
                                           log=trade_runtime.ConsoleLog(args.verbose))
        try:
            app.run()
        except GiftConnectionClosed:
            if app.interrupted:
                return 130
            if app.idle_timed_out:
                return 124
            raise
        with args.report.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(app.operation_result, handle, indent=2)
            handle.write("\n")
        if args.display_demo:
            print(f"NS display payload acknowledged. Confirm the Chinese screen visually. Report: {args.report}")
        else:
            print(f"Read-only runtime fingerprint written to {args.report}")
        return 0
    except (LocalizationError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"localization probe stopped: {exc}\n")
    finally:
        if old_radio is None:
            os.environ.pop("POKELDN_RADIO", None)
        else:
            os.environ["POKELDN_RADIO"] = old_radio


if __name__ == "__main__":
    sys.exit(main())
