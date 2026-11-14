# BlueRetro receiver compatibility fixture

`upstream/sw.c`, `upstream/sw.h`, `upstream/hidp.h` and `upstream/LICENSE` are unmodified files from
[darthcloud/BlueRetro v25.10-beta](https://github.com/darthcloud/BlueRetro/tree/243ba40a0a632285b5b7171cbbb1aa61a8cd0ce7),
commit `243ba40a0a632285b5b7171cbbb1aa61a8cd0ce7`. The three sources originate in
`main/bluetooth/hidp/`; their Apache-2.0 notices and license are preserved.

`host.h` and `host.c` replace the radio, timers and host environment so tests can run the actual
BlueRetro Nintendo controller initialization and report receiver against pokeldn's emulated device.
No BlueRetro source is linked into the released controller firmware or desktop app. The test proves
protocol compatibility with this receiver revision; RF pairing and wired console output need hardware.
