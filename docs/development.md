# 开发者指南 / Development guide

## 目录 / Layout

```
core/          三端共用核心 / shared core
  inference/   推理后端抽象层 / backend abstraction
server/        FastAPI 路由 / FastAPI routes
desktop/       PySide6 / PySide6 UI
mobile/        Flutter + PWA / Flutter + PWA
examples/      调用示例 / client examples
tests/         pytest 套件 / pytest suite
```

## 核心模块接口 / Core interfaces

| 接口 / Interface | 位置 / Location | 说明 / Notes |
|---|---|---|
| `InferenceBackend` | `core/inference/base.py` | 所有引擎的契约 / contract for every engine |
| `AppContext` | `core/context.py` | 共享协作者 / shared collaborators |
| `AppError` | `core/errors.py` | 错误码 + HTTP 状态 / codes + HTTP status |
| `DecideRequest` / `DecisionResponse` | `core/schemas.py` | 请求与响应契约 / request + response contract |
| `evaluate_tier*` | `core/tiers.py` | 三态判定 / tri-state rules |
| `resolve_backend` | `core/inference/__init__.py` | 降级链 / degradation chain |

## 如何新增推理后端 / Adding an inference backend

以接入一个 OpenAI 兼容服务为例，完整步骤：
> *Here is the full walkthrough for adding an OpenAI-compatible service:*

**1. 加一个文件 / add one file** — `core/inference/mybackend.py`：

```python
from typing import Any
from .base import Completion, InferenceBackend
from .openai_client import OpenAIClientBackend


class MyBackend(OpenAIClientBackend):
    name = "myservice"
    engine_key = "myservice"

    def __init__(self, config: dict, model: str, **kwargs: Any) -> None:
        super().__init__(
            base_url=config["openai_base_url"],
            model=model,
            api_key=config.get("api_key") or "EMPTY",
            timeout_seconds=float(config.get("timeout_seconds", 60)),
            label="myservice",
            **kwargs,
        )

    def kind(self):
        from core.schemas import BackendKind
        return BackendKind.CPU
```

**2. 注册 / register** — 在 `core/inference/__init__.py` 的 `engine_backend_class()` 里加一行：

```python
def engine_backend_class(mode: DeployMode):
    return {
        DeployMode.OLLAMA: OllamaBackend,
        DeployMode.VLLM: VllmBackend,
        DeployMode.LLAMACPP: LlamaCppBackend,
        DeployMode.MYSERVICE: MyBackend,      # ← 新增 / new
    }.get(mode)
```

**3. 加配置 / add config** — `config.yaml`：

```yaml
engines:
  myservice:
    enabled: true
    base_url: http://localhost:9000
    openai_base_url: http://localhost:9000/v1
    timeout_seconds: 2
    api_key: null
    model_map:
      L0: my-model-0.5b
      L1: my-model-1.5b
      L2: my-model-3b
      L3: my-model-7b
```

**4. 加探测 / add a probe** — `core/engines.py`：

```python
def probe_myservice(cfg: Config) -> EngineStatus:
    engine_cfg = cfg.engine("myservice")
    ok, _, latency = _probe_http(f"{engine_cfg['base_url']}/health",
                                 float(engine_cfg.get("timeout_seconds", 2)))
    return EngineStatus(engine=EngineKind.MYSERVICE, state=..., ...)
```

**5. 完成 / done** — 决策逻辑零改动，`/v1/engines` 自动出现新引擎。
> *No decision-logic changes; `/v1/engines` picks it up automatically.*

---

## 新增档位 / Adding a tier

1. 在 `config.yaml` 的 `tiers:` 下加一段（`params` / `disk_mb` / `peak_memory_gb` / `min_*`）；
   *add an entry under `tiers:`;*
2. 在 `core/tiers.py` 的 `TIER_ORDER` 里加入档位名；
   *add the name to `TIER_ORDER`;*
