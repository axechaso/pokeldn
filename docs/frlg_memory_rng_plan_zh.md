---
title: FRLG 内存乱数自动化方案
parent: FireRed and LeafGreen
nav_order: 12
---

# 火红/叶绿内存乱数自动化实现方案

本文针对 Nintendo Switch 版 FireRed/LeafGreen（GBA ROM 运行在 Switch GBA 模拟器内）设计一套“读取实时内存、预测 RNG、自动操作、读取结果并复核”的闭环方案。方案以当前 PokeLDN 的代码和实测结论为基础，同时兼容另一个已有的 EasyCon/OCR 自动乱数工具。

## 先给结论

建议分成两条后端，但共用同一套 RNG 模型和目标规划器。模拟器进程内存读取目前是待验证假设，不应视为已有能力：

1. **模拟器进程内存后端（候选优先路线）**：先针对 Ryujinx 做只读验证，确认当前版本确实能从进程内存定位并稳定读取 GBA 客体数据。30 Hz 是验证通过后的目标，不是当前已证明的能力；yuzu 需要单独验证其内存映射。
2. **PokeLDN 客体后端（零售机和模拟器通用）**：复用现有 Wonder Card buffer script、rng-trace、memory-dump、install-resident 和 shiny/ivs/noencounter hook。它不依赖模拟器私有进程接口，能在零售 Switch 上验证地址、预测模型和搜索逻辑，但当前链路不能在野外每帧把 RAM 传回 PC，因此第一阶段以启动校准、结果验证和半自动辅助为主。

不要把核心建立在 Sloop syscall 读取 wrapper 任意内存之上。当前资料只证明若干 syscall 会写 wrapper 对象或把数值送入 ServicePrepo 日志，没有证明 GBA 能任意读取 Switch 文件或任意进程内存；这条路应作为独立实验，不得成为主流程依赖。

第一版的明确目标是：

- 支持英文 FireRed BPRE、英文 LeafGreen BPGE；随后补齐法文 BPRF、BPGF。
- 先用 Ryujinx + 英文 BPRE 完成只读 PoC：识别 ROM、验证客体地址到宿主内存的映射，并连续采样 RNG。PoC 通过后，再扩展到 BPGE、目标搜索和自动操作。
- 最终在 Ryujinx 中自动发现已支持的游戏、读取 RNG 状态、识别支持的场景、寻找目标并完成一次“野生/定点闪光 + 性格 + IV 条件”的闭环。
- 在零售 Switch 上用 PokeLDN 复现同一套 LCG、目标搜索和结果复核；不能实时读客体 RAM 时明确降级为 Resident overlay、客体内搜索或 OCR 辅助。
- 进程内存后端禁止写宿主进程内存；写存档和其他直接修改游戏数据的功能默认关闭。虚拟手柄输入单独设置每次运行的显式启用步骤，不与内存/存档写入共用一个开关。

当前已打包的 `frlg-rng-assist` 只覆盖离线候选搜索、单次校准和报告保存；它不提供实时内存同步、Resident 安装或自动输入。只有完成下文的快照有效性、seed epoch、build-specific hook 和结果复核验收后，才可把运行时功能从实验分支升级为默认能力。

## 当前仓库能直接复用的事实

PokeLDN 已有以下基础，不需要重新逆向：

| 能力 | 当前实现/事实 | 对新系统的用途 |
|---|---|---|
| LCG | pokeldn/frlg/rom/lcg.py | 32 位状态前进、逆推、距离计算、野生宝可梦恢复 |
| RNG 主状态 | gRngValue | 法文 FR/LG 为 0x03004220，英文 FR/LG 为 0x030042D0 |
| SaveBlock 指针 | gSaveBlock1Ptr/gSaveBlock2Ptr | 每次加载/进入战斗可能重滚，必须运行时重读 |
| 固定 EWRAM | gPlayerParty=0x02024280、gEnemyParty=0x02024028 | 读取队伍或敌方宝可梦；EWRAM 地址在英法构建间相同 |
| 客体读内存 | memory-dump、memory-dump-multi、memory-dump-scatter | 启动探测、离线地址验证、结果读取 |
| 易失 RNG 读取 | rng-trace | 避免把正在变化的 gRngValue 放进会重新计算 CRC 的普通 dump |
| 常驻代码 | install-resident、save-write --resident | 零售机上安装 RNG overlay、计数器、禁遇敌和客体内搜索 |
| 已验证 hook | shiny、ivs、noencounter、turbo | 校准和结果对照；以后可扩展 telemetry hook |
| 构建表 | pokeldn/frlg/rom/builds.py | BPRF/BPGF/BPRE/BPGE 的 IWRAM/ROM 函数地址选择 |
| Switch ROM 证据 | docs/frlg_rom_map.md、docs/frlg_rom.md | 1.0.1 与 v0 客体 ROM 基本一致，英版地址可由 ELF/ROM 验证 |

