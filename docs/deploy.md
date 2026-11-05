# 部署方式 / Deployment

## 总览 / Overview

| 方式 / Mode | 引擎 / Engine | 场景 / Use case | 硬件要求 / Requirement |
|---|---|---|---|
| builtin | ONNX Runtime | 全平台、离线、低配、手机 | 见 [tiers.md](tiers.md) |
| ollama | Ollama | 低配本、Apple Silicon、单用户 | 可用内存 ≥ 档位峰值；纯 CPU 可跑 L0–L2 |
| vllm | vLLM | 生产高并发、GPU 服务器 | **必须 NVIDIA GPU**；7B AWQ 约 4–6 GB 显存 |
| llamacpp | llama-server | 轻量自托管、AMD/Apple GPU | 同 Ollama |
| docker | 上述任一容器化 | 生产、隔离、CI/CD | 随引擎 |

**默认 builtin，首装只带 L0。**
> *builtin is the default and only L0 ships on first install.*

---

## 1. 内置 ONNX（默认）/ Builtin ONNX (default)

```bash
# Windows / Linux
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
python -m desktop            # 或 / or: python -m server

# macOS Apple Silicon
uv venv --python 3.12 .venv && source .venv/bin/activate
pip install -r requirements-macos-arm64.txt
python -m desktop

# macOS Intel
uv venv --python 3.12 .venv && source .venv/bin/activate
pip install -r requirements-macos-x86_64.txt
python -m desktop
```

切换部署模式不影响已下载的 ONNX 文件。
> *Switching modes never affects the downloaded ONNX files.*

---

## 2. Ollama / Ollama

```bash
# 安装 / install
#   Windows / macOS: https://ollama.com/download
#   Linux:
curl -fsSL https://ollama.com/install.sh | sh

# 拉取 / pull
ollama pull qwen2.5:0.5b     # L0
ollama pull qwen2.5:1.5b     # L1
ollama pull qwen2.5:3b       # L2
ollama pull qwen2.5:7b       # L3
```

App 接入 `config.yaml` 的 `engines.ollama.base_url`，设置页一键测试。
> *The app reads `engines.ollama.base_url`; Settings has a one-click test.*

自定义模型通过 Modelfile 导入 GGUF（`PARAMETER temperature 0.6`、`num_ctx 4096`）：
> *Custom models import via a Modelfile:*

```dockerfile
# deploy/ollama.Modelfile
FROM ./qwen2.5-1.5b-instruct-q4_k_m.gguf
PARAMETER temperature 0.6
PARAMETER num_ctx 4096
```
```bash
ollama create qwen2.5-1.5b -f deploy/ollama.Modelfile
```

适用 L0–L2 纯 CPU / 低显存；L3 需 GPU 或判 warning。不适用高并发生产。
> *L0–L2 on CPU or low VRAM; L3 needs a GPU or is a warning. Not for production concurrency.*

---

## 3. vLLM（生产 / 高并发）/ vLLM (production)

> **需要 NVIDIA GPU + Linux。** Windows / macOS 不可用。
> ***Requires NVIDIA GPU + Linux.*** *Not available on Windows or macOS.*

```bash
# WSL2 Ubuntu（Windows 用户）/ WSL2 Ubuntu (for Windows users)
python3 -m venv .venv-vllm && source .venv-vllm/bin/activate
pip install -r requirements-vllm.txt

# 启动 7B AWQ / serve 7B AWQ
vllm serve Qwen/Qwen2.5-7B-Instruct-AWQ \
  --quantization awq --max-model-len 4096 --port 8000

# 或用 Docker / or Docker
docker compose -f deploy/docker-compose.vllm.yml up -d
```

### ⚠️ PyPI 不通时：离线 wheel 方案 / When PyPI is unreachable

本机实测：WSL 里 `pip install vllm` 会失败 —— **PyPI 被阻断**，国内镜像**只有源码包没有 wheel**（源码编译要 CUDA 工具链 + 数小时）。
> *Measured here: `pip install vllm` fails in WSL — PyPI is blocked and the Chinese mirrors carry only sdists, so a source build needs the CUDA toolkit and hours.*

**可行办法 / what works**：在 Windows 侧（能连 PyPI）下载 Linux wheel，再喂给 WSL venv。
> *Download the Linux wheels from Windows (which can reach PyPI) and feed them to the WSL venv.*

```bash
# 1) Windows 侧下 Linux wheel / fetch Linux wheels on Windows
python -m pip download vllm==0.9.2 \
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
  --python-version 312 --implementation cp --abi cp312 --abi abi3 \
  --only-binary=:all: -d .tools/wheels092

# 2) WSL 侧离线安装 / install offline in WSL
. .vllm-venv/bin/activate
pip install --no-index --find-links /mnt/d/ai/paimind/.tools/wheels092 vllm
```

