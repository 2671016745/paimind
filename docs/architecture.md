# 架构 / Architecture

## 总览 / Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  前端 / Front-ends                                                    │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ desktop/     │   │ mobile/pwa/  │   │ mobile/flutter/          │  │
│  │ PySide6      │   │ 浏览器 PWA   │   │ 原生 App                 │  │
│  └──────┬───────┘   └──────┬───────┘   └────────────┬─────────────┘  │
└─────────┼──────────────────┼────────────────────────┼────────────────┘
          │ HTTP             │ HTTP                   │ 进程内直调
          │                  │                        │ in-process
          ▼                  ▼                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  server/  FastAPI  (127.0.0.1:8686, /docs, /redoc, /app)             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  core/  三端共用核心 / shared core                                    │
│                                                                      │
│  decision.py ──► prompt.py ──► online.py ──► search.py               │
│       │                                                           │
│       ├──► tiers.py ◄── hardware.py                                 │
│       │        ▲                                                  │
│       │        └── engines.py                                      │
│       │                                                           │
│       └──► inference/  ──► builtin.py  (ONNX Runtime)              │
│                        ──► openai_client.py (Ollama/vLLM/llama.cpp)│
│                                                                      │
│  download.py ──► model_files.py ──► hf.py ──► keys.py               │
│  config.py · errors.py · schemas.py · usage.py · runtime.py          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 模块职责 / Module responsibilities

| 模块 / Module | 职责 / Responsibility |
|---|---|
| `core/config.py` | 加载 `config.yaml`，补齐默认值，URL 模板变量与校验 |
| | Loads `config.yaml`, fills defaults, template variables and validation |
| `core/errors.py` | 错误码枚举、HTTP 状态映射、`AppError` |
| | Error code enum, HTTP status map, `AppError` |
| `core/schemas.py` | Pydantic v2 契约，三端共用 |
| | Pydantic v2 contract shared by all three shells |
| `core/hardware.py` | 内存 / CPU / AVX2 / 磁盘 / GPU 检测，全平台 |
| | Memory / CPU / AVX2 / disk / GPU detection on every platform |
| `core/tiers.py` | 档位定义、三态判定、自动推荐 |
| | Tier definitions, tri-state rules, recommendation |
| `core/engines.py` | Ollama / vLLM / llama-server / Docker 并行探测 |
| | Parallel probing of Ollama / vLLM / llama-server / Docker |
| `core/inference/` | 后端抽象层 + 五个实现 |
| | Backend abstraction plus five implementations |
| `core/prompt.py` | prompt 构造与模型输出解析 |
| | Prompt construction and model-output parsing |
| `core/decision.py` | 决策流水线（本地 → 联网 → 结果） |
| | The pipeline (local → online → result) |
| `core/online.py` | 触发规则、24 小时缓存、隐私开关 |
| | Trigger rules, 24h cache, privacy switch |
| `core/search.py` | SerpAPI / Bing / Brave / DuckDuckGo |
| | SerpAPI / Bing / Brave / DuckDuckGo |
| `core/download.py` | provider+mirror 模型、多源降级、断点续传、SHA256 |
| | provider+mirror model, fallback, resume, SHA256 |
| `core/hf.py` | HF 登录、仓库、上传、设为下载源 |
| | HF login, repos, upload, set-as-source |
| `core/model_files.py` | 磁盘清单、校验和、自定义导入 |
| | On-disk inventory, checksums, custom import |
| `core/keys.py` | 系统密钥库 + 文件降级 |
| | System keyring with a file fallback |
| `core/usage.py` | 本地用量统计 |
| | Local usage accounting |
| `core/runtime.py` | 当前档位与部署模式持久化 |
| | Persists the current tier and deploy mode |
| `core/context.py` | 共享协作者（配置、硬件缓存、引擎状态、后端缓存） |
| | Shared collaborators (config, hw cache, engine state, backend cache) |

---

## 数据流 / Data flow

```
用户输入 question + options
        │
        ▼
_resolve_tier()      auto → 运行时档位；disabled → 推荐档位
        │
        ▼
_validate()          三态判定；disabled→422；未下载→409；warning→允许执行
        │
        ▼
resolve_backend()    降级链：指定模式 → 回退 builtin → 报错
        │
        ▼
build_decision_prompt()   本地小模型推理
        │
        ▼
needs_online()?      置信度 < 阈值 或 含时效词 或 强制
        │ 是
        ▼
rewrite query → search top3 → 带依据再决策 → source=hybrid
        │
        ▼
DecisionResponse     选中项 + 理由 + 置信度 + 来源 + usage + latency
```

---

## 三端共享边界 / Shared boundary

`core/` **不 import** PySide6、Flutter 或 FastAPI —— 它是纯 Python。
> *`core/` imports neither PySide6, Flutter nor FastAPI — it is pure Python.*

