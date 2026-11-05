# 档位与可用性三态 / Tiers and the tri-state

## 三态定义 / Tri-state definitions

| 状态 / State | 含义 / Meaning | UI / UI |
|---|---|---|
| **ready** | 硬件达标，可下载/加载，或引擎可达且模型就绪 | 绿色卡片 / green card |
| | Hardware is sufficient, or the engine is reachable and the model is ready | |
| **warning** | 能跑但体验差（如 L3 无 GPU、Ollama 纯 CPU 跑 7B） | 黄色卡片 + 提示，允许使用 |
| | Runs but slowly (e.g. L3 without a GPU, 7B on CPU-only Ollama) | Yellow card + note, still usable |
| **disabled** | 硬件不达标或引擎不可达/未就绪 | 灰色卡片 + 锁图标，不可点击 |
| | Hardware insufficient, or engine unreachable/not ready | Grey card + lock, not clickable |

**关键：warning 不是禁用。** 黄色档位可以正常切换和使用，只是会给出性能提示。
> ***Key: warning is not disabled.*** *A yellow tier switches and runs normally, with a performance note.*

---

## 硬件检测口径 / Detection sources

| 指标 / Signal | Windows | macOS | Linux | Android | iOS |
|---|---|---|---|---|---|
| 可用内存 / Available | `GlobalMemoryStatusEx.ullAvailPhys` | `vm_stat` free+inactive+purgeable | `/proc/meminfo` MemAvailable | `ActivityManager.MemoryInfo.availMem` | `os_proc_available_memory()` |
| 总内存 / Total | 同上字段 | `sysctl hw.memsize` | MemTotal | 同可用 | 同可用 |
| CPU / CPU | `os.cpu_count` | 同 | 同 | `Build.SUPPORTED_ABIS` | `sysctl` |
| AVX2 | `IsProcessorFeaturePresent(40)` | `machdep.cpu.features` | `/proc/cpuinfo` flags | 跳过 / skipped | 跳过 / skipped |
| 磁盘 / Disk | `shutil.disk_usage` | `os.statvfs` | `shutil.disk_usage` | `StatFs` | `URLResourceValues` |
| GPU / GPU | `nvidia-smi` → `wmic` | `system_profiler SPDisplaysDataType` | `nvidia-smi` → `lspci` | Vulkan/egl | Metal |

**移动端必须用系统 available 接口**，不能用总内存。
> ***Mobile must use the system available-memory API***, never total memory.

### Apple Silicon 保守系数 / Apple Silicon conservative factor

Apple Silicon 是统一内存，GPU 与 CPU 共用同一池。标称 8 GB 实际约 6.8 GB 可用于纯 CPU 模型。
> *Apple Silicon uses unified memory — the GPU shares the same pool. A nominal 8 GB gives only ~6.8 GB to a CPU-only model.*

可用内存会乘以 `tiers.apple_silicon_memory_factor`（默认 `0.85`，可在 `config.yaml` 调整）。
> *Available memory is multiplied by `tiers.apple_silicon_memory_factor` (default `0.85`, configurable).*

---

## 判定规则 / Judgement rules

### builtin 模式 / builtin mode（按序短路 / short-circuits in order）

| 档位 / Tier | 可用内存下限 | 磁盘下限（未下载时） | CPU 要求 | 其他 |
|---|---|---|---|---|
| L0 | ≥ 1.5 GB | ≥ 500 MB | 64 位 | — |
| L1 | ≥ 3 GB | ≥ 1.5 GB | 64 位 +（x86 需 AVX2） | — |
| L2 | ≥ 6 GB | ≥ 3 GB | 64 位 +（x86 需 AVX2） | — |
| L3 | ≥ 12 GB | ≥ 6 GB | 64 位 +（x86 需 AVX2） | 无 GPU 或显存 < 6 GB → **warning**，不禁用 |

规则细节 / Rule details：
- 内存用**可用内存**判定；
  *memory is checked against* available *memory;*
- **已下载/已就绪不再校验磁盘**（字节已经花掉了）；
  *once downloaded, the disk floor is no longer enforced;*
- 所有下限含 **30% 余量**；
  *every floor already includes 30% headroom;*
- L2 有 GPU 但显存 < 4 GB → 仍 ready，提示「将用 CPU 推理」；
  *L2 with a GPU under 4 GB VRAM stays ready with a "will use CPU" note;*
- L3 无 GPU 或显存 < 6 GB → warning，**不是** disabled。
  *L3 without a GPU or with under 6 GB VRAM is a warning, not disabled.*

### 外部引擎模式 / External engine modes

