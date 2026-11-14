#!/usr/bin/env python3
"""Press a Switch's buttons through the controller board (firmware/pad, docs/hardware_pad.md).

    pad.py A                      press A
    pad.py HOME wait:1 A          steps run in order
    pad.py 'down*3' A             a step repeated
    pad.py hold:B:2               hold B for 2 s
    pad.py stick:L:-1:0:0.5       left stick fully left for 0.5 s
    pad.py --play FILE.pokemacro  load a macro onto the board and start it
    pad.py --stop                 stop the board's macro
    pad.py --status               mounted, macro progress, firmware version
    pad.py --restart              restart the board's firmware
    pad.py --pair                 forget the last host and pair with BlueRetro or a new console

A step list runs from this host, a macro on the board's own clock. Requests go to the controller
service (pokeldn.pad.service) on 127.0.0.1, which this starts in-process when none is running.

Select the classic ESP32 explicitly with --serial COM5 (or a /dev/... port). The board stays
plugged into the computer and emulates a Pro Controller for BlueRetro or a Switch.
"""
import argparse
import asyncio
import os
import pathlib
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pokeldn.pad import macro, service  # noqa: E402


def parse(step):
    """One step -> (report, seconds) pairs."""
    name, _, count = step.partition("*")
    times = int(count) if count else 1
    parts = name.split(":")
    head = parts[0].upper()
    if head == "WAIT":
        return [(None, float(parts[1]))] * times
    if head == "HOLD":
        return [(macro.state(parts[1].split("+")), float(parts[2])), (macro.NEUTRAL, 0.1)] * times
    if head == "STICK":
        side, xy = parts[1].upper(), (float(parts[2]), float(parts[3]))
        secs = float(parts[4]) if len(parts) > 4 else 0.3
        r = macro.state(left=xy) if side == "L" else macro.state(right=xy)
        return [(r, secs), (macro.NEUTRAL, 0.1)] * times
    try:
        return [(macro.state(head.split("+")), 0.1), (macro.NEUTRAL, 0.15)] * times
    except macro.MacroError as e:
        raise SystemExit(f"{e}; known: {', '.join(macro.KEYS)}")


def client(serial_port=""):
    if not service.running():
        if not serial_port:
            raise service.ServiceError("select the controller port with --serial COM5")
        threading.Thread(target=lambda: asyncio.run(service.serve(serial_port=serial_port)),
                         daemon=True).start()
        for _ in range(50):
            if service.running():
                break
            time.sleep(0.1)
    result = service.Client()
    if serial_port:
        wanted = f"serial {serial_port}"
        status = result.call("status")
        if status.get("selector") != wanted:
            result.close()
            raise service.ServiceError("the running controller service uses another link; disconnect it first")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("steps", nargs="*")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--play", metavar="FILE", help="load a .pokemacro onto the board and start it")
    ap.add_argument("--stop", action="store_true", help="stop the board's macro")
    ap.add_argument("--restart", action="store_true", help="restart the board's firmware (not the ROM loader)")
    ap.add_argument("--pair", action="store_true", help="forget the last Bluetooth host; pair with BlueRetro/a new console")
    ap.add_argument("--serial", default="", help="classic ESP32 port; required unless a service is already running")
    args = ap.parse_args()
    plan = [item for step in args.steps for item in parse(step)]
    text = pathlib.Path(args.play).read_text(encoding="utf-8") if args.play else None
    if text:
        macro.compile_macro(macro.loads(text))
    try:
        c = client(args.serial)
    except (OSError, service.ServiceError) as error:
        ap.exit(1, f"{error}\n")
    try:
        if args.pair:
            c.call("pair")
            print("Previous host unplugged; start controller inquiry on BlueRetro to pair this Pro Controller.")
            return
        if args.restart:
            c.call("restart")
            print("restarting the controller firmware")
            return
        if args.stop:
            c.call("stop")
        if text:
            r = c.call("play", macro=text)
            loops = "until stopped" if r["loops"] == 0 else f"{r['loops']} time(s)"
            print(f"playing {r['entries']} entries; setup {r['setup_ms']} ms, loop {r['loop_ms']} ms, {loops}")
        for report, secs in plan:
            if report is not None:
                c.call("send", report=report.hex())
            time.sleep(secs)
        if plan:
            c.call("send", report=macro.NEUTRAL.hex())
        if args.status or not (plan or text or args.stop):
            c.call("connect")
            s = c.call("status")["status"]
            print(f"Bluetooth host ready {'yes' if s['mounted'] else 'no'}, writes {s['writes']}, "
                  f"{'playing' if s['playing'] else 'idle'} entry {s['index']}/{s['count']} "
                  f"loops done {s['loops_done']}, firmware {s['version'] or 'before 1.0.0'}, "
                  f"reset {s['reset_reason']} previous boot stage {s['stage_before']} boots {s['boots']}")
    except service.ServiceError as e:
        sys.exit(str(e))
    finally:
        if plan:
            try:
                c.call("send", report=macro.NEUTRAL.hex())
            except (OSError, service.ServiceError):
                pass
        c.close()


if __name__ == "__main__":
    main()