**坑 / Gotchas**
- 依赖是**精确版本锁定**的（`xgrammar==0.1.19`、`xformers==0.0.30`）—— 下「最新版」永远不满足解析器，必须按报错里的完整 specifier 下。
  *Dependencies are pinned exactly; "latest" never satisfies the resolver — use the full specifier from the error.*
- 新版本可能要求**尚未发布**的依赖（0.29.0 要 `llguidance>=1.7.0`，镜像最新才 1.6.1）—— 改装稍旧的稳定版。
  *A newer release may require an unpublished dependency — fall back to an older stable version.*
- 已自动化：`.tools/install_vllm_wsl.py` 循环「装 → 解析缺哪个 → 补下 → 再装」。
  *Automated: `.tools/install_vllm_wsl.py` loops install → parse → fetch → retry.*

OpenAI 兼容地址 `http://localhost:8000/v1`，`api_key` 任意非空。
> *OpenAI-compatible endpoint at `http://localhost:8000/v1` with any non-empty key.*

适用 L2/L3（AWQ）；L0/L1 收益不大但可运行。不适用 macOS、低配本、单用户离线。
> *Fits L2/L3 (AWQ). L0/L1 run but gain little. Not for macOS, low-end laptops or offline single users.*

---

## 4. llama.cpp / llama.cpp

```bash
# 从 llama.cpp Release 下载预编译二进制 / grab a prebuilt binary from the releases
# Windows CUDA 构建需要同时下载 cudart 包，两个 zip 解压到同一目录。
# The Windows CUDA build also needs the cudart zip — extract both into one folder.
llama-server -m qwen2.5-1.5b-instruct-q4_k_m.gguf --port 8080 -c 2048 -ngl 99 -fa on
```

OpenAI 兼容地址 `http://localhost:8080/v1`。
> *OpenAI-compatible endpoint at `http://localhost:8080/v1`.*

### ⚠️ 必须确认真的用了 GPU / You must verify the GPU is actually used

llama.cpp 在 CUDA 初始化失败时会**静默回退 CPU**，不报错、不警告。本机实测：
> *llama.cpp falls back to CPU silently when CUDA init fails — no error, no warning. Measured on this machine:*

| 现象 / Symptom | prompt eval | 决策延迟 / latency |
|---|---|---|
| 静默回退 CPU / silent CPU fallback | 228 ms/token（4.4 tok/s） | 53894 ms |
| 真正 offload / real offload | 11.9 ms/token（84 tok/s） | 3470 ms |

**三步确认 / three-step check:**
```bash
# 1) 设备是否被识别（应列出 CUDA0）/ is the device visible?
.tools/llama.cpp/llama-server.exe --list-devices

# 2) 启动日志里应有 CUDA 相关行，且 n_threads 不应是瓶颈
#    the startup log should mention CUDA
.tools/llama.cpp/llama-server.exe -m model.gguf -ngl 99 -fa on

# 3) 推理瞬间采样利用率（应看到 100% + 显存占用）
#    sample utilisation during inference — expect ~100% and VRAM in use
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader -l 1
```

Quadro T1000（4 GB）实测 1.5B Q4_K_M 占 **1128 MiB**，跑得很宽裕；7B 显存不够。
> *On a 4 GB Quadro T1000 the 1.5B Q4_K_M uses **1128 MiB** — comfortable; a 7B will not fit.*

比 Ollama 更底层，适合精细控制上下文、批处理、非 NVIDIA GPU；纯 CPU 可跑 GGUF。
> *Lower-level than Ollama — fine-grained control over context and batching, and it works on non-NVIDIA GPUs; GGUF runs on CPU.*

---

## 5. Docker / Docker

```bash
# Ollama / Ollama
docker run -d --gpus=all -v ollama:/root/.ollama \
  -p 11434:11434 --name ollama ollama/ollama
# 或 compose / or compose
docker compose -f deploy/docker-compose.ollama.yml up -d

# vLLM（需 NVIDIA GPU + Linux）
docker compose -f deploy/docker-compose.vllm.yml up -d
```

App 通过 `http://host:port/v1` 连接容器内引擎，`engines.*.base_url` 填宿主机地址。
> *The app connects to the engine inside the container at `http://host:port/v1`; set `engines.*.base_url` to the host address.*

**docker 部署模式在 App 内不直接管理容器**，只负责探测和连接；容器生命周期由 docker compose 或编排系统管理。
> *The app never manages containers — it only probes and connects; docker compose or an orchestrator owns their lifecycle.*