| 模式 / Mode | 判定 / Rule |
|---|---|
| **ollama** | 引擎可达且模型已拉取；再按可用内存判定；L0–L2 ≥ 峰值 → ready；L3 无 GPU → warning；L3 有 GPU 且显存 ≥ 6 GB → ready |
| | Engine reachable and model pulled; then the memory ladder; L3 without GPU → warning, with ≥ 6 GB VRAM → ready |
| **llamacpp** | 同 ollama / same as ollama |
| **vllm** | **必须有 NVIDIA GPU**；无 GPU → disabled（原因「vLLM 需要 NVIDIA GPU，当前未检测到」）；有 GPU 按显存：L3 AWQ ≥ 8 GB → ready，4–8 GB → warning（调小 max-model-len），< 4 GB → disabled |
| | NVIDIA GPU mandatory; no GPU → disabled; with a GPU by VRAM: L3 AWQ ≥ 8 GB ready, 4–8 GB warning, < 4 GB disabled |
| **docker** | 容器健康 **且** 宿主机满足对应引擎规则 / container healthy **and** the host meets the engine's rules |

引擎不可达 → 该引擎所有档位 disabled，原因「Ollama 未启动」/「vLLM 服务未启动」/「llama-server 未启动」。
> *Unreachable engine → every tier disabled with "Ollama not running" / "vLLM not running" / "llama-server not running".*

---

## 自动推荐算法 / Recommendation algorithm

输入：`hw`（硬件快照）、`mode`（部署模式）、`engines`（各引擎状态）、`downloaded`（已下载档位）、`first_install`。
> *Inputs: hardware snapshot, deploy mode, engine states, downloaded tiers, first-install flag.*

```text
function recommend_tier(hw, mode, engines, downloaded, first_install):
    # 1) 评估所有 (tier, mode) 对
    statuses = [ evaluate_tier(t, mode, hw, engines, downloaded)  for t in L0..L3 ]

    # 2) 只保留 ready 与 warning —— disabled 一律不考虑
    survivors = [ s for s in statuses if s.state in {ready, warning} ]
    if survivors is empty:
        return { tier: L0, state: disabled, risk: true,
                 reason: "当前硬件不满足任何档位，建议升级内存或启用外部引擎" }

    # 3) 取参数最大的一档（L3 > L2 > L1 > L0）
    winner = max(survivors, key = param_rank)      # param_rank: L0<L1<L2<L3

    # 4) 只有 warning 时标记风险
    risk = (winner.state == warning)

    # 5) 一句话理由
    reason = f"可用内存 {hw.available_gb:.1f}GB、{gpu_name(hw) or 'CPU'}，推荐 {winner.tier}"

    # 6) 引擎附加信息
    if engines.ollama.reachable and engines.ollama.models:
        reason += "；Ollama 已就绪，可优先使用"
    if engines.vllm.reachable and engines.vllm.models:
        reason += "；vLLM 已就绪，服务端场景可优先使用"

    # 7) 首装：取推荐档与 L0 的较小者，先给一个能立刻用的模型
    tier_to_download = first_install ? min_by_param(winner.tier, L0) : winner.tier

    return { tier: winner.tier, state: winner.state, risk,
             reason, first_install_tier: tier_to_download }
```

### 区分部署模式 / Per deploy mode

```text
# 每个模式的候选集不同 —— 先按模式过滤，再排序
builtin:
    candidates = [t for t in L0..L3 if builtin_ok(t, hw, downloaded)]
    # 纯硬件判定：内存 / 磁盘 / CPU

ollama | llamacpp:
    if not engine.reachable:        return all_disabled("Ollama 未启动")
    if engine.model_missing:        return all_disabled("未拉取模型 X")
    candidates = [t for t in L0..L3 if memory_ok(t, hw) and engine_has_model(t)]
    # L3 无 GPU → warning（仍可跑，只是慢）

vllm:
    if no nvidia_gpu:               return all_disabled("vLLM 需要 NVIDIA GPU，当前未检测到")
    if not engine.reachable:        return all_disabled("vLLM 服务未启动")
    candidates = []
    for t in L0..L3:
        if vram(t) >= READY[t]:     candidates.append(ready)
        elif vram(t) >= WARN[t]:    candidates.append(warning)   # L3: 4–8GB
        else:                       candidates.append(disabled)  # L3: <4GB

docker:
    if container_unhealthy:         return all_disabled("Docker 容器未运行或健康检查未通过")
    # 宿主机满足对应引擎规则即可（容器只负责隔离）
    return recommend_tier(hw, inner_engine_mode, engines, downloaded, first_install)
```

### 阈值来源 / Where the thresholds live

全部集中在 `config.yaml` 的 `tiers.*`，改配置即可调整，不用改代码。
> *All thresholds live in `config.yaml` under `tiers.*` — tune behaviour without touching code.*

| 参数 / Parameter | 默认 / Default | 含义 / Meaning |
|---|---|---|
| `min_available_memory_gb` | L0 1.5 / L1 3 / L2 6 / L3 12 | 可用内存下限 / available-memory floor |
| `min_disk_mb` | L0 500 / L1 1500 / L2 3000 / L3 6000 | 未下载时的磁盘下限 / disk floor |
| `cpu_x86_avx2` | L0 false / L1+ true | 是否要求 AVX2 / AVX2 required |
| `gpu_vram_gb` | L3 = 6 | 低于此值降 warning / below → warning |
| `apple_silicon_memory_factor` | 0.85 | Apple Silicon 统一内存折扣 / unified-memory discount |

