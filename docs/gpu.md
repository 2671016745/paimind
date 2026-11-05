# GPU 检测 / GPU detection

## 实测环境：Quadro T1000（4 GB）/ Measured on Quadro T1000 (4 GB)

本机是一块 **compute capability 7.5** 的卡，实测结论直接可用：
> *This machine has a compute capability 7.5 GPU; the findings below are directly usable.*

| 引擎 / Engine | 结果 / Result | 证据 / Evidence |
|---|---|---|
| llama.cpp | ✅ 可用，**必须 `-fa on`** | 84 tok/s，3.5 s；缺 flag 会静默退 CPU（228 ms/token，53.9 s） |
| vLLM | ✅ 可用，**必须 `--dtype half`** | 0.5B 模型跑通；`bfloat16` 直接报错退出 |
| vLLM + bfloat16 | ❌ 拒绝启动 | `Bfloat16 is only supported on GPUs with compute capability of at least 8.0` |
| vLLM + 1.5B | ❌ 显存不足 | 权重 2.89 + 激活 1.39 = 4.28 GiB > 3.68 可用，KV cache **-0.70 GiB** |

**硬约束 / Hard limit**：vLLM 的 V1 引擎要求 compute capability ≥ 8.0，7.5 会**自动回退 V0**（仍可用，只是慢一些）。
> *vLLM's V1 engine needs compute capability ≥ 8.0; 7.5 silently falls back to V0 (usable, just slower).*

**4 GB 显存能跑什么 / What 4 GB can run**
```
0.5B FP16（约 1 GB 权重）  →  ✅ 可以，--enforce-eager --max-model-len 2048
1.5B FP16（约 2.9 GB 权重） →  ❌ 不行，KV cache 为负
1.5B AWQ（约 1 GB 权重）    →  ✅ 大概率可以（需 --quantization awq）
7B AWQ                     →  ❌ 需要 ≥ 8 GB
```

**可用的启动参数（本机实测通过）/ Flags that work here**
```bash
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/Qwen2.5-0.5B-Instruct \
  --served-model-name qwen2.5-0.5b \
  --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.92 --max-model-len 2048 \
  --dtype half --enforce-eager
```

**WSL 额外注意 / WSL note**：vLLM 检测到 WSL 会强制 `pin_memory=False`（日志有 WARNING），性能略降。
> *vLLM forces `pin_memory=False` when WSL is detected — slightly slower.*

---

## 流程 / Flow

```
① 探测硬件
   Win  wmic path win32_VideoController
   Linux  lspci
   macOS  system_profiler SPDisplaysDataType
   Android  Vulkan / ro.hardware.egl
   iOS  Metal
        ↓
② 探测驱动 / 运行时
   NVIDIA  nvidia-smi
   AMD / Intel  DirectML / Vulkan / OpenCL
   Apple  Metal
        ↓
③ 分支
   有硬件 + 驱动就绪 → GPU，状态栏「GPU 加速 · <设备名>」
   无 GPU           → CPU「CPU 模式」
   有 GPU 无驱动     → 弹框引导
        ↓
④ 探测 2 秒超时 + try/except，异常降级 CPU，不阻塞启动
⑤ 「重新检测」按钮
```

---

## 各平台探测命令 / Per-platform commands

| 平台 / Platform | 命令 / Command | 解析 / Parsed from |
|---|---|---|
| Windows | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` | 型号 + 显存 / model + VRAM |
| Windows | `wmic path win32_VideoController get Name,AdapterRAM,DriverVersion /format:csv` | 型号 + 驱动版本 |
| Linux | `nvidia-smi …`，失败则 `lspci -nn` | 型号（无显存）/ model only |
| macOS | `system_profiler SPDisplaysDataType` | `Chipset Model` / `VRAM (Total)` |
| macOS | `sysctl -n hw.memsize` | 总内存 / total memory |
| Android | Vulkan / `ro.hardware.egl` | 由客户端注入 / injected by the client |
| iOS | Metal | 由客户端注入 / injected by the client |

---

## 驱动缺失引导 / Missing-driver guidance

检测到显卡但缺少驱动时弹框：
> *When a GPU is present but the driver is missing:*

```
检测到显卡但缺少驱动
你的 NVIDIA 显卡已识别，但驱动未就绪，将使用 CPU 运行。