| 端 / Shell | 入口 / Entry | 用到 core 的方式 / How it uses core |
|---|---|---|
| 桌面 / Desktop | `python -m desktop` | 内嵌 API 服务 + HTTP 客户端 |
| | | In-process API server + HTTP client |
| API | `python -m server` | 直接 import `core.*` |
| | | Imports `core.*` directly |
| PWA | `http://<host>:8686/app` | 只走 HTTP API |
| | | HTTP API only |
| Flutter | `flutter run` | 进程内直调核心逻辑（onnxruntime 插件） |
| | | Calls core logic in-process (onnxruntime plugin) |

**为什么这样切分 / Why this split**：决策规则、置信度阈值、联网触发条件、三态判定这些「业务真相」只能有一份。三端各写一份必然漂移。
> *Decision rules, confidence thresholds, online triggers and tri-state rules must exist exactly once; three copies would inevitably drift.*

---

## 关键设计决策 / Key design decisions

### 1. 外部引擎统一走 OpenAI 兼容接口 / One OpenAI-compatible contract

Ollama、vLLM、llama-server 都有原生 API，但三者也都提供 OpenAI 兼容的 `/v1/chat/completions`。统一走它意味着：
> *Ollama, vLLM and llama-server each have a native API, but all three also expose an OpenAI-compatible `/v1/chat/completions`. Standardising on it means:*

- 一个 HTTP 客户端、一种请求体、一套错误映射，覆盖全部外部引擎；
  *one HTTP client, one request shape and one error map cover every external engine;*
- 任何第三方 OpenAI 兼容服务（云网关、LM Studio、llama.cpp 分支）零改动接入；
  *any third-party OpenAI-compatible service plugs in with zero changes;*
- App 层不暴露引擎特有参数（例如 vLLM 的 `max-model-len`），契约保持稳定。
  *engine-specific knobs (e.g. vLLM's `max-model-len`) never leak into the App layer.*

### 2. 内存一律用「可用内存」而非「总内存」/ Available memory, never total

「这台机器有 16 GB」在已用掉 14 GB 时毫无意义。所有档位阈值都按**可用内存**判定：
> *"This machine has 16 GB" means nothing when 14 GB is already committed. Every tier threshold is evaluated against* available *memory:*

| 平台 / Platform | 接口 / API |
|---|---|
| Windows | `GlobalMemoryStatusEx.ullAvailPhys` |
| macOS | `vm_stat` free + inactive + purgeable |
| Linux | `/proc/meminfo` MemAvailable |
| Android | `ActivityManager.MemoryInfo.availMem` |
| iOS | `os_proc_available_memory()` |

### 3. 档位判定按部署模式分支 / Tier rules branch by deploy mode

同一档位在 builtin 与 vllm 下的可用性完全不同 —— vLLM 没有 NVIDIA GPU 就是 disabled。判定必须知道「用哪个引擎」，所以三态是 `(tier, deploy_mode) -> state` 的函数。
> *A tier's viability differs completely between builtin and vLLM — vLLM without an NVIDIA GPU is disabled. Judgement must know the engine, so tri-state is a function of `(tier, deploy_mode)`.*

### 4. 不静默失败 / Never fail silently

- 模型缺失 → 明确错误 + 下载入口（含源选择与部署模式选择）；
  *missing model → explicit error plus a download entry point;*
- 联网失败 → 静默回退本地 + 状态栏提示，不弹错误框；
  *online failure → silent local fallback plus a status-bar note, no error dialog;*
- 外部引擎不可达 → 503 `ENGINE_UNAVAILABLE`，绝不返回 500 或崩溃。
  *unreachable engine → 503 `ENGINE_UNAVAILABLE`, never a 500 or a crash.*

### 5. 密钥只存引用名 / Secrets are referenced by name only

`config.yaml` 里只有 `hf_token` 这样的**引用名**，真值在系统密钥库（macOS Keychain / Windows Credential Manager / Linux Secret Service / iOS Keychain / Android Keystore）。配置和日志里永不出现密钥。
> *`config.yaml` holds only a* reference *like `hf_token`; the real value lives in the system keyring. Secrets never appear in config or logs.*

---

## 推理后端抽象层 / Inference backend abstraction

```python
class InferenceBackend(abc.ABC):
    name: str
    engine_key: str
    def ready(self) -> bool: ...
    def kind(self) -> BackendKind: ...        # 实际生效的后端，可能是 cpu
    def complete(self, messages, max_tokens, temperature, stop) -> Completion: ...
    def stream(self, messages, ...) -> Iterator[str]: ...
    def close(self) -> None: ...
```

| 实现 / Implementation | 引擎 / Engine | 说明 / Notes |
|---|---|---|
| `BuiltinOnnxBackend` | ONNX Runtime | 默认路径，进程内推理 |
| | | Default path, in-process |
| `OllamaBackend` | Ollama | OpenAI 兼容 `/v1` |
| `VllmBackend` | vLLM | OpenAI 兼容 `/v1` |
| `LlamaCppBackend` | llama-server | OpenAI 兼容 `/v1` |
| `OpenAIClientBackend` | 任意兼容服务 | 通用基类 / generic base |

**新增一个后端只需要一个文件**，决策逻辑零改动。见 [docs/development.md](development.md#新增推理后端)。
> *Adding a backend is one file; the decision logic never changes. See [docs/development.md](development.md).*