frlg-auto-rng 目前走 Ten Lines + EasyCon + 采集卡 + OCR，适合没有客体内存接口的场景。新的内存后端应通过 JSONL 或 Python API 给它提供统一的观测数据，不能把 PokeLDN radio 层和 OCR/手柄脚本硬耦合。

### 外部参考的适用边界

本机另有 `FRLGSwitchSeedFarmer` 研究项目。其 README 要求 Switch 安装 CFW 和 sys-botbase/sys-botbase-cpp，通过 Switch 侧服务读取游戏 RAM；它不是 Ryujinx 的 `ReadProcessMemory` 后端，也不能用于未破解零售机。这里只参考算法和测量方法，不直接复用其中的 Title ID、进程地址或语言版本地址表。

可参考的做法：

- `operation_rng_test.py` 用固定 32 轮的 LCG 距离算法计算完整周期内的 advances，可作为高频采样器的实现参考；先与本项目 `lcg.distance()` 做已知样例和随机样例交叉验证，再用于实时路径。
- 采样时在读取 RNG 前后读取 VBlank，并用 RNG 读取起止时间的中点作为观测时刻；VBlank 跨刷新时记录范围，不伪装成原子快照。可用同地点、等时长的空等窗口对照按键窗口，但两者场景或刷新数不一致时，只报告原始差值，不解释为精确按键消耗。
- 其 EWRAM 扫描和 Gen III 宝可梦解密可作为结果校验的交叉参考；所有地址及结构仍需由当前 PokeLDN 的 build/decomp 证据确认。

## 总体架构

~~~text
              ┌──────────────────────────────┐
              │     FRLG RNG Core             │
              │ LCG / encounter / criteria   │
              │ planner / verifier / logs    │
              └──────────────┬───────────────┘
                             │ RngObservation
              ┌──────────────┴───────────────┐
              │                              │
  ProcessMemoryBackend                 GuestLinkBackend
  Ryujinx PoC; 30 Hz target             PokeLDN/ESP32
  只读 ReadProcessMemory                 rng-trace/buffer script
              │                              │
              └──────────────┬───────────────┘
                             │
                    ControllerBackend
           Ryujinx 虚拟手柄 / EasyCon / 人工确认
~~~

所有后端都只输出同一个 RngObservation，规划器不关心数据是从进程还是从 GBA 客体来的。控制器也采用接口隔离：模拟器可以先接键盘/虚拟手柄，零售机继续使用 EasyCon 或人工按键。

## 内存读取的两种实现

### A. Ryujinx 进程内存后端

这是完全自动化的候选路线，也是当前最大的可行性未知项。只允许读取本机、用户自己启动的 Ryujinx 进程，不注入代码、不安装驱动、不修改第三方进程保护，也不写进程内存或存档。

#### 进程连接

RyujinxMemoryReader 启动时完成以下步骤：

1. 按进程可执行文件名、用户选择的 PID 或项目配置连接；发现多个候选时必须停下来报错，不能猜第一个。连接前还要校验可执行文件的规范路径、所属用户/会话和进程启动时间，记录 PID 启动时间作为 PID 复用保护。
2. 通过 Windows OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ) 获取只读句柄；句柄失效、模拟器退出或权限不足时进入 Disconnected 状态。
3. 用 VirtualQueryEx 枚举 MEM_COMMIT 且具有 PAGE_READ* 权限的区域，只保存区间表，不每帧重复枚举。
4. 读取客体 ROM header：0xA0 附近的标题、0xAC 的 BPRE/BPGE、版本 0x0A，并记录客体 ROM 的 SHA-256。若要接受 wrapper 启动 patch，profile 必须同时记录精确的 patch 地址、原字节和允许的新字节；不能用未定义的“忽略三个 patch”规则扩大可接受范围。
5. 用“模拟器版本 + 客体 ROM hash + 游戏码”选择静态地址映射规则。没有匹配规则时只允许进入探测模式，不能开始自动操作。profile 记录版本、签名和映射规则，不保存某次进程运行的宿主虚拟地址。

