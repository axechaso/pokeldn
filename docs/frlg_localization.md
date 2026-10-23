---
title: 汉化运行时补丁工具
parent: FireRed and LeafGreen
nav_exclude: true
---

# 汉化运行时补丁工具

2026-10-23 更新。已交付[汉化方案](pokeldn_localization_implementation_plan_zh.md)的软件基础：版本配置、差分包、ARM 页载荷、恢复日志，以及接入现有 PokeLDN RFU／Mystery Gift 主机的持续多步骤适配器。另有字库、排版预览，以及已在 mGBA 中运行的两页中文开场对话源码原型。**完整游戏汉化尚不能安装**，也没有经过本项目验证的零售 ROM 写入配置。用户已在实际 NS 上确认神秘礼物消息窗口和临时 NPC 首句均能正常显示；NPC 对话 RAM 原型的实机视觉验收已完成。只读采集入口继续保留。实际 ROM 写入适配器为库接口，保留独立观测的版本与写入门禁。

| 阶段 | 当前状态 |
| --- | --- |
| P0 离线核心 | 已实现并有自动回归；真实 ROM／包装器及暂存区证据尚待补齐 |
| P1 修改版 Ryubing 复现 | 未执行；已构建匹配原版并用 mGBA 验证源码渲染原型，仍未验证 Switch 包装器 |
| P2 零售机单页验证 | 4 KiB 实机只读采集及正常断开已通过；页写入、恢复与取指尚待验收 |
| P3 中文编码、字库、渲染 | NS 神秘礼物窗口和一处 NPC 首句的临时中文显示均已由用户在实机确认；另有 mGBA 两页中文开场源码原型。完整内容和通用编码仍未交付 |
| P4 内容、P5 插件 | 已加入完整文本目录扫描和中文字符覆盖检查；客户端已有 NS 中文显示及只读测试入口，正式 ROM 内容补丁及完整汉化插件未实现 |

## 全文文本目录与中文字符覆盖

现在可以把外部的完整 FRLG 源码检出转换成可审阅的文本目录。扫描器覆盖
`data`、`src` 和 `include` 下的 `.string`／`.braille` 记录，以及 C 源码中的
`_()`／`__()` 字符串；会保留换行、滚动、清页和变量占位符，并处理跨行拼接的 C
字面量。源码、字库和 ROM 都不进入本仓库。

