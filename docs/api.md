# API 参考 / API reference

服务默认监听 `127.0.0.1:8686`，OpenAPI 3.1 文档在 `/docs` 与 `/redoc`。
> *The server listens on `127.0.0.1:8686` by default; OpenAPI 3.1 lives at `/docs` and `/redoc`.*

启动 / To start:
```bash
python -m server                 # 或 / or uvicorn server.app:create_app --factory
python -m server --host 0.0.0.0 --port 8686
```

---

## 端点清单 / Endpoint list

| 方法 / Method | 路径 / Path | 说明 / Description |
|---|---|---|
| GET | `/health` | 存活 + 引擎摘要 / liveness + engine summary |
| GET | `/v1/hardware` | 硬件检测 + builtin provider 状态 / hardware + builtin provider status |
| GET | `/v1/hardware/tiers?mode=` | 某部署模式下各档三态 / tri-state per mode |
| GET | `/v1/models` | 全部档位：status / unavailable_reason / downloaded / requirements |
| GET | `/v1/models/{tier}` | 单档详情 / single tier detail |
| GET | `/v1/models/{tier}/why-unavailable` | 「为什么不可用」说明 / explainer |
| POST | `/v1/models/{tier}/download` | 下载（后台任务）/ download (background task) |
| GET | `/v1/downloads/{task_id}` | 下载进度 / download progress |
| POST | `/v1/downloads/{task_id}/cancel` | 取消下载 / cancel download |
| PUT | `/v1/models/{tier}/deploy-mode` | 切换部署模式 / switch deploy mode |
| POST | `/v1/models/{tier}/activate` | 设为当前档位 / activate a tier |
| GET | `/v1/download-sources` | provider + mirror 列表 / provider + mirror list |
| PUT | `/v1/download-sources` | 更新镜像与默认源 / update mirrors and defaults |
| POST | `/v1/download-sources/{provider}/{mirror_id}/test` | 测试单个镜像（HEAD）/ test one mirror (HEAD) |
| POST | `/v1/download-sources/restore-defaults` | 恢复默认 / restore defaults |
| POST | `/v1/decide` | 决策（一次性返回）/ decide (single response) |
| POST | `/v1/decide/stream` | 决策（SSE 流式）/ decide (SSE) |
| GET/POST | `/v1/decide/online-consent` | 联网隐私确认 / online privacy consent |
| GET | `/v1/engines` | 各引擎状态 / engine states |
| POST | `/v1/engines/{engine}/test` | 单独探测某引擎 / probe one engine |
| GET | `/v1/config` · PUT | 读写配置 / read / update config |
| GET | `/v1/usage` | 本地用量统计 / local usage |
| GET | `/v1/hf/auth/status` | HF 登录状态 / HF login status |
| POST | `/v1/hf/auth/login` · logout | 登录 / 登出 / login / logout |
| GET/POST | `/v1/hf/repos` | 列出 / 创建仓库 / list / create repos |
| POST | `/v1/hf/repos/set-download-source` | 把仓库设为下载源 / set repo as download source |
| GET | `/v1/upload/candidates` | 可上传内容 / uploadable candidates |
| POST | `/v1/upload/tasks/preview` | 上传预览 / upload preview |
| POST | `/v1/upload/tasks` | 开始上传 / start upload |
| GET | `/v1/upload/tasks/{task_id}` | 上传状态 / upload status |
| POST | `/v1/upload/tasks/{task_id}/cancel` | 取消上传 / cancel upload |

---

## 请求 / `POST /v1/decide`

| 字段 / Field | 必填 / Required | 说明 / Notes |
|---|---|---|
| `question` | ✔ | 1–2000 字符 / characters |
| `options` | ✔ | 2–20 项，每项 `{id,text}`，`text` ≤ 200 / 2–20 items |
| `context` | | 可选 ≤ 2000 / optional, ≤ 2000 |
| `mode` | | `auto` \| `local` \| `online`，默认 `auto` |
| | | `auto` \| `local` \| `online`, default `auto` |
| `model_tier` | | `auto` \| `L0`–`L3`，默认 `auto` |
| `language` | | `zh`（默认）\| `en` / `zh` (default) \| `en` |
| `stream` | | `false`（用 `/stream` 时置 true）/ use the `/stream` endpoint |
| `confidence_threshold` | | 默认 `0.6` / default `0.6` |
| `max_reason_length` | | 默认 `20` / default `20` |
| `allow_online` | | 默认 `true` / default `true` |
| `upload_options` | | 「仅联网、不上传选项」开关 / "online only, no options" switch |
| `request_id` | | 可选 / optional |

```json
{
  "question": "周末两天，预算 500，两个人出行，去哪里？",
  "options": [
    {"id": "a", "text": "近郊露营"},
    {"id": "b", "text": "周边古镇一日游"}
  ],
  "mode": "auto",
  "model_tier": "auto"
}
```

---

## 响应 / Response

```json
{
  "id": "dec-796500629fc846b5",
  "object": "decision",
  "created": 1793542727,
  "request_id": null,
  "model": "ollama:L1",
  "tier": "L1",
  "backend": "ollama",
  "source": "hybrid",
  "selected": {"id": "a", "text": "近郊露营", "confidence": 0.91},
  "reason": "路程近、花费低，符合预算",
  "alternatives": [
    {"id": "a", "text": "近郊露营", "score": 0.91},
    {"id": "b", "text": "周边古镇一日游", "score": 0.09}
  ],
  "sources": [
    {"title": "周末近郊游推荐", "url": "https://example.com/x", "domain": "example.com"}
  ],
  "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
  "latency_ms": 2034
}
```