容器重启后探测状态自动刷新。
> *Engine status refreshes automatically after a container restart.*

### 构建 App API 镜像 / Building the App API image

```bash
docker build -f Dockerfile -t paimind-api .
docker run -d -p 8686:8686 -v paimind-data:/app/data --name paimind paimind-api
```

**构建慢或随机失败 / Slow or flaky builds**：镜像默认走官方 PyPI。在直连不稳定（例如中国区网络）的环境里，构建可能在不同依赖包上随机失败 —— 换镜像或加时长即可：
> *The image uses the official PyPI by default. Where that connection is unreliable, builds fail on random packages — point pip at a mirror or raise the timeout:*

```bash
docker build -f Dockerfile -t paimind-api \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PIP_RETRIES=5 --build-arg PIP_TIMEOUT=60 .
```

---

## 6. App API 与外部 vLLM 分离部署 / Split deployment

```
┌────────────────  CPU 节点 / CPU node ────────────────┐
│  paimind API (Docker)                                │
│  engines.vllm.base_url = http://<GPU节点>:8000       │
└───────────────────────┬──────────────────────────────┘
                        │ HTTP /v1/chat/completions
                        ▼
┌────────────────  GPU 节点 / GPU node ────────────────┐
│  vLLM  serve Qwen2.5-7B-Instruct-AWQ                  │
└───────────────────────────────────────────────────────┘
```

`server/` 可独立部署到任意支持 Python 的 PaaS / VM；App API 作为轻量网关跑在 CPU 节点。
> *`server/` deploys anywhere Python runs; the API acts as a thin gateway on a CPU node.*

```bash
docker build -t paimind-api .
docker run -d -p 8686:8686 \
  -e PAIMIND_ROOT=/app \
  -v paimind-models:/app/models \
  paimind-api
```

---

## 环境变量 / Environment variables

| 变量 / Variable | 用途 / Purpose |
|---|---|
| `PAIMIND_ROOT` | 项目根 / project root |
| `PAIMIND_CONFIG` | 指定 config.yaml / config path |
| `PAIMIND_SECRETS` | 密钥文件路径（无 keyring 时）/ secrets file (no keyring) |
| `PAIMIND_USAGE` | 用量文件路径 / usage file path |
| `PAIMIND_RUNTIME` | 运行时状态路径 / runtime state path |
| `PAIMIND_ONLINE_CACHE` | 联网缓存路径 / online cache path |
| `PAIMIND_CUSTOM_MODELS` | 自定义模型目录 / custom models dir |
| `PAIMIND_CHECKSUMS` | 校验清单路径 / checksums path |
| `PAIMIND_PWA` | PWA 目录 / PWA directory |

---

## macOS 打包 / macOS packaging

```bash
# 构建 .app / build .app
pyinstaller --windowed --name paimind desktop/main.py

# 签名 / sign
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  --options runtime --entitlements deploy/entitlements.plist \
  dist/paimind.app

# 公证 / notarize
xcrun notarytool submit dist/paimind.zip --wait --keychain-profile "paimind-notary"

# 双架构 / dual architecture
pyinstaller --target-architecture universal2 --windowed desktop/main.py
```

- 产物双架构 `.dmg` 或 `universal2`；
  *build a dual-architecture `.dmg` or universal2 binary;*
- 签名 + notarization 避免 Gatekeeper 拦截；
  *sign and notarize to satisfy Gatekeeper;*
- Mac App Store 需处理沙盒与网络 entitlement；
  *the Mac App Store build needs sandbox and network entitlements;*
- 首次下载模型到 `~/Library/Application Support/` 需处理 App Sandbox 路径与用户选择目录；
  *handle App Sandbox paths and a user-chosen directory for the first download;*
- Token 存 macOS Keychain；
  *tokens live in the macOS Keychain;*
- API 监听 `127.0.0.1:8686` 首次启动说明防火墙弹窗；
  *the first API start explains the firewall prompt;*
- vLLM 在 macOS 整体不可用，提示「vLLM 需 Linux + NVIDIA GPU，macOS 请用 builtin/Ollama/llama.cpp」。
  *vLLM is unavailable on macOS with an explanatory message.*

---

## 移动端打包 / Mobile packaging

```bash
# PWA（零安装）/ PWA (zero install)
python -m server          # 手机访问 http://<电脑IP>:8686/app

# Flutter / Flutter
cd mobile/flutter
flutter build apk --release          # Android
flutter build ios --release          # iOS（需 macOS + Xcode）
```

移动端与桌面共享 ONNX 模型与核心逻辑。
> *Mobile shares the ONNX models and core logic with desktop.*