#### 客体 GBA 地址到进程地址

不能假定宿主进程地址等于 0x030042D0。0x030042D0 是英文 FR/LG 的 GBA 客体地址；每个模拟器可能把 GBA ROM、EWRAM、IWRAM 放在不同的 guest-memory 对象中，宿主地址还会受 ASLR 影响。必须把静态映射规则和每次运行发现的地址分开：

1. **静态映射规则**：记录某个 Ryujinx 版本中 guest-memory 对象的识别方法、结构偏移或 ROM 签名规则；使用 gba_to_host(0x030042D0) 计算当前会话的地址。不要把宿主虚拟地址写入可复用 profile。
2. **每次运行重新定位**：进程启动或游戏重载后，在当前进程可读区域搜索 ROM header 和已知函数字节，定位本次会话的 ROM/内存对象。进程退出、游戏重载或映射验证失败时，丢弃所有宿主地址并重新定位。
3. **动态候选验证**：在已确认的 guest IWRAM 映射范围内枚举 4 字节候选，寻找满足 next = 1103515245 * state + 24691 (mod 2^32) 的地址。连续采样 3–5 次后，候选必须满足：状态距离为小整数、变化频率与客体帧率相符、附近的 gSaveBlock2Ptr 是可读且 4 字节对齐的指针、TID/SID 范围合法。不能只因某个地址会变化就认定是 RNG。

静态 profile fingerprint 只保存可跨进程复用的字段：

~~~text
emulator = ryujinx
emulator_version = ...
rom_game_code = BPRE | BPGE
rom_sha256 = ...
layout_id = ...
profile_schema = 1
~~~

宿主基址、进程 PID、当前 GBA 区域基址及其转换出的宿主地址属于 RuntimeMapping，只在当前进程/游戏会话中有效，不得作为稳定 fingerprint。任何字段校验失败，后端都应降级为 Unverified，停止规划和输入，只保留诊断日志。

#### 读取策略

- 先测量实际可持续采样频率；30 Hz 是 PoC 通过后的目标，不能预设可达到。每次把相邻的 IWRAM/EWRAM 请求合并成 4 KiB 或 16 KiB 批量读取，避免几十个 ReadProcessMemory 调用造成抖动。
- 一个快照至少包含：gRngValue、两个 SaveBlock 指针、gMain 的回调/按键/帧计数、地图与战斗状态候选、TID/SID、队伍/敌方宝可梦相关区域。
- 读取指针型结构时采用“读关键指针和 RNG → 读结构 → 再读关键指针和 RNG”的重试；指针变化或快照跨越无法解释的 RNG 状态时丢弃该快照。若客体帧计数尚未验证，不得把宿主时间当成客体帧来声称快照原子一致。
- 每条快照记录宿主单调时钟、客体帧计数、进程读取耗时和失败原因。不能把宿主时间直接当成游戏帧。

### B. PokeLDN 客体后端

它用于零售 Switch 和模拟器交叉验证。

1. 启动阶段用 trainer-id-probe、memory-dump 和 memory-scan 验证游戏码、构建表、gRngValue、gSaveBlock2Ptr 与 TID/SID。
2. 用 rng-trace 读取 gRngValue。普通 memory-dump 不得跨过 gRngValue，因为发送前后会发生 CRC，读到的值会变化并导致链路错误。Mystery Gift 会话中的 rng-trace 只能校准该会话的消耗速率；退出会话后 title screen 会重新播种，不能把该 seed 当作野外连续状态。
3. 用 save-dump 读取队伍与结果；保存块指针每次加载/战斗会移动，payload 必须按照本次调用收到的指针计算地址，不能缓存上一次的绝对保存地址。
4. 需要跨重启的代码用 save-write --resident 写入 SaveBlock2 + 0xB20，重启后由 MOM 的 RAM script 重新安装。写入游戏实际读取的字段必须独立要求 --write-unsafe 和二次校验；已证明的 filler scratch 区仍可走现有安全默认值。
5. 当前 RFU/Mystery Gift 会话关闭后，PokeLDN 没有持续读取野外 RAM 的通道。因此零售机实时模式采用以下顺序：
   - 优先使用客体内 mon-seek/shiny 搜索，让 GBA 自己把 RNG 调到目标；
   - 其次安装 overlay 或 rng-telemetry，由采集卡/OCR 读取少量状态；
   - 需要实时 PC 读数时切换到 Ryujinx 后端，而不是伪装成已有的 PokeLDN 读内存能力。

