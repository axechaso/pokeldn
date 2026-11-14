---
title: Controller board
parent: Hardware and setup
nav_order: 5
---

# 经典 ESP32 蓝牙手柄固件

固件 `1.3.1` 将经典 ESP32 模拟为 Switch Pro Controller。连接方向是：

```text
电脑 Controller 页面／CLI ── USB 串口 ── 经典 ESP32 ── Bluetooth Classic ── Switch
```

使用带 CP2102／CH340 USB 串口的 ESP32-WROOM-32、WROOM-32E 或 ESP32-D0WD 开发板。
本固件仅支持原版 ESP32；S3、C3、C6 不具备所需的 Bluetooth Classic。
手柄操作不需要 `prod.keys`。同时进行无线交换时，应另接一块运行原有 radio 固件的板子。

## 安装与配对

1. 将经典 ESP32 通过 USB 数据线接到电脑，打开桌面 **Controller** 页，刷新并选择对应串口。
2. 点击 **Install controller firmware**。程序校验镜像版本、芯片、分区和 SHA-256 后刷写。
   安装会替换该板的固件并清除原有配对。esptool 会检查实际芯片，不使用 `--force`。
3. Switch 打开“手柄 → 更改握法／顺序”，等待名为 **Pro Controller** 的手柄完成配对。
   蓝色 GPIO2 LED 在配对完成后亮起；后续启动会尝试重连已配对主机。
4. 点击 **Connect**。状态显示固件版本、Switch 连接情况和宏进度。可发送按键、控制双摇杆，
   或打开 `.pokemacro` 并点击 **Play on board**。
5. **Stop macro and release buttons** 停止宏并释放按键。切换页面会释放实时按键；板上宏继续运行。

刷写连接失败时，按住 BOOT 再开始安装，写入开始后松开。已通过 BOOT 进入下载模式的板子可勾选
**Already in ROM loader**。手柄运行时仍通过电脑 USB 供电和收串口指令，无须连接 Switch USB。

从源码运行：`python -m pip install -r gui/requirements.txt`，然后 `python gui/main.py`。
须先按下面步骤构建并打包固件到 `gui/firmware`，或将 CI 构建产物中的 `.bin` 和 `.json` 一起复制进去。

## 构建和校验

源码在 `firmware/pad`，默认目标是 `esp32`；其他目标在 CMake 配置阶段即被拒绝。
激活 ESP-IDF **v6.1** 环境后，从仓库根目录执行：

```sh
idf.py -C firmware/pad build
idf.py -C firmware/pad merge-bin -o pokeldn-pad.bin
python scripts/package_controller_firmware.py firmware/pad/build/pokeldn-pad.bin
python -m pokeldn.firmware
```

打包脚本默认输出 `gui/firmware/pokeldn-pad.bin`、`pokeldn-pad.json` 和 `SHA256SUMS`；
可用 `--output-dir dist/controller --revision COMMIT` 指定输出目录和源码提交。
镜像合并刷写地址为 **0x0**，内部 bootloader 位于 `0x1000`，分区表位于 `0x8000`，应用位于 `0x10000`。
安装器检查原版 ESP32 芯片 ID、应用名称 `pokeldn_pad`、版本 `1.3.1`、分区表和清单的 SHA-256。

GitHub Actions 的 **Controller firmware** 工作流构建并上传 `classic-esp32-controller` 产物，
包含镜像、清单和校验文件。Release 工作流也从这些源码编译并将镜像放入桌面包。

## 命令行

先校验产物，再按实际端口刷写（Linux/macOS 使用相应的 `/dev/...`）：

```sh
python -m pokeldn.firmware --image dist/controller/pokeldn-pad.bin
python -m pokeldn.firmware --image dist/controller/pokeldn-pad.bin --flash --port COM5
```

明确指定手柄串口，避免探测正在交换的无线板：

```sh
python tools/switch/pad.py --serial COM5 A
python tools/switch/pad.py --serial COM5 HOME wait:1 'RIGHT*3' A
python tools/switch/pad.py --serial COM5 hold:B:2
python tools/switch/pad.py --serial COM5 stick:L:-1:0:0.5
python tools/switch/pad.py --serial COM5 --play config/macros/controller_test.pokemacro
python tools/switch/pad.py --serial COM5 --stop --status
```

`A+B` 同时按下两个键，`hold:B:2` 持有 B 两秒，`stick:L:-1:0:0.5` 将左摇杆向左推半秒。
宏的 `setup` 执行一次，`loop` 根据 `loops` 重复，`loops: 0` 持续到手动停止。
板子最多保存 8192 个编译步骤；电脑会先校验宏格式和长度。停止、重载和重新播放会中断当前长按。

需要持续占用端口时，另开终端执行 `python -m pokeldn.pad.service --serial COM5`。
后续 `pad.py` 命令可以复用该服务；桌面会自行启动服务，并在桌面关闭时结束子进程。
同一时间只让一个服务占用一块板。`--restart` 仅重启固件；进入 ROM 下载模式需使用 BOOT 或 esptool。

## 串口与蓝牙实现

UART0 使用 **921600 baud、8N1**，GPIO1 TX、GPIO3 RX。帧格式：
`A5 5A | 长度 u16 LE（含类型）| 类型 u8 | 负载 | 类型和负载字节和 mod 256`。
请求类型 `0x01` 为 8 字节按键报告，`0x02` 为状态，`0x03` 为宏／控制命令；
响应 `0x81` 为结果码，`0x82` 为状态。Python 客户端和桌面共享本地 `127.0.0.1:47800` 控制服务。

Bluetooth Classic HID 提供 Pro Controller 报告、Switch 配对握手、虚拟 SPI 校准和按键／摇杆编码。
实现来自 [pokeldn v0.65.3](https://github.com/Decryptu/pokeldn/releases/tag/v0.65.3) 的经典 ESP32
方案，本分支修复了截断子命令、SPI 返回长度和宏停止／重载问题，并增加构建校验与桌面适配。
源码来源和许可证见 [UPSTREAM.md](../firmware/pad/UPSTREAM.md)。BlueRetro 是接收蓝牙手柄的方案，
本固件未使用它的代码或发布镜像。

离线检查覆盖宏编译、串口交互、Pro Controller 报告、截断子命令、长按停止／重载和安装校验。
本次开发尚未对实体板刷写或完成 Switch 配对、按键及重连验收；Switch 2 兼容性也需实机确认。
