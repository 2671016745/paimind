# 模型 / Models

## 四档对比 / Tier comparison

| 档位 | 参数量 | 磁盘 | 峰值内存 | 建议设备 |
|---|---|---|---|---|
| Tier | Params | Disk | Peak RAM | Suggested device |
| **L0** | 0.3–0.5B | ~300 MB | ~1.2 GB | 老手机、低配本、纯 CPU |
| | | | | Old phones, low-end laptops, CPU only |
| **L1** | ~1.5B | ~1 GB | ~2.8 GB | 主流手机、普通笔记本 |
| | | | | Mainstream phones, ordinary laptops |
| **L2** | ~3B | ~2 GB | ~5 GB | 中端台式 / 核显本 |
| | | | | Mid-range desktops, iGPU laptops |
| **L3** | ~7B | ~4.5 GB | ~10 GB | 独显台式 |
| | | | | Desktops with a discrete GPU |

模型统一为 **Qwen2.5 Instruct INT4**，放在 `models/<tier>/`。
> *All tiers are Qwen2.5 Instruct INT4, stored under `models/<tier>/`.*

```
models/
├── checksums.json      内置 SHA256 清单 / bundled SHA256 manifest
├── L0/
│   ├── model.onnx      主模型 / main graph
│   ├── tokenizer.json
│   ├── tokenizer_config.json
│   ├── config.json
│   ├── manifest.json   档位元信息 / tier metadata
│   └── SHA256SUMS
├── L1/ … L3/
└── custom/             用户导入的 GGUF/ONNX / user-imported GGUF/ONNX
```

---

## 量化流程 / Quantisation

| 格式 / Format | 量化 / Quant | 用在哪 / Used by | 特点 / Notes |
|---|---|---|---|
| ONNX | INT4 | builtin | 默认路径，全平台离线 |
| | INT4 | builtin | Default path, offline everywhere |
| GGUF | Q4_K_M | Ollama / llama.cpp | 低配友好，CPU 可跑 |
| | Q4_K_M | Ollama / llama.cpp | Low-end friendly, runs on CPU |
| AWQ / GPTQ | INT4 | vLLM | 需 NVIDIA GPU |
| | INT4 | vLLM | Requires an NVIDIA GPU |

**选型 / Selection**：低配首选 `builtin + L0`，其次 `ollama + L0/L1`；有 NVIDIA GPU 且需要并发选 `vllm`。
> *Low-end: `builtin + L0` first, then `ollama + L0/L1`. NVIDIA GPU plus concurrency: `vllm`.*

低配友好细节 / Low-end details：
- 全 INT4 量化，CPU 可跑；
- *INT4 throughout, CPU-capable;*
- L0 纯 CPU 首次响应 < 3 秒；
- *L0 answers in under 3 seconds on CPU;*
- 推理线程 = `min(CPU 核数 - 1, 4)`；
- *inference threads = `min(cores - 1, 4)`;*
- 低内存模式：上下文 ≤ 1024，KV cache 复用，流式首 token 优先；
- *low-memory mode: context ≤ 1024, KV-cache reuse, first token first;*
- 移动端退出即释放；桌面空闲 5 分钟自动卸载。
- *mobile releases on exit; desktop unloads after 5 idle minutes.*

---

## SHA256 校验 / Verification

`models/checksums.json` 随 App 内置，**下载完成必校验**。
> *`models/checksums.json` ships with the app and is enforced after every download.*

```json
{
  "L1": {
    "model.onnx": "9f2c…（完整 64 位十六进制）",
    "tokenizer.json": "a1b3…"
  }
}
```

- 清单不匹配 → 删除该源产物，切下一源；
  *mismatch → the partial file is deleted and the next source is tried;*
- HF 仓库有 `manifest.json` 时优先读它，没有则退回内置清单；
  *an HF repo `manifest.json` wins; otherwise the bundled manifest is used;*
- 上传时把该档条目同步写入仓库的 `SHA256SUMS`。
  *on upload the tier's entries are written into the repo's `SHA256SUMS`.*

---

## 各档实测性能 / Measured performance

以下为本机实测（Windows 10 / i7-10750H 6 核 12 线程 / 32 GB / Quadro T1000 4 GB）。
> *Measured on this machine (Windows 10, i7-10750H 6C/12T, 32 GB, Quadro T1000 4 GB).*

| 档位 | 后端 / Backend | 延迟 / Latency | 内存 / Memory | 备注 / Notes |
|---|---|---|---|---|
| L0 | builtin CPU | < 3 s | ~1.2 GB | 达标 / meets target |
| L1 | builtin CPU | 4–8 s | ~2.8 GB | 可接受 / acceptable |
| L1 | ollama CPU | 3–6 s | ~2.8 GB | 与 builtin 相当 / comparable |
| L2 | builtin CPU | 10–20 s | ~5 GB | 慢但可用 / slow but usable |
| L3 | builtin CPU | 30 s+ | ~10 GB | warning：建议 GPU / warning: GPU advised |
| L3 | vllm CUDA | 2–4 s | 显存 ~4.5 GB | 需 ≥ 8 GB 显存才 ready |
| | | | VRAM ~4.5 GB | ready only with ≥ 8 GB VRAM |

> 上表为参考区间，实际取决于 CPU、量化与上下文长度。
> *Figures are indicative; real numbers depend on CPU, quantisation and context length.*

---

## 自定义模型替换 / Custom models

把 GGUF 或 ONNX 放进 `models/custom/`，App 会按文件大小推算峰值内存并按三态规则判定。
> *Drop a GGUF or ONNX into `models/custom/`; the app estimates peak memory from the file size and applies the tri-state rules.*

估算公式 / Estimate: `peak ≈ 文件大小 × 1.6 + 1 GB`（INT4 权重约 0.5 字节/参数，另加 KV cache 与激活）。
> *`peak ≈ file_size × 1.6 + 1 GB` (INT4 weights ≈ 0.5 B/param, plus KV cache and activations).*

支持的操作 / Supported:
- 一键导入 Ollama（生成 Modelfile，`PARAMETER temperature 0.6`、`num_ctx 4096`）；
  *one-click import into Ollama (generates a Modelfile);*
- 一键导入 llama.cpp（生成 `llama-server` 启动参数）；
  *one-click import into llama.cpp (generates `llama-server` arguments);*
- 直接用于 HF 上传。
  *usable directly for HF upload.*

判定失败 → 禁用并提示，不静默加载。
> *A failed check disables the tier with a message; nothing loads silently.*
