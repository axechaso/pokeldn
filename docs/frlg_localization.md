---
title: 汉化运行时补丁工具
parent: FireRed and LeafGreen
nav_exclude: true
---

# 汉化运行时补丁工具

2026-10-19 更新。已交付[汉化方案](pokeldn_localization_implementation_plan_zh.md)的软件基础：版本配置、差分包、ARM 页载荷、恢复日志，以及接入现有 PokeLDN RFU／Mystery Gift 主机的持续多步骤适配器。另有 26 字、两页对话的离线字库与排版预览。**目前不能让 Switch 游戏显示中文**，也没有经过本项目验证的零售版本配置。桌面客户端提供只读连接测试和完整运行时 ROM 采集，CLI 继续提供相同采集入口及离线工具；实际写入适配器为库接口，保留独立观测的版本与写入门禁。

| 阶段 | 当前状态 |
| --- | --- |
| P0 离线核心 | 已实现并有自动回归；真实 ROM／包装器及暂存区证据尚待补齐 |
| P1 修改版 Ryubing 复现 | 未执行；Unicorn 软件模型不等于 Switch 包装器模拟器 |
| P2 零售机单页验证 | 4 KiB 实机只读采集及正常断开已通过；页写入、恢复与取指尚待验收 |
| P3 中文编码、字库、渲染 | 有字库子集、Unicode IR、像素排版和离线预览；游戏编码及完整渲染链未接入 |
| P4 内容、P5 插件 | 客户端已有只读测试入口；正式内容及完整汉化插件未实现 |

## 上游与固件基线