## 统一数据模型

建议新增 pokeldn/frlg/rng/model.py：

~~~python
@dataclass(frozen=True)
class RngObservation:
    sequence: int
    session_id: str
    seed_epoch: int
    host_time_ns: int
    guest_frame: int | None
    rng_state: int
    rng_state_after: int | None
    rng_turns_since_previous: int | None
    sample_boundary: str          # pre-vblank/post-vblank/unknown
    snapshot_valid: bool
    discontinuity: str | None    # reseed/scene-change/remap/torn/read-failure
    mapping_generation: int | None
    game_code: str                 # BPRE/BPGE/BPRF/BPGF
    scene: str                     # title/menu/overworld/battle/link/unknown
    callback1: int | None
    callback2: int | None
    map_id: int | None
    tid: int | None
    sid: int | None
    save_block2: int | None
    party_snapshot: bytes | None
    enemy_snapshot: bytes | None
    source: str                     # ryujinx-process/guest-link/resident-overlay
    confidence: float
~~~

请求模型 RngTarget 至少包括：

~~~text
encounter_kind = wild | static | scripted | egg (egg 后续实现)
species / level / slot
shiny = true/false
natures = []
iv_min[6], iv_max[6]
gender, ability, hidden_power (可选)
max_frames / max_advances
trigger = walk | fish | talk | scripted
~~~

## RNG 和遭遇模型

### 状态运算

沿用现有 lcg.py，并把以下函数纳入公共接口：

~~~text
step(state)       = (state * 1103515245 + 24691) mod 2^32
advance(state, n)
distance(start, target)
draw(state)        -> high16, next_state
~~~

Random() 返回更新后状态的高 16 位。distance 是 32 位全周期 LCG 的距离，适合把两次内存观测之间的实际消耗量算出来；不能只用“每帧固定 2 次”这一假设。

### 消耗量校准

不同场景消耗不同。Mystery Gift 菜单的实测值约为每帧 2 次，但标题、菜单、野外、战斗、文本和链接状态不能共用一个常数。运行时应维护 SceneRate：

1. 连续读取 16–32 个稳定快照。
2. 对每一对状态估算 RNG turns，同时记录客体帧差。参考 `FRLGSwitchSeedFarmer/operation_rng_test.py` 的固定 32 轮 LCG 距离算法，避免实时路径每次调用都会重建查找表的 baby-step/giant-step；先与现有 `lcg.distance()` 交叉验证，完整搜索保留给低频重同步或离线分析。
3. 用中位数和 MAD 排除读取撕裂、暂停帧和菜单切换帧。
4. 只有当一个场景连续达到稳定阈值，才允许倒计时；场景变化立即使计划失效并重新校准。

目标帧应按观测到的 RNG turns 计算，不能用宿主睡眠毫秒数直接换算。操作消耗校准应在同一地点配对记录按键窗口和等时长空等窗口，并保存实际按键序列、执行耗时、RNG advances 与 VBlank 前后值；只有场景和刷新范围可比时才解释净差。必要时将游戏置于可控等待态（例如使用已有 noencounter 或暂停输入）再开始计数。

### 宝可梦生成

- 脚本定点/礼物优先实现 CreateScriptedWildMon 的 Method 1：PID 两次 draw，IV 两次 draw，OT 为玩家时不额外消耗。
- 普通野生实现方法 1/2/4 候选，保留现有 recover_wild_state 对 PID 半字顺序、IV 顺序、IV 前/中间空 draw 的搜索逻辑。
- 性格循环、性别、特性、觉醒力量和游走兽单独建模；不得把“脚本四抽”模型套到普通野生。
- 结果验证优先调用客体已有的 GetMonData（现有 ivs hook 的做法），避免直接猜加密结构；进程后端需要时再实现 Gen 3 BoxMon 解密器，并用已知 .pk3 golden vectors 测试。