`backend` 取值 / values：`gpu:cuda` | `gpu:metal` | `gpu:coreml` | `cpu` | `ollama` | `vllm` | `llamacpp`
> *`backend` is the* actual *runtime, which may differ from the requested mode.*

`source` 取值 / values：`local` | `online` | `hybrid`

---

## 流式 / SSE

`POST /v1/decide/stream` 返回 `text/event-stream`，事件顺序 / events in order：

```
event: meta     data: {"id":..., "model":"ollama:L1", "backend":"ollama", "source":"local", "notes":[...]}
event: token    data: {"delta":"..."}
event: token    data: {"delta":"..."}
event: result   data: {完整响应 JSON / full response JSON}
event: done     data: {"latency_ms":...}
```

**错误在响应开始前抛出**：模型未下载返回 409，档位不可用返回 422，不会出现断掉的流。
> ***Errors are raised before the response starts***: 409 for a missing model, 422 for an unusable tier — never a truncated stream.*

---

## 错误格式 / Error envelope

```json
{
  "error": {
    "code": "MODEL_UNSUPPORTED",
    "message": "档位 L3（vllm 模式）当前不可用：vLLM 需要 NVIDIA GPU，当前未检测到",
    "type": "model_unsupported",
    "param": null,
    "details": {"tier": "L3", "deploy_mode": "vllm", "reason": "...", "hardware": {...}}
  }
}
```

### 错误码 / Error codes

| HTTP | 错误码 / Code | 触发 / When |
|---|---|---|
| 400 | `INVALID_REQUEST` | 请求体不合法 / malformed body |
| 400 | `INVALID_OPTIONS` | 选项少于 2 个 / fewer than 2 options |
| 400 | `INVALID_REPO_NAME` | 仓库名不合法 / invalid repo name |
| 400 | `INVALID_URL_TEMPLATE` | 模板非 https 或缺变量 / non-https template or missing variable |
| 401 | `UNAUTHORIZED` | API token 错误 / bad API token |
| 401 | `HF_UNAUTHORIZED` | HF token 无效 / invalid HF token |
| 403 | `HF_FORBIDDEN` | token 无 write 权限 / token lacks write scope |
| 404 | `MODEL_NOT_FOUND` | 档位未知 / unknown tier |
| 404 | `MIRROR_NOT_FOUND` | 镜像不存在 / mirror missing |
| 404 | `HF_REPO_NOT_FOUND` | 仓库不存在 / repo missing |
| 409 | `MODEL_NOT_READY` | 档位未下载 / tier not downloaded |
| 409 | `ENGINE_MODEL_NOT_READY` | 引擎缺该档模型 / engine lacks the tier's model |
| 413 | `HF_FILE_TOO_LARGE` | 文件超限 / file too large |
| 422 | `MODEL_UNSUPPORTED` | 档位不可用（附硬件快照）/ tier unusable (hardware snapshot attached) |
| 422 | `ENGINE_UNSUPPORTED` | 引擎不支持 / engine unsupported |
| 429 | `RATE_LIMITED` | 限流 / rate limited |
| 500 | `INFERENCE_ERROR` | 推理失败 / inference failed |
| 500 | `UPLOAD_FAILED` | 上传失败（附每文件结果）/ upload failed (per-file results) |
| 502 | `ALL_MIRRORS_FAILED` | 全部镜像失败（附每源结果）/ every mirror failed (per-source results) |
| 502 | `HF_UPSTREAM_ERROR` | HF 服务异常 / HF upstream error |
| 503 | `MODEL_LOADING` | 模型加载中 / model loading |
| 503 | `ENGINE_UNAVAILABLE` | 引擎不可达 / engine unreachable |
| 503 | `NETWORK_ERROR` | DNS/TLS/连接失败等传输层错误（可重试）/ transport failure — retryable |
| 507 | `HF_QUOTA_EXCEEDED` | HF 配额超限 / HF quota exceeded |

---

## 鉴权与限流 / Auth and rate limiting

默认关闭鉴权与 CORS 限制在本地回环。开启后：
> *Auth is off by default and CORS is limited to loopback. When enabled:*

```bash
# 生成 API token 并存入密钥库 / generate a token into the keyring
python -c "from core.keys import set_secret; set_secret('api_token', 'your-token')"
```
```bash
# config.yaml / config.yaml
api:
  enable_auth: true
  rate_limit_per_minute: 60
```
```bash
curl -H "Authorization: Bearer <REDACTED>" http://127.0.0.1:8686/v1/models
```

---

## 示例 / Examples

```bash
curl -s http://127.0.0.1:8686/v1/models | python -m json.tool
curl -s -X POST http://127.0.0.1:8686/v1/decide \
  -H 'Content-Type: application/json' \
  -d '{"question":"今天适合出游吗","options":[{"id":"a","text":"适合"},{"id":"b","text":"不适合"}]}'
curl -N -X POST http://127.0.0.1:8686/v1/decide/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"选哪个","options":[{"id":"a","text":"A"},{"id":"b","text":"B"}]}'
```

更多见 [examples/](../examples/)。
> *More in [examples/](../examples/).*