2026-10-19 从 GitHub 重新获取并同步上游 `main`，固定提交为
`f513dd0d5e1150aaa48fe99d165b0cec49c8c249`。它包含最新稳定版
[v0.31.4](https://github.com/Decryptu/pokeldn/releases/tag/v0.31.4)
（`8ff92a4c235a312c965fb2f8517bda5c63d9e35f`）及其后的主线提交。
原分支基于 `b7ec482`，相对这次主线缺少 557 个提交；仅核对汉化提交是否改过固件，
不能证明电脑端已与新固件对齐。

当前电脑端版本为 `0.31.4`，配套无线固件为 `1.6.2`（`firmware/esp32/version.txt`），
串口协议号仍为 `1`。固件版本和协议号是不同字段，`protocol 1` 本身不代表固件最新。
汉化工具沿用同步后的无线传输实现，没有单独修改 ESP32 固件。已经安装该配套固件的板子可直接测试。
本次在 `COM8` 执行实际串口 HELLO，读取到 `protocol=1` 及
`pokeldn-radio esp32 version=1.6.2 idf=v6.1`，没有刷写固件。这只确认板载版本和串口握手，
不代表已通过 Switch 会话、ROM 采集或实机页写入验收。

同日随后在用户的实际 Switch 上完成客户端快速测试：`BPRE 0x0A` 的 4,096 字节采集，
GBA 头校验和正确，保存文件 SHA-256 与报告一致（`a5c8d8f89e801d468cc5545620fc11edea0f91982bd7049eb03cbcaf58af0800`）。
日志依次出现 `READ_ONLY_CAPTURED`、正常 RFU／Pia／LDN 关闭、退出码 0。该样本读取约 40.9 秒，
此耗时不含等待用户加入、初次读头和断开流程。它验证了实际只读链路，仍不确认完整 ROM、包装器或写入资格。

随后首次尝试 64 KiB 时失败：192 字节读头完成后，32 KiB 批次所需的较长客户端脚本持续重传，
从开始发送到主机进入 `MG_CLOSE` 恰好 30 秒，正文文件为 0 字节，没有成功报告。
已将默认 RPC 批次和落盘批次改为 4 KiB，完整客户端脚本／载荷发送也计入进展；
失败时先发关闭轮询再断开 RFU，主机直接报告工作线程错误并释放网络，不再等待正常礼物收尾。
新报告记录 `read_batch_bytes=4096`。此修复仍需同一台实机重试 64 KiB；失败产物不覆盖先前有效的 4 KiB 样本。

同步保留了上游的 USB 断线／信用窗口处理、Mystery Gift／存档传输入口，以及可选板载屏幕支持。
汉化主机改用共用的 `_build_engine` 扩展点，并提供不依赖 Wonder Card 的屏幕内容。

## 在客户端测试

这次开发版客户端的 **Games → FireRed & LeafGreen → Localization test** 提供按钮入口。
沿用 Board 页的无线板和 Settings 中的密钥，不需要手动填写命令或输出路径。
本机的 **PokeLDN 汉化测试** 桌面快捷方式会直接打开此页；源码中的对应启动入口为
`gui/localization_test.py`。

默认 **Quick connection test (4 KiB)** 先验证真实无线读取；点 Start 后，在英文火红选择
**Mystery Gift → Wonder Cards → Friend → POKELDN**，等待 Session 显示
**Quick connection test passed**。这份 4 KiB 样本只用于连接测试，不是完整 ROM。

连接通过后，下一步选 **Capture text engine (64 KiB)**：只采集运行时 ROM 的前 64 KiB，
用来研究文字解码、字形绘制、字宽、字符串复制和控制符处理。报告附带 17 个函数窗口的地址和哈希，
地址由上游英文 ELF 名称映射推导，保持 `symbol_addresses_verified=false`；仍需将实际指令与反编译核对。
它不含完整字库和剧情资源，不授予写入资格，也不表示中文已经能显示。

Session 每收到 1 KiB 就更新进度，完成后显示 **Text engine captured** 和实际读取耗时。
根据这次快速测试的速率，64 KiB 粗略为十分钟量级；实际耗时取决于链路和重传。
完整 **Capture runtime ROM (16 MiB)** 仍保留，但在该速率下线性外推为数十小时，暂不作为下一次必做测试。
Session 显示读取进度、
完成状态、SHA-256 和 **Open output folder** 按钮。每次运行自动在 Received 下新建独立目录，
保存 `report.json` 及 `runtime-sample.bin`（快速测试／文本引擎）或 `runtime.gba`（完整采集）。
客户端会重新计算文件 SHA-256，损坏或未完成的副本不会显示为成功。
两次运行之间先在 Switch 退出搜索页面再进入。当前入口只读；游戏内中文显示仍未接通。

## 命令行采集运行时 ROM

不需要事先提供 ROM 文件。沿用 `config/host.toml`、可选本机配置和已有密钥位置，默认连接 `POKELDN_RADIO` 指定的无线板，未设置时使用 `esp32:auto`：

```powershell
python tools/frlg/localization_probe.py --report probe.local.json
```

主机开始广播后，在既定英文火红 BPRE `0x0A` 的 **Mystery Gift → Wonder Cards → Friend** 连接。工具交叉检查链路身份与 GBA ROM 头，分批读取 16 MiB ROM，输出 `runtime_sha256`。`--dump-rom C:/private/runtime.gba` 可同时保存本机副本；`--over-ip auto` 沿用现有模拟器 IP 传输路径。其他可选项包括原有配置文件、密钥和信道覆盖，均使用现有 PokeLDN 配置构建器。

报告与副本路径必须不存在，父目录须已存在。采集中断时可能留下部分副本，只有正常完成后的报告才表示整段读取结束。报告固定声明 `original_rom_verified=false`、`wrapper_identity_verified=false`、`write_eligible=false`。运行时副本可能含包装器启动补丁，不能直接宣称为原始卡带或自动获得写入资格。此入口只发内存读取与客户端回读游标重置载荷，不发 ROM 页提交、存档写入或礼物安装载荷。

## 运行软件闭环

在仓库根目录，安装 `requirements-dev.txt` 后执行：

```powershell
python tools/frlg/localization_load.py self-test --output-dir scratchpad/zh-smoke-001
```

输出目录必须不存在。此命令不需要游戏、密钥、存档或无线设备，生成一份人工构造的 64 KiB 测试图像，在内存中完成三页加载、重复加载以及逆序恢复。测试图像不写到输出文件。

输出 `profile.json`、`patch.json`、`report.json` 和 `recovery.local.json`。报告应包含 `loaded.state=COMPLETE`、`restored.state=RESTORED`、`rom_page_writes=6`，以及 `software_only=true`、`ryubing_verified=false`、`retail_verified=false`、`chinese_rendering_verified=false`。恢复日志包含完整原始页，应仅保留在本机。

软件模型执行实际汇编出来的 ARM／THUMB 载荷；ROM 写入 syscall 由模型显式实现。它只能验证主机算法和载荷的内部一致性，不能证明真实包装器接受写入、缓存行为一致、无线多步会话可用或实机稳定。示例中的地址和身份全部用于模型，不能移作实机配置。

## 离线构建与检查

先读取本机原始 ROM 的游戏代码、修订号、大小和 SHA-256：

```powershell
python tools/frlg/localization_build.py fingerprint C:/private/FireRed.gba
```

结果固定标明 `write_eligible=false`；仅有游戏代码和文件哈希不能识别运行中的 Switch 包装器。

真实配置由研究者提供，结构以 `self-test` 输出的 profile 为例，但每项都必须替换为经验证的实际证据：

- `identity`：游戏代码、ROM 修订号、Switch 标题 ID、更新版本、包装器 SHA-256。现有地址表仅允许客体修订 `0x0A`；不能使用 `unknown` 代替身份。
- `rom_size`、`base_sha256`：完整原始 ROM，尺寸为 4 KiB 对齐的二次幂，最多 16 MiB。
- `allowed`、`protected`：ROM 文件偏移范围及证据。保护范围覆盖整页提交；同页未修改的保护字节也会阻止写入。ROM 头页和已知的客户端执行页始终禁止提交。
- `startup_changes`：包装器每处启动改动的偏移、原始字节和运行时字节。补丁不能覆盖这些改动。
- `staging`：经证实在整个通信会话中可保留的 4 KiB 数据区和独立 64 字节状态区。两者必须位于有证据的 EWRAM 分配内，不能互相重叠或触及接收载荷、通信对象、Resident／RNG 等保护区。

`edits.json` 是精确字节改动的数组，例如：

```json
[
  {"offset": 8192, "before": "1122", "after": "3344", "kind": "resource"}
]
```

这个偏移和字节只是格式示例。`kind` 为 `resource`、`code` 或 `activation`。构建器验证原始字节，将跨页改动分开，保持所有未改动字节；不同种类不得混在同一页。资源和代码排在入口激活页之前。

```powershell
python tools/frlg/localization_build.py build --profile C:/private/profile.json --base C:/private/FireRed.gba --edits C:/private/edits.json --output C:/private/zh.patch.json
python tools/frlg/localization_load.py check --profile C:/private/profile.json --base C:/private/FireRed.gba --patch C:/private/zh.patch.json
python tools/frlg/localization_load.py simulate --profile C:/private/profile.json --base C:/private/FireRed.gba --patch C:/private/zh.patch.json --journal C:/private/model.frlgzh-recovery.json
```

构建输出和模拟日志都必须使用新路径。`build` 可用 `--resources`、`--compatibility` 载入额外 JSON 元数据；这些声明不作为设备已验证的证明。`simulate` 仅在软件模型内加载、重复加载、恢复。`check` 不连接设备，不确认包装器身份，也不授予实机写入资格。

差分包包含格式版本、目标 profile 及其哈希、原始 ROM 哈希、各页原始／目标哈希、精确差分、激活顺序、资源和兼容元数据。`patch_id` 是清单规范化后的 SHA-256。解析拒绝重复 JSON 键、错误哈希、重叠改动、重复页、改动越界和错误激活顺序。包中不带完整 ROM、密钥、存档或恢复页。

## 主机与客体的约束

`pokeldn/frlg/localization/` 包含以下核心：

| 模块 | 行为 |
| --- | --- |
| `profiles.py` | 构建身份、启动差异、ROM 权限和 RAM 布局 |
| `patch.py` | 页合并、内容身份及完整预检 |
| `codec.py` | 国际版消息的无损 token；未知选择器保留剩余字节并阻止改写 |
| `payloads.py` | 受约束 ARM 构建器，固定 512 字节净数据块 |
| `session.py` | 重试、全 ROM 重新识别、逐页回读、激活门禁和恢复日志 |
| `transport.py` | 可接入主机会话的 callback 适配器，以及 Unicorn 软件模型 |
| `gift_transport.py`、`host.py` | 持续 Mystery Gift RPC、非阻塞协议主机和现有无线/IP 运行时接入 |
| `font.py`、`dialogue.py` | OFL 字形子集、无损 Unicode/control IR、最长变量排版和离线 PNG 预览 |
| `probe.py` | 无需原始 ROM 文件的 BPRE 0x0A 只读采集与指纹报告 |

`asm/rom-page-stage.s` 为 928 字节，包含 512 字节数据；`asm/rom-page-commit.s` 为 484 字节。两者都在 1024 字节接收限制内。暂存状态包含完整的 16 字节会话 ID、32 字节补丁 ID、页号、块位图、冲突标记和 FNV-1a。乱序与相同重复块可以接受，内容冲突导致此页停止。缺块、旧会话、错误页、源或目标内容校验失败均不能提交。

主机在提交前回读暂存区并计算 SHA-256。客体的 FNV-1a 是执行边界的附加完整性检查，不是 SHA-256 的替代品。提交仅使用 `swi 0x48`；目标页只在经校验的 ROM 范围内形成对应扇区参数，保留普通 Flash 构建器的 0–31 限制及通用 Sloop 对写入 syscall 的禁令。

机制依据固定为[上游 ROM 页写入实验](https://github.com/Decryptu/pokeldn/blob/da882c7633ff106a0d2206fa2015a3538777e542/docs/frlg_rom.md#the-flash-sector-path)。该实验是在修改版 Ryubing 中进行。当前模型没有运行该构建，不继承其实验结果。

`BufferTransport` 的宿主接入需提供真实 `Observation`、内存读取和单步 buffer-script 执行 callback。读取端采用 `CLIENT_SCRIPT_DUMP_MEMORY`，执行端使用 `CLIENT_SCRIPT_RUN_BUFFER`。观察数据必须来自宿主识别与执行现场，不能从补丁清单回填。所有 callback 须在同一游戏进程和持续会话中运行。

新增的 `LocalizationGiftOperation`／`LocalizationHostApplication` 已接入现有 `HostMysteryGiftEngine`、RFU、Pia、无线板／IP 主机调度：

```python
from pokeldn.frlg.localization.gift_transport import LocalizationGiftOperation
from pokeldn.frlg.localization.host import LocalizationHostApplication

operation = LocalizationGiftOperation(
    profile, patch, base_rom, journal_path,
    observer=measured_wrapper_observer, operation="inspect",
)
application = LocalizationHostApplication(existing_pokeldn_config, operation)
application.run()
```

沿用已有 PokeLDN 配置；`observer(game_data)` 需要快速返回宿主实际观测的包装器与当前执行范围。默认 `inspect` 只读；`load` 和 `restore` 继续经过完整 ROM／页／执行现场门禁。尚未提供可从 CLI 跳过这些门禁的设备写入选项。

一次会话只交换一次游戏身份，随后可连续接收／运行多个载荷，RFU 主线程在等待主机准备时继续处理链路。工作线程处理日志和同步加载逻辑，每次仅有一个待处理 RPC。ROM／页回读默认批量接收 4 个 1 KiB 响应，响应之间按真实客户端脚本顺序处理；32 块仍是底层协议编码上限，未作为慢无线链路的默认值。每次批量回读先执行 16 字节 `client-param-reset` 载荷，防止上一次多块回读的游标污染下一次事务。采集每 4 KiB 写入并刷新本机文件。

超时按无进展时间计算；完整客户端脚本、游标重置和载荷发送完成，以及每个有效响应块，都会刷新截止时间。空闲帧和重复重传不会刷新它。短响应、超时或断线关闭本连接，唤醒等待线程并保留恢复日志；因为 `ident 19` 没有请求编号，不在旧连接上重试或把晚到响应用于新事务。成功结束采用动态消息的无保存退出路径，`operation.result` 记录操作成功，`gift_sent` 保持 false；该路径的实机菜单与存档行为仍需实际验收。

每次加载／恢复都重新读取身份和完整运行时 ROM。只允许原始页或本补丁目标页，未解释的任何差异都会停止。写前再次核对目标与当前执行范围；资源页全部回读一致后才允许入口切换。页提交应答丢失时，先回读确定结果，不盲目重写。每页也检查两侧邻页；结束时复核完整 ROM。

会话先将原始完整页落盘，再进行任何暂存或提交；`WRITE_INTENT` 在 syscall 前写入。日志更新使用同目录临时文件、flush/fsync 和替换。恢复不信任旧的“成功”状态：重新识别、回读，先撤销激活入口，再逆序恢复代码和资源。当前内容既不是原始值也不是目标值时保留日志并停止，不能自动覆盖。多页过程不提供事务原子性。

文本解析按参数长度消费控制码。特别是 `PLAY_SE (FC 10)` 的两参数、`CLEAR (FC 11)` 的单参数不能被错认成文字或终止符；参数长度对照[反编译的 GetExtCtrlCodeLength](https://github.com/pret/pokefirered/blob/master/src/string_util.c#L573)。未分配中文前缀、未更改姓名／昵称／存档／联机字段的编码。

## 最小中文素材与排版原型

```powershell
python tools/frlg/localization_text_build.py --output-dir scratchpad/zh-dialogue-001
```

默认原创对话为 26 个汉字，包含 `{PLAYER}`、换行及 `{CLEAR}`（按键后清页）。输出 `font.bin`、`font.json`、`dialogue.ir.json`、`preview.png` 和 `OFL.txt`。不需要 Pillow、游戏文件或无线设备；输出目录须不存在。可用 `--text UTF8_FILE`、`--font BITMAP_JSON` 和 `--player-name NAME` 改变输入。

字形源固定为 [Noto Sans CJK SC Regular](https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf)，源提交及 SHA-256 写入素材元数据，版权和 OFL 1.1 随子集分发。实际派生字形名为 FRLG Prototype Glyphs，来源与再生成步骤见[字形说明](../pokeldn/frlg/localization/assets/README.md)。常规构建仅使用已提交的少量位图，不下载字体，也不带完整源字体。

子集为 16×16、2bpp 行顺序位图，每字 64 字节；示例构建保留原拉丁字符回退后为 107 个字形、6,848 字节。`glyph_order` 给出 `font.bin` 内字形顺序。排版同时检查实际预览名和 7 字符、每字符最多 16 像素的最坏占位；默认示例最大行宽 168 像素，窗口限制 224 像素、每页两行。缺字、未知控制符、超长姓名、窗口溢出均失败，不丢字或静默换页。

这份 IR 明确设置 `game_encoding_assigned=false`，字形索引没有被当成游戏内字符编码。PNG 标注 `OFFLINE PROTOTYPE`，是独立的软件预览，不是 Switch／Ryubing 游戏画面。尚不能直接把这些素材交给 ROM 加载器；下一步仍需目标构建的编码、字符串处理、字形绘制适配和完整入口证据。

## 验证与继续条件

```powershell
python -m pytest tests/test_frlg_localization.py -q -W error
python -m pytest tests/test_frlg_localization_gift.py tests/test_frlg_localization_text.py -q -W error
python -m pytest tests/ -q -W error
python scripts/gen_buffer_scripts.py --check
dotnet build -c Release services/pkhex -warnaserror
```

汇编复核需要 PATH 中的 `arm-none-eabi-as` 和 `arm-none-eabi-objcopy`。生成器将 CRLF 规范化为 LF 后计算源哈希，Windows 和 Linux 输出一致。Windows 的 Unicorn 原生库可能在测试框架的 faulthandler 下打印已处理的 SEH 诊断；本次本地回归使用 `-p no:faulthandler`，断言和 `-W error` 仍启用，CI 保持原有命令。

自动回归覆盖错版本／保护区、清单篡改、跨页合并、控制符无损往返、乱序／重复／旧会话、缺块、暂存损坏、写入静默拒绝、应答丢失、部分写入续接、逆序恢复、邻页变化、重复加载、模型中的软复位／完整重启，以及已上传 THUMB 代码的取指执行。

2026-10-19 上游同步和客户端入口完成后的本机验收环境为 Windows、Python 3.12.8、Unicorn 2.1.4、GNU Arm 12.2.Rel1、.NET SDK 10.0.400。第一轮客户端全仓回归 **4420 项通过、349 项跳过**，使用 `-q -W error -p no:faulthandler -n 8 --dist worksteal`。buffer、field 和 resident 三类汇编产物检查通过；PKHeX Release 构建 0 警告、0 错误。三页模型的加载、重复加载及恢复通过，实际提交总数为 6。客户端测试页已通过本机浏览器渲染检查，原生桌面窗口正常启动，随后完成上述实机 4 KiB 只读采集。前述软件结果和只读采集不代替页写入及中文渲染验收。

回归包括完整客户端 VM／RFU 分块状态机下的加载和恢复、批量回读游标重置、身份拒绝、超时／短响应／断线取消、无需原始文件的 ROM 采集、主机初始化／清理、无 Wonder Card 的屏幕回调、字模往返、版权元数据、缺字、变量及像素溢出。另修复 field／resident 生成器的 Windows CRLF 源哈希差异，统一按 LF 计算（含 resident 的共享 include）；逐项重汇编确认机器码和符号偏移与已提交产物一致，无需修改这些机器码。

继续实机阶段可先用 PokeLDN 只读入口取得运行时材料，再独立核对原始 ROM 指纹、对应 Switch 标题／更新／包装器身份，以及通信与暂存区证据。按原方案先复现同构建 Ryubing 单页读取、写入、恢复与取指，再做零售机单页验收。只有这些关口通过，才把当前离线中文素材接入游戏内解码、字体、字符串复制和渲染路径。