闪光判断使用：

~~~text
shiny_value = TID ^ SID ^ PID_high ^ PID_low
shiny       = shiny_value < 8
~~~

## 自动操作闭环

实现 pokeldn/frlg/rng/runner.py，状态机如下：

~~~text
DISCONNECTED
  -> IDENTIFYING
  -> CALIBRATING
  -> READY
  -> PLANNED
  -> WAITING_TARGET
  -> TRIGGERING
  -> VERIFYING
  -> SUCCEEDED / RESYNC / FAILED_SAFE
~~~

核心循环：

~~~python
while run.active:
    try:
        obs = backend.snapshot()
    except BackendError as error:
        controller.release_all()
        controller.cancel_pending()
        planner.invalidate(reason="backend-error")
        run.resync_or_fail(error)
        continue
    if obs is None or not validator.accept_or_drop(obs):
        controller.release_all()
        controller.cancel_pending()
        planner.invalidate(reason="invalid-snapshot")
        continue
    if (validator.scene_changed() or validator.profile_changed()
            or validator.seed_epoch_changed() or validator.mapping_changed()):
        controller.release_all()
        controller.cancel_pending()
        planner.invalidate()
        continue

    if planner.needs_plan(obs):
        plan = planner.find_first_target(obs, request)
        planner.arm(plan, input_latency=controller.calibrated_latency())

    if planner.ready_to_trigger(obs):
        token = controller.trigger(plan.trigger)
        if not token.acknowledged:
            controller.release_all()
            controller.cancel_pending()
            planner.invalidate(reason="input-not-acknowledged")
            continue
        planner.mark_triggered(obs, token)

    if planner.in_verification_window(obs):
        result = verifier.read_result(obs)
        if result.matches(request):
            return Success(plan, result)
        planner.recover_from_miss(obs, result)

controller.release_all()
controller.cancel_pending()
~~~

关键规则：

- 触发前要预留控制器延迟、一个或多个输入帧以及遭遇动作自身的消耗量；预留值通过固定目标做一次校准，不写死成毫秒。
- 触发窗口内若连续两次读不到有效快照，必须停止按键并转入 RESYNC，不能继续盲按。
- 用户停止、读取异常、profile 失效或控制器传输失败时，必须释放所有当前按住的按键，并取消尚未派发的后续输入；不得用补按来猜测或恢复游戏状态。
- 结果验证优先读 PID/IV/性格；只看到“战斗开始”不能判定命中。
- 命中后默认停在战斗画面，不自动捕获、存档或覆盖存档；这些是后续显式功能。
- 目标找不到、距离超过上限、profile 不匹配、内存读取权限失效、RNG 速率不稳定时，进入 FAILED_SAFE 并保存诊断包。

## 计划加入的文件

PokeLDN 侧建议按以下边界改动：

~~~text
pokeldn/frlg/rng/model.py             观测、目标、计划、结果数据类
pokeldn/frlg/rng/backend.py           MemoryBackend 协议和状态机
pokeldn/frlg/rng/ryujinx.py           Windows 只读进程后端、profile、批量读取
pokeldn/frlg/rng/guest.py             rng-trace/memory-dump/resident 适配器
pokeldn/frlg/rng/sampler.py           快照一致性、帧率/RNG 消耗校准
pokeldn/frlg/rng/encounters.py        野生/脚本/静态生成模型
pokeldn/frlg/rng/planner.py           目标搜索和输入延迟补偿
pokeldn/frlg/rng/verifier.py          PID/IV/性格/闪光结果复核
pokeldn/frlg/rng/runner.py            自动流程状态机、JSONL 日志
tools/switch/process_memory.py        Windows 进程内存原语和区域枚举
asm/resident/rng-telemetry.s          可选常驻采样/overlay hook
pokeldn/frlg/rom/buffer_script.py     注册 telemetry payload（若需要）
bin/frlg_rng_auto.py                  CLI：probe/observe/plan/run/verify
gui/views/frlg_rng.py                 后续桌面 GUI
tests/test_frlg_rng_*.py              单元、模拟器 fixture、故障恢复测试
~~~

frlg-auto-rng 侧只增加一个薄适配器：读取 PokeLDN 产生的 JSONL RngObservation，把结果送入现有 GUI/日志/控制器；现有 EasyCon/OCR 流程保持默认不变。