[前往官网下载驱动]   [稍后再说（本会话不再弹）]
```

- 按钮链接到 **NVIDIA / AMD / Intel 官方驱动页**；
  *the button links to the official NVIDIA / AMD / Intel driver pages;*
- 「稍后再说」在**本会话**内不再弹；
  *"later" suppresses it for the current session only;*
- **移动端与 macOS 不弹驱动引导**，仅状态栏提示。
  *mobile and macOS never show the driver prompt — a status-bar note only.*

---

## Metal / CoreML 后端选择 / Backend selection

macOS 上 ONNX Runtime 的执行提供者优先级：
> *On macOS the execution providers are tried in this order:*

```
MetalExecutionProvider  →  CoreMLExecutionProvider  →  CPUExecutionProvider
```

- Apple Silicon：显示「GPU 加速 · Apple M2 (Metal)」；
  *Apple Silicon: "GPU acceleration · Apple M2 (Metal)";*
- Intel Mac 无独显：显示「CPU 模式」；
  *Intel Mac without a discrete GPU: "CPU mode";*
- **状态栏如实显示实际生效的后端**，不误报。
  *the status bar reports the backend that actually took effect — no false claims.*

Windows 上同理：`CUDAExecutionProvider` → `DmlExecutionProvider` → `CPUExecutionProvider`。
> *Same idea on Windows: `CUDAExecutionProvider` → `DmlExecutionProvider` → `CPUExecutionProvider`.*

> **本机提示**：`onnxruntime` 的 CPU wheel 不含 CUDA/DML。要用 GPU 请换装 `onnxruntime-gpu`（NVIDIA）或 `onnxruntime-directml`（AMD/Intel）。
> **Note**: the CPU wheel of `onnxruntime` ships no CUDA/DML. For GPU, install `onnxruntime-gpu` (NVIDIA) or `onnxruntime-directml` (AMD/Intel).

---

## 常见排查 / Troubleshooting

| 现象 / Symptom | 原因 / Cause | 处理 / Fix |
|---|---|---|
| 有显卡但显示 CPU 模式 | 装了 CPU 版 onnxruntime | 换 `onnxruntime-gpu` / `-directml` |
| | GPU shown as CPU | CPU wheel installed | swap in the GPU wheel |
| 显示 GPU 但很快 | DirectML 回退 / 集显 | 正常，用显存大的独显 |
| | GPU shown but slow | DirectML fallback / iGPU | expected; use a discrete GPU |
| 显存识别为 4 GB 上限 | `wmic AdapterRAM` 32 位截断 | 以 `nvidia-smi` 为准 |
| | VRAM capped at 4 GB | 32-bit `wmic AdapterRAM` clamp | trust `nvidia-smi` |
| macOS 上 Metal 未生效 | onnxruntime 版本旧 | 升级 onnxruntime |
| | Metal not used on macOS | old onnxruntime | upgrade onnxruntime |
| 探测卡住 | 外部命令挂起 | 2 秒超时后降级 CPU |
| | probe hangs | external command hangs | 2s timeout then CPU fallback |

---

## 与档位判定的关系 / Relation to tiers

GPU 检测结果直接影响三态：
> *GPU detection feeds directly into the tri-state:*

- **builtin L3**：无 GPU 或显存 < 6 GB → `warning`（不是 disabled）；
  *no GPU or < 6 GB VRAM → `warning` (not disabled);*
- **vllm**：无 NVIDIA GPU → 全部 `disabled`；
  *no NVIDIA GPU → every tier `disabled`;*
- **ollama / llamacpp L3**：无 GPU → `warning`；有 GPU 且显存 ≥ 6 GB → `ready`。
  *no GPU → `warning`; ≥ 6 GB VRAM → `ready`.*

详见 [docs/tiers.md](tiers.md)。
> *See [docs/tiers.md](tiers.md) for the full rules.*