**余量 / Headroom**：所有下限已含 30% 余量（`disk_headroom_percent`）。
> *Every floor already includes 30% headroom.*

**已下载不再校验磁盘 / Downloaded tiers skip the disk check**：字节已经花掉了，再拦没意义。
> *The bytes are already spent; blocking again would serve no purpose.*

区分部署模式 / Per deploy mode：
- **builtin**：只按硬件选档；
  *builtin: hardware only;*
- **ollama / llamacpp**：先确认引擎可达且模型已拉取，再按内存选档；
  *ollama / llamacpp: confirm the engine and model first, then the memory ladder;*
- **vllm**：先确认 NVIDIA GPU，再按显存选档；
  *vLLM: confirm the NVIDIA GPU, then the VRAM ladder;*
- **服务端场景**：若 vLLM 可达，优先推荐 vLLM 模式。
  *server workloads: prefer vLLM when reachable.*

---

## 禁用态 UI / Disabled-state UI

点击 disabled 卡片**不下载、不加载**，只弹说明层：
> *Clicking a disabled card never downloads or loads — it opens an explainer:*

```
档位 L3 · vllm · disabled
原因：vLLM 需要 NVIDIA GPU，当前未检测到

需要什么            当前是什么                如何解决
需要 可用内存 ≥12GB   可用内存 19.8GB         安装 NVIDIA GPU 并装驱动
需要 NVIDIA GPU      未检测到                 或使用 builtin / Ollama 模式
需要 显存 ≥8GB        —                       调小 max-model-len
```

不提供「强制开启」。`allow_force_load` 是调试项，开启后仍做硬校验。
> *No "force enable". `allow_force_load` is a debug switch; hard checks still apply when it is on.*

---

## 动态刷新 / Live refresh

- 打开设置页 → 自动重新检测硬件 + 重新探测外部引擎；
  *opening Settings re-detects hardware and re-probes engines;*
- 「重新检测硬件」按钮随时可用；
  *a "re-detect hardware" button is always available;*
- 使用中档位变 disabled（内存被占、Ollama 被杀）→ **不强制卸载**，顶部橙条建议切换并给一键切换；
  *a tier going disabled mid-use does not force-unload; an orange banner suggests switching with one click;*
- 切换/下载/加载前**再次校验**内存与引擎可达性，不足则拒绝并提示实际缺口。
  *switching, downloading and loading re-validate memory and engine reachability, refusing with the actual shortfall.*

---

## 误判排查 / Troubleshooting

| 现象 / Symptom | 可能原因 / Likely cause | 处理 / Fix |
|---|---|---|
| 档位无故置灰 | 可用内存被占满 | 关程序后点「重新检测硬件」 |
| | Tier grey for no reason | Memory pressure — free some and re-detect |
| 显示内存与任务管理器不符 | 用了总内存口径（bug）或虚拟化预留 | 以 `/v1/hardware` 的 `available_bytes` 为准 |
| | Memory differs from Task Manager | Wrong metric, or hypervisor reservation | Trust `/v1/hardware` `available_bytes` |
| Apple Silicon 上 L2/L3 偏保守 | 0.85 系数生效 | 调 `tiers.apple_silicon_memory_factor` |
| | Over-conservative on Apple Silicon | The 0.85 factor applies | Tune `tiers.apple_silicon_memory_factor` |
| Intel Mac 无 AVX2 导致 L1+ 置灰 | CPU 太老 | 换 L0，或改用 Ollama 模式 |
| | L1+ grey on an Intel Mac | CPU lacks AVX2 | Use L0, or switch to Ollama |
| vLLM 全档 disabled | 无 NVIDIA GPU / 驱动缺失 | 换 builtin / Ollama / llama.cpp |
| | Every vLLM tier disabled | No NVIDIA GPU, or missing driver | Use builtin / Ollama / llama.cpp |

---

## 本机实测（Windows 10 / i7-10750H / 32 GB / Quadro T1000 4 GB）/ Measured on this machine

```
可用内存 19.8 GB · AVX2 ✓ · 磁盘 551 GB · GPU Quadro T1000 4.0 GB (CUDA)

builtin:  L0 ready · L1 ready · L2 ready · L3 warning（显存 4.0 < 6，将用 CPU 推理）
ollama:   引擎未启动 → 全部 disabled，原因「Ollama 未启动」
vllm:     L0 ready · L1 ready · L2 ready · L3 warning（显存 4.0，建议调小 max-model-len）
vllm(无GPU): 全部 disabled，原因「vLLM 需要 NVIDIA GPU，当前未检测到」
推荐:     L3（warning）· 首装先下 L0
```
