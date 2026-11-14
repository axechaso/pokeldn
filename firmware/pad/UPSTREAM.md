# Upstream source

Imported from [Decryptu/pokeldn v0.65.3](https://github.com/Decryptu/pokeldn/tree/dfbf83fd5a5bc86c16e337bc4b6107725c607bac/firmware/pad),
commit `dfbf83fd5a5bc86c16e337bc4b6107725c607bac`. The starting firmware version was `1.3.0`;
this project's classic ESP32 build is `1.3.1`. Sources remain under the repository's AGPL-3.0 license.

The target is restricted to the original ESP32 with Bluetooth Classic. Changes include truncated
HID subcommand guards, bounded SPI replies, checked NVS/UART initialization, interruptible macro
stop/reload, an accurately named restart command and explicit virtual cable unplugging to pair with
BlueRetro after another host. The desktop serial client and macro compiler
start from the same upstream release and are integrated with this branch's managed child processes.

CI builds the tracked sources using ESP-IDF v6.1. `scripts/package_controller_firmware.py` packages
the merged image with its target, app identity, source commit and SHA-256 manifest; the installer
verifies them before invoking esptool. BlueRetro code and prebuilt upstream images are not linked
into this firmware. The unmodified BlueRetro v25.10-beta Nintendo receiver is included only as a
[protocol test fixture](../../tests/fixtures/blueretro/README.md) under its Apache-2.0 license.
Setup and hardware validation limits: [Controller board](../../docs/hardware_pad.md).