可用已有的完整中文源码作为覆盖检查输入，例如[开源 PretFRLG 中文分支](https://github.com/Xzonn/PretFRLG)：

```powershell
python tools/frlg/localization_catalog.py `
  --source-dir C:/private/PretFRLG `
  --charmap C:/private/PretFRLG/charmap.txt `
  --output C:/private/frlg-text-catalog.json `
  --report C:/private/frlg-text-report.json
```

报告会给出文本条目、字符、控制符和占位符数量，以及中文字符表是否覆盖全部出现的
字符。当前验证结果为 13,206 个条目、398 个文件、2,794 个不同字符，中文字符表覆盖
率为 2,794/2,794，包含 2,558 个双字节字符。这个阶段只证明文本资源和编码表可以
完整盘点，仍不会生成可安装的 ROM 补丁；下一步才是把源码构建产物接入版本专用差分并
在 mGBA 中逐批验收。

要把英文键和中文源逐条对齐，使用：

```powershell
python tools/frlg/localization_source_diff.py `
  --base-dir C:/private/pokefirered `
  --translated-dir C:/private/PretFRLG `
  --charmap C:/private/PretFRLG/charmap.txt `
  --output C:/private/frlg-translations.json `
  --report C:/private/frlg-source-diff.json
```

当前外部源码的对齐结果是 12,791 条共同文本、9,146 条已变化、0 条英文键缺失；
另有 415 条中文分支独有记录。报告还标出 397 条占位符不一致、1 条结束符不一致和
935 条有意为空的字符串。`--strict` 会在这些控制项未复核前拒绝输出，避免把语义
不同的变量直接送入 ROM 编码阶段。

在非严格模式下，当前字符表可以把 12,791 条对齐文本全部编码，得到 350,735 字节，
使用 559 个控制宏，且没有缺字。占位符差异仍必须逐条回到源码确认；它们不是字体问题，
直接跳过会改变战斗消息或变量展开的运行时语义。

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
新报告记录 `read_batch_bytes=4096`。软件回归不证明同机 64 KiB 已通过；失败产物不覆盖先前有效的 4 KiB 样本。

后续实机重试仍持续报错，没有取得有效正文。**目前停止要求用户继续重试采集。**
4 KiB 成功只证明一次短读取，自动测试通过也不证明零售机的长会话稳定。
开发改为先使用固定版本反编译源码研究和构建文本引擎，再在模拟器完成最小中文对话及加载／恢复验证；
有可验证的开发结果之后，再安排小范围实机验收。无线采集不再作为用户安装汉化前必须完成的步骤。

同步保留了上游的 USB 断线／信用窗口处理、Mystery Gift／存档传输入口，以及可选板载屏幕支持。
汉化主机改用共用的 `_build_engine` 扩展点，并提供不依赖 Wonder Card 的屏幕内容。

## 在客户端测试

当前桌面 **PokeLDN 汉化测试** 快捷方式进入 **NS NPC dialogue test**，窗口标题为
**PokeLDN · NS 剧情汉化测试**。本轮只翻译真新镇南侧胖男子的第一句话：
**“科学的力量真伟大！”**，后面的英文说明保留。

1. 客户端点 **Start**，NS 英文火红进入 **Mystery Gift → Wonder Cards → Friend → POKELDN**。
2. 等待 **NPC Chinese test ready** 提示，按 **A** 返回主菜单，然后选 **Continue** 继续存档。
   保持游戏运行，不要软复位或完整重开游戏。
3. 到真新镇大木研究所南侧，和胖男子对话。检查中文首句、按 **A** 翻到原来的英文第二页，
   再正常结束对话；尝试其他 NPC，确认其英文对话仍正常。
4. 完整重开游戏后，这句应恢复英文。如需再次测试，再从客户端加载。

2026-10-23，用户确认上述 NPC 首句在实际 NS 上正常显示。该结果只确认本次临时 RAM 载荷、字库分发和原版续页路径；它不改变 `retail_write_eligible`，也不代表完整 ROM 汉化已经安装。

上传包括两个各 1,024 字节的短载荷，不采集 ROM。第一阶段只将部分字节放入
`0x0203FC00..0x02040000` 的未链接 EWRAM；第二阶段核对完整 784 字节代码、字体表和九字子集后，
最后切换 VBlank 指针。阶段前后均核对整个 1 KiB 区域的 FNV，拒绝其他驻留功能占用或中途数据变化。
执行前还核对 BPRE `0x0A`，以及原版完整 `VBlankIntr`、`CopyGlyphToWindow`、字体分发函数区段、
`RenderText`、`RenderFont`、`RunTextPrinters`、字体表和目标 NPC 原文的指纹。
未知内容在写入驻留区前即被拒绝；部分上传未完成时入口保持原样，需重开游戏清理后再试。

VBlank 只在 `gFonts` 仍指向原版字体表时将其指向 RAM 副本。普通／男性字体函数仅对
目标完整原文启用小型中文分支，使用本次打印器的前景／背景色，并让原版函数处理文字速度、A/B 加速、
等待和清页；字形由原版 `CopyGlyphToWindow`、`RunTextPrinters` 上传到当前窗口。
其他文本继续调用原函数，剧情脚本、ROM、剧情标志、存档和神秘礼物卡片都不改写。
它是用于验收一处 NPC 首句的临时挂钩，并非通用字库或完整汉化安装。

`STORY_DEMO_READY` 只表示载荷安装回执，仍需用户在 NS 正常游戏中确认字形、翻页及关闭；
不得将自动报告当作剧情实机验收通过。`retail_write_eligible` 及源码实验的 `switch_verified` 不因 RAM 原型改变。

开发端使用未改动的源码匹配原版映像，在 mGBA 0.11-212-7a12d6d 中运行 10,440 帧：
开发用回调跳板执行这两个实际 ARM 载荷，再调用原版目标 NPC 的字段脚本。
回执依次为 `0x5A485331`、`0x5A485332`；实际帧缓冲确认中文首句、英文第二页和正常关闭回地图。
重置后完整驻留区清零、VBlank 指针恢复为 `0x08000735`，执行前后 ROM 的 SHA-256 均为
`d32c8df8702293716ad8de68755fe5903161f4829a0a2e486bfa7e74a0b62e94`。
这验证 GBA 内部运行，不是 NS 无线会话或在实际地图上与 NPC 交互的实机验收。

已通过的神秘礼物窗口显示入口仍保留在 **NS Chinese display test** 页面，操作如下：

1. 客户端点 **Start**。
2. NS 英文火红进入 **Mystery Gift → Wonder Cards → Friend → POKELDN**。
3. 看下方消息窗口是否出现 **“你好！”**，等待英文结束提示出现，再按 **A** 返回。等待时间受通信影响，不固定为十秒。

本入口仅发送一个 808 字节 ARM 载荷，服务器不先读取 ROM。载荷核对 `BPRE 0x0A` 头、
`CopyWindowToVram` 的 64 字节 FNV-1a 指纹、消息窗口 1 的完整模板，以及像素缓冲在游戏堆内的范围；
随后只清理这个窗口的 3,584 字节像素数据，将授权字形组成的 48×16 图块复制到窗口并请求游戏上传显示。
它不修改 ROM、不访问存档闪存、不注册驻留任务，结束使用不保存的动态消息路径。
`DISPLAY_DEMO_EXECUTED` 只确认脚本回执，报告明确保留 `visual_confirmation_required=true`；
需要用户实际看到中文，才能确认这台 NS 的显示结果。2026-10-19，用户已确认实机正常显示，
同次报告为 `BPRE 0x0A`、`reply=0x5A48444D`，808 字节载荷 SHA-256 为
`7f5bc55ee29f654ac5a4052cfb0b2530eed37113327e25c8a5c896142ba1d1f5`。
用户观察到停留超过十秒；载荷计数为 600 次回调，返回后中文像素仍会保留到后续英文结果消息重画窗口，
所以计数不能作为固定显示时长。报告没有记录画面起止时间，未测得准确停留秒数。
这是临时窗口绘制测试，尚未接入剧情文本，也不验证 ROM 页写入或完整游戏汉化。

既有只读采集入口位于单独的 **Localization test** 页面，不是当前这项中文显示测试：

这次开发版客户端的 **Games → FireRed & LeafGreen → Localization test** 提供按钮入口。
沿用 Board 页的无线板和 Settings 中的密钥，不需要手动填写命令或输出路径。
源码中的 `gui/localization_test.py` 现已改为直接打开上述 NS NPC 对话测试页。

默认 **Quick connection test (4 KiB)** 先验证真实无线读取；点 Start 后，在英文火红选择
**Mystery Gift → Wonder Cards → Friend → POKELDN**，等待 Session 显示
**Quick connection test passed**。这份 4 KiB 样本只用于连接测试，不是完整 ROM。

开发入口 **Capture text engine (64 KiB)** 只采集运行时 ROM 的前 64 KiB，
用来研究文字解码、字形绘制、字宽、字符串复制和控制符处理。报告附带 17 个函数窗口的地址和哈希，
地址由上游英文 ELF 名称映射推导，保持 `symbol_addresses_verified=false`；仍需将实际指令与反编译核对。
它不含完整字库和剧情资源，不授予写入资格，也不表示中文已经能显示。**该入口尚未在实机稳定完成，当前不要求用户执行。**

Session 每收到 1 KiB 就更新进度，完成后显示 **Text engine captured** 和实际读取耗时。
此前根据短读取速率作出的 64 KiB 耗时估算未得到实机完整会话验证，不能作为使用承诺。
完整 **Capture runtime ROM (16 MiB)** 仍保留，但在该速率下线性外推为数十小时，暂不作为下一次必做测试。
Session 显示读取进度、
完成状态、SHA-256 和 **Open output folder** 按钮。每次运行自动在 Received 下新建独立目录，
保存 `report.json` 及 `runtime-sample.bin`（快速测试／文本引擎）或 `runtime.gba`（完整采集）。
客户端会重新计算文件 SHA-256，损坏或未完成的副本不会显示为成功。
两次运行之间先在 Switch 退出搜索页面再进入。该采集入口只读，不执行中文绘制。

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

这份 IR 明确设置 `game_encoding_assigned=false`，字形索引没有被当成游戏内字符编码。PNG 标注 `OFFLINE PROTOTYPE`，是独立的软件预览，不是 Switch／Ryubing 游戏画面。下面的游戏源码实验另行生成限定编码，不能直接把 IR 或重建后的游戏映像交给运行时加载器。

## 游戏文本引擎中的中文实验

已从 [pret/pokefirered 固定源码](https://github.com/pret/pokefirered/tree/037335f4c725d7c9aecdac87066f2002b4bd7e14)
构建 `FIRERED / GAME_REVISION=10 / MODERN=0`。原版 16 MiB 映像通过上游 SHA-1 校验
`baa452d0b24629dd7782cfc07a8984085dde1311`；其前 4 KiB 的 SHA-256 与上述 NS 实机成功样本相同。
这验证了源码与目标基础版本的对应关系，尚不确认整个运行时映像及包装器。

`tools/frlg/localization_game_demo.py` 检查原版哈希和指定源码文件的 SHA-256，临时改动
`src/text.c` 与开场文本，生成独立命名的游戏、ELF、map 和构建报告。编译失败时也会恢复原始源码与时间戳。
需要提前按反编译项目说明构建原版、准备其构建工具；脚本不下载游戏或工具。这是开发入口：

```powershell
python tools/frlg/localization_game_demo.py --source-dir PATH_TO_DECOMP --output-dir scratchpad/zh-game-001
python tools/frlg/localization_emulator_smoke.py --core PATH_TO_MGBA_LIBRETRO --rom scratchpad/zh-game-001/demo.gba --output-dir scratchpad/zh-emulator-001
```

实验复用游戏现有 `F9` 额外字形解码，限定国际版 normal／male 字体的 `40..CF` 选择符区间，
现有 `00..17` 符号、`D0..FE` 表情和日文字体继续走原有分支。
剧情首句的 9 个汉字从提交的 14 px、16×16、2bpp 位图生成；常驻代码将 2bpp 的二级以上墨色
压成 1bpp，再转换成游戏需要的四块 8×8、4bpp 像素，使用游戏当前文字颜色。游戏推进仍固定为
14 px，原有换行、按键清页、终止符沿用；姓名与联机字段未改变。这个小字库通道不作为完整汉化的通用编码。

实测 mGBA `0.11-212-7a12d6d` 运行 2,640 帧，帧缓冲截图人工确认两页中文、按键清页，以及其后英文剧情继续。
核心 SHA-256 为 `7a5430bdd0b323e28f180201d699f8caa490bbe6e9c3a8489454df8bb5dde760`，
中文实验映像 SHA-256 为 `562f311ba20b172a9824c305b2310188d78e2aa0ee434336103af4ff75b9a911`。
外部核心、游戏映像与截图留在本机忽略目录中。

2026-10-23 的字体复验使用同一 mGBA 核心和 `scratchpad/verify-ns-story-gba.py`，
以 14 px 字形、1bpp 二级墨色阈值运行 10,440 帧；两个 RAM 上传阶段分别返回
`0x5A485331` 和 `0x5A485332`，`frame-009240.png` 显示完整中文首句，之后英文续页和清页路径继续通过，
软复位回读确认常驻槽已清空。该复验仍是离线模拟器结果，`switch_verified` 与 `retail_write_eligible` 保持 `false`。

这是源码重编译的 GBA 实验，仍需整理适合零售运行时的局部挂钩和载荷，并在 Switch 包装器环境验证加载与恢复。
桌面客户端当前没有安装这份实验的入口；`retail_write_eligible` 与 `switch_verified` 继续为 `false`。

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

本轮离线游戏渲染实验已完成：以 `pret/pokefirered` 固定提交 `037335f4c725d7c9aecdac87066f2002b4bd7e14`
建立通过原版 SHA-1 的英文修订版 10 基线，完成有限字库的源码绘制与字宽适配，
并在 mGBA 验证两页中文及其后原有剧情。新增源码实验及文档检查与既有汉化模块分别通过本机回归。
随后仍须复现同构建包装器的单页读取、写入、恢复与取指；零售机阶段独立核对完整 ROM、
Switch 标题／更新／包装器、通信和暂存区证据。源码、离线原型和一次短读取都不代替这些关口。