## rng-telemetry Resident hook 设计（零售机增强项）

现有 Resident 区间是 0x0203FC00..0x02040000，所有常驻 hook 共用 1 KiB，不能随意扩大。建议先做 128–256 字节的最小 ring buffer，并由生成器检查代码、数据和 ring 不重叠：

~~~text
header:
  magic      u32  = 0x46524E47  # FRNG
  version    u16
  record_sz  u16
  write_seq  u32
  flags      u32
record[n]:
  seq        u32
  vblank     u32
  rng        u32
  callback1  u32
  callback2  u32
  vcount     u16
  scene      u16
  crc16      u16
  reserved   u16
~~~

hook 每 VBlank 只做固定数量的读写，不调用可能递归或耗时的 ROM 函数；宿主进程后端可在其内存映射已验证后读取 ring。ring buffer 本身不提供零售机到 PC 的回传通道。零售机只有在实现并验证了屏幕 overlay 显示数据供 OCR 读取，或已验证某个可在野外调用的 PokeLDN 读取流程后，才把 telemetry 纳入验收；否则将此 hook 留作后续实验。hook 必须保留原始 gIntrTable[4]，软重置后自动失效；需要跨重启时沿用 save-head/MOM 重新安装流程。

记录提交顺序必须固定：writer 先写除 `seq` 外的字段，再写 `crc16`，最后以一次对齐的 `seq` 写入作为 commit；`write_seq` 只在 commit 后递增。reader 先读 `seq`，复制整条记录，再读一次 `seq` 和 `crc16`，只有两次序号相同且 CRC 正确才接受。序号回绕、记录大小或 magic/version 不匹配都要丢弃该条并触发重采样，不能把 CRC 当作无撕裂保证。

## 测试和验收

### 离线单元测试

- step/unstep/advance/distance 往返和已知样例。
- recover_wild_state 覆盖 Method 1/2/4、两种 PID 半字顺序、性格循环和空 draw。
- BPRF/BPGF/BPRE/BPGE 地址表、ROM header 和 build profile 拒绝未知版本。
- Gen 3 .pk3 解密/IV/性格解析 golden vectors。
- RngObservation 序列化、坏 magic、序号回绕、快照撕裂重试。

### 模拟器集成验收

1. 第 0 阶段先在 Ryujinx BPRE 上完成只读 PoC；通过后再启动英文 FR 和 LG，识别 BPRE/BPGE，不靠手填地址。
2. 连续读取至少 10 秒，RNG advances 与已验证的客体计数/交叉观测一致；暂停、菜单、战斗切换时能检测速率变化。
3. 读取 TID/SID 与保存档案一致；重载、进入战斗后 SaveBlock 指针变化时仍能重新定位。
4. 先做只读目标搜索，再开启虚拟手柄完成一个低难度闪光目标；至少 20 次重复中记录命中、偏移、丢帧和误触发。
5. 模拟器重启、窗口失焦、读句柄失效、profile 不匹配、读取超时都必须停止输入并留下可复现日志。

### 零售 Switch 验收

- 英文 FR/LG 各完成一次 trainer-id-probe、rng-trace、save-dump。
- shiny/ivs Resident hook 的观测与进程后端/客体生成模型对照。
- 客体内 mon-seek 结果至少复核 PID、性格、6 项 IV、闪光值；不能只凭画面颜色验收。
- 任意软重置后 hook 状态、save loader、误遇敌保护都符合文档，不破坏存档。

### 性能指标

- PoC 实测可持续达到 30 Hz 后，才把它作为进程后端目标；单次批量读取 P95 < 5 ms，采样、RNG turns 估算和日志在内的总 CPU 占用目标 < 5%。性能报告必须拆分读取与计算耗时。
- 读取后端不得造成 Ryujinx 可见掉帧；连续 10 分钟无句柄泄漏、线程增长或日志无限膨胀。
- 异常路径检测到失效后不得再提交新的控制器输入；报告从故障发生到最后一次输入派发的实际毫秒数和客体帧数。只有使用并验证了帧同步输入机制后，才可把“一帧内停止”作为保证。

## 分阶段交付

### 第 0 阶段：Ryujinx 只读可行性 PoC（阶段门槛）