3. 在 `core/model_files.py` 的 `TIER_DIR_NAMES` 里映射目录名；
   *map the directory name;*
4. 在 `core/inference/builtin.py` 的 vLLM 阈值表里加显存档位（如适用）。
   *add VRAM thresholds where relevant.*

---

## 调整阈值 / Tuning thresholds

所有阈值集中在 `config.yaml`，不用改代码：
> *Every threshold lives in `config.yaml` — no code changes needed.*

```yaml
tiers:
  apple_silicon_memory_factor: 0.85
  allow_force_load: false
  L1: {min_available_memory_gb: 3, min_disk_mb: 1500, cpu_x86_avx2: true}
decision:
  confidence_threshold: 0.6
  max_reason_length: 20
  online_keywords: [今天, 最新, …]
online:
  timeout_seconds: 5
  cache_ttl_hours: 24
```

---

## 新增搜索 provider / Adding a search provider

**1. 加一个函数 / add a function** — `core/search.py`：

```python
def _myprovider(query: str, key: str, limit: int, timeout: float) -> List[SearchResult]:
    r = httpx.get("https://api.example.com/search",
                  params={"q": query, "limit": limit},
                  headers={"X-Api-Key": key}, timeout=timeout)
    r.raise_for_status()
    return [SearchResult(title=i["title"], url=i["url"],
                         snippet=i["snippet"], domain=domain_of(i["url"]),
                         provider="myprovider")
            for i in r.json()["results"][:limit]]
```

**2. 加入顺序 / add it to the order** — `_provider_order()`：

```python
order = ["serpapi", "myprovider", "bing", "brave", "duckduckgo"]
```

**3. 加配置引用 / add the config reference**：

```yaml
online:
  search:
    myprovider_key_ref: myprovider_key
```

完成 —— 有 key 就用，没 key 自动跳过。
> *Done — used when a key exists, skipped otherwise.*

---

## 新增 DownloadProvider / Adding a download provider

以自定义 HF 兼容服务为例：
> *For example, a custom HF-compatible host:*

**1. 加镜像模板 / add a mirror template**：

```python
{
    "id": "myhost", "name": "我的 HF 兼容站", "provider": "huggingface",
    "url_template": "https://hf.myhost.com/{repo}/resolve/{revision}/{path}",
    "auth_required": False, "enabled": True, "priority": 250, "kind": "builtin",
}
```

**2. 加入 fallback / add it to the fallback order**：

```yaml
download:
  fallback_order:
    huggingface: [official, hf-mirror, myhost]
```

**3. 完成 / done** —— `GET /v1/download-sources` 会列出它，`POST …/test` 可测。
> *It appears in `GET /v1/download-sources` and is testable via `POST …/test`.*

---

## 跑测试 / Running tests

```bash
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe -m pytest tests/ -q
```

覆盖 / Coverage:
- `test_tiers.py` — 三态判定矩阵（含 2 GB 手机、4 GB 机器、无 GPU、无 AVX2）
- `test_api.py` — 端点、状态码、错误封装、SSE
- `test_decision.py` — 决策流水线与降级链
- `test_prompt.py` — prompt 构造与容错解析
- `test_online.py` — 触发规则、缓存、隐私开关
- `test_download.py` — 模板、降级、SHA256

---

## 代码规范 / Conventions

- Python 3.11+，类型注解完整；
  *Python 3.11+, fully type-annotated;*
- `core/` 不 import 任何 UI / Web 框架；
  *`core/` never imports a UI or web framework;*
- 错误一律走 `AppError` + `ErrorCode`，不裸抛；
  *always raise `AppError` with an `ErrorCode`;*
- 密钥只存引用名，日志里用 `redact()`；
  *secrets are reference-only; redact before logging;*
- 文档双语：中文正文 + 小字英文（`> *English*`）。
  *docs are bilingual: Chinese body with a small English line.*
