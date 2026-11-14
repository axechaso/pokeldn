---
title: Desktop builds
---
# Desktop builds

Released apps include PKHeX and the merged ESP32 radio firmware. Users provide their own `prod.keys`.

The interface bundles the free MIT SVG set from Pixelarticons 2.4.1 in `gui/assets/icons/`, with its
license. `gui/icons.py` renders the unchanged paths at the shared UI icon sizes and applies the
interface colors through Flet's image tint.

Shared control sizing and button styles live in `gui/theme.py`. Compact icons use the default size
in `gui/icons.py`; the navigation rail uses a larger size. `CodeBlock` in `gui/views/widgets.py`
provides the code display and copy action for board setup, session commands and fenced Docs examples.

For source development, install Python 3.13 and the .NET 10 SDK:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r gui/requirements.txt
dotnet build -c Release services/pkhex -warnaserror
python gui/main.py
```

Use the virtual environment's Python for these commands. The CLI shares the desktop tool presets:

```sh
python -m pokeldn --list
python -m pokeldn --radio esp32:auto swsh-host --offer-file offer.pk8
```

Arguments after the tool name override scalar preset options. Direct `bin/` entry points remain
available for protocol experiments. Offer files go through the same PKHeX validation.

To package an app, install ESP-IDF v6.1 and activate its environment, then build the tracked firmware:

```sh
mkdir -p gui/firmware
idf.py -C firmware/esp32 build
idf.py -C firmware/esp32 merge-bin -o ../../../gui/firmware/pokeldn-radio.bin
idf.py -C firmware/pad build
idf.py -C firmware/pad merge-bin -o pokeldn-pad.bin
python scripts/package_controller_firmware.py firmware/pad/build/pokeldn-pad.bin
python scripts/pack_app.py
```

The output path is relative to the firmware build directory.
The release workflow builds both firmware projects and supplies their images to each desktop packer.
The controller image is bundled with its version, target and SHA-256 manifest. The packer rejects
missing or mismatched controller builds; installation uses the bundled image.

## The controller

The Controller page installs this project's classic ESP32 controller firmware, connects over USB
serial, sends buttons and plays `.pokemacro` files on the board. Pair with BlueRetro removes the
previous HID virtual cable/bond so the simulated Pro Controller can connect to the BlueRetro receiver.
The firmware also retains its existing Switch protocol. Choose its port explicitly and use a separate board for wireless trades.
See [Controller board](hardware_pad.md) for installation and CLI use.