先只支持一台本机 Ryujinx 和英文 BPRE，不做虚拟手柄输入或进程写入。证明 ROM header 可识别、客体到宿主的映射可重复定位、gRngValue/TID/SID 能稳定读取，并在游戏运行、暂停/继续及重载后验证失效检测和重新定位；记录实际采样频率、读取耗时及与客体侧可用观测的交叉校验结果。若无法确认映射或数据一致性，停止扩展进程后端，保留 PokeLDN 客体后端路线。PoC 通过后再增加 BPGE 和通用 profile。

### 第 1 阶段：离线规划器

把当前 LCG、rng_countdown 和 recover_wild_state 整理成统一 API；用探针记录重放测试不同场景的 RNG 消耗；输出“当前状态、目标状态、预计 turns/frames、触发窗口”。

### 第 2 阶段：结果复核

加入队伍/敌方宝可梦读取和 Gen 3 解密；能判断“命中/偏移/无法判定”。先人工按键，系统只读和报告。

### 第 3 阶段：单目标自动化

在用户每次显式启用控制器输入后接入 Ryujinx 虚拟手柄，只支持一个低风险触发路线（建议脚本定点或固定草地）；完成停止、重连、超时、命中后停机。进程内存仍保持只读，不自动捕获或存档。

### 第 4 阶段：PokeLDN/零售交叉验证

增加 guest backend、rng-trace 和 Resident hook 的测试；对照英法四种 build；把不能实时读数的场景明确降级，不伪装成同样的实时能力。Resident telemetry ring 只有在定义并验证零售机的读取/显示路径后才进入实现；ring buffer 本身不构成向 PC 回传数据的通道。

### 第 5 阶段：GUI 和现有工具桥接

在 PokeLDN GUI 加“FRLG RNG”页，或让 frlg-auto-rng 通过 JSONL 接入；保留 EasyCon/OCR 作为 fallback，显示 backend、profile、读取频率、当前场景、目标距离和验证结果。

## 风险和明确边界

| 风险 | 处理方式 |
|---|---|
| Ryujinx 更新改变内存布局 | 静态 profile 按 ROM hash + emulator version；宿主地址每次会话重新定位；未知版本只读探测，拒绝自动操作 |
| 客体帧和 RNG 消耗不固定 | 观测 distance，按场景校准；场景变化使计划失效 |
| SaveBlock 地址重滚 | 每次快照重新读取指针，战斗/加载前后不复用旧地址 |
| 普通 dump 读取易失 gRngValue 造成 CRC 错误 | 使用 rng-trace 或进程读取，禁止跨该地址的普通 dump |
| 宝可梦数据加密/子结构顺序 | 先调用客体 GetMonData，进程端解密器用 golden vector 验证 |
| 零售机没有持续 RAM 回传通道 | 用客体内搜索、overlay/OCR 或改用模拟器后端；不声称已有功能能实时回传 |
| 写入或意外按键改变游戏状态 | 进程内存和存档保持只读；控制器输入每次运行单独显式启用；停止/异常时释放按键并取消后续输入 |
| 与现有 EasyCon 工具冲突 | JSONL/API 适配，两个后端可独立运行，默认不切换旧流程 |

## 第一批应立即实现的任务

1. 在 tools/switch/process_memory.py 先做支撑第 0 阶段的最小只读 PoC；仅在 Ryujinx BPRE 映射和采样通过阶段门槛后，才扩展成通用进程读取模块。
2. 在 pokeldn/frlg/rng/model.py 建立 RngObservation、RngTarget、RngPlan、RngResult。
3. 以 builds.py 作为 GBA 客体地址的单一来源；单独记录经验证的模拟器映射规则，不复制四份宿主地址 profile。先完成 BPRE 探针，再扩展到 BPGE。
4. 增加 probe --jsonl，采集 10 秒 gRngValue + callback + TID/SID + pointers。
5. 用真实采样建立场景速率 fixture，补齐 LCG 距离和撕裂重试测试。
6. 完成只读目标搜索和结果复核，人工按键跑通一轮。
7. 只读目标搜索和人工按键复核通过后，才显式启用虚拟手柄自动输入并进行低风险重复测试；任何 profile 或读取置信度不足都必须拒绝启动。

完成这七项后，项目已经具备可审查的“内存读取 RNG 核心”；Resident hook、零售机实时回传和完整 GUI 都可以在这个稳定核心之上继续迭代。
