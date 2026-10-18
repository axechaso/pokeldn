---
title: 汉化运行时补丁工具
parent: FireRed and LeafGreen
nav_exclude: true
---

# 汉化运行时补丁工具

2026-10-18。已交付[汉化方案](pokeldn_localization_implementation_plan_zh.md)第一批的软件基础：版本配置、差分包、完整页合并、ARM 暂存／提交构建器、会话和本地恢复日志。**目前不能让 Switch 游戏显示中文**，也没有经过本项目验证的零售版本配置。CLI 仅提供离线检查和明确标为 synthetic 的软件模型。

| 阶段 | 当前状态 |
| --- | --- |
| P0 离线核心 | 已实现并有自动回归；真实 ROM／包装器及暂存区证据尚待补齐 |
| P1 修改版 Ryubing 复现 | 未执行；Unicorn 软件模型不等于 Switch 包装器模拟器 |
| P2 零售机单页验证 | 未执行；本次环境未检测到串口无线设备，未提供目标 ROM／包装器材料 |
| P3 中文编码、字库、渲染 | 未实现；只交付保留原始字节的国际版文本解析器 |
| P4 内容、P5 插件 | 未实现；不得将此分支作为完整汉化版发布 |

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

`asm/rom-page-stage.s` 为 928 字节，包含 512 字节数据；`asm/rom-page-commit.s` 为 484 字节。两者都在 1024 字节接收限制内。暂存状态包含完整的 16 字节会话 ID、32 字节补丁 ID、页号、块位图、冲突标记和 FNV-1a。乱序与相同重复块可以接受，内容冲突导致此页停止。缺块、旧会话、错误页、源或目标内容校验失败均不能提交。

主机在提交前回读暂存区并计算 SHA-256。客体的 FNV-1a 是执行边界的附加完整性检查，不是 SHA-256 的替代品。提交仅使用 `swi 0x48`；目标页只在经校验的 ROM 范围内形成对应扇区参数，保留普通 Flash 构建器的 0–31 限制及通用 Sloop 对写入 syscall 的禁令。

机制依据固定为[上游 ROM 页写入实验](https://github.com/Decryptu/pokeldn/blob/da882c7633ff106a0d2206fa2015a3538777e542/docs/frlg_rom.md#the-flash-sector-path)。该实验是在修改版 Ryubing 中进行。当前模型没有运行该构建，不继承其实验结果。

`BufferTransport` 的宿主接入需提供真实 `Observation`、内存读取和单步 buffer-script 执行 callback。读取端必须采用 `CLIENT_SCRIPT_DUMP_MEMORY` 的命令顺序；执行端使用 `CLIENT_SCRIPT_RUN_BUFFER`。观察数据必须来自宿主识别与执行现场，不能从补丁清单回填。所有 callback 须在同一游戏进程和持续会话中运行；当前没有集成到 ESP32/IP host 的实际连接器或面向设备的 CLI。

每次加载／恢复都重新读取身份和完整运行时 ROM。只允许原始页或本补丁目标页，未解释的任何差异都会停止。写前再次核对目标与当前执行范围；资源页全部回读一致后才允许入口切换。页提交应答丢失时，先回读确定结果，不盲目重写。每页也检查两侧邻页；结束时复核完整 ROM。

会话先将原始完整页落盘，再进行任何暂存或提交；`WRITE_INTENT` 在 syscall 前写入。日志更新使用同目录临时文件、flush/fsync 和替换。恢复不信任旧的“成功”状态：重新识别、回读，先撤销激活入口，再逆序恢复代码和资源。当前内容既不是原始值也不是目标值时保留日志并停止，不能自动覆盖。多页过程不提供事务原子性。

文本解析按参数长度消费控制码。特别是 `PLAY_SE (FC 10)` 的两参数、`CLEAR (FC 11)` 的单参数不能被错认成文字或终止符；参数长度对照[反编译的 GetExtCtrlCodeLength](https://github.com/pret/pokefirered/blob/master/src/string_util.c#L573)。未分配中文前缀、未更改姓名／昵称／存档／联机字段的编码。

## 验证与继续条件

```powershell
python -m pytest tests/test_frlg_localization.py -q -W error
python -m pytest tests/ -q -W error
python scripts/gen_buffer_scripts.py --check
dotnet build -c Release services/pkhex -warnaserror
```

汇编复核需要 PATH 中的 `arm-none-eabi-as` 和 `arm-none-eabi-objcopy`。生成器将 CRLF 规范化为 LF 后计算源哈希，Windows 和 Linux 输出一致。Windows 的 Unicorn 原生库可能在测试框架的 faulthandler 下打印已处理的 SEH 诊断；本次本地回归使用 `-p no:faulthandler`，断言和 `-W error` 仍启用，CI 保持原有命令。

自动回归覆盖错版本／保护区、清单篡改、跨页合并、控制符无损往返、乱序／重复／旧会话、缺块、暂存损坏、写入静默拒绝、应答丢失、部分写入续接、逆序恢复、邻页变化、重复加载、模型中的软复位／完整重启，以及已上传 THUMB 代码的取指执行。

本机软件验证环境为 Windows、Python 3.12.8、Unicorn 2.1.4、GNU Arm 12.2.Rel1、.NET SDK 10.0.400。新增测试 59 项通过，现有 buffer-script／文档测试 264 项通过、1 项跳过；汇编产物复核通过，PKHeX Release 构建 0 警告、0 错误。三页模型报告的加载及恢复状态均通过，重复加载未增加提交次数。这些结果仅适用于软件回归。

继续实机阶段需要目标原始 ROM 的本机指纹、对应 Switch 标题／更新／包装器身份、通信与暂存区的地址证据，以及可连接的无线设备和实机。按原方案先复现同构建 Ryubing 单页读取、写入、恢复与取指，再做零售机单页验收。只有这些关口通过，才接入中文解码、字体、字符串复制和渲染路径，开展 20–50 字固定对话及之后的内容／插件工作。
