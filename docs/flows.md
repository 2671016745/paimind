# 流程图 / Flow diagrams

八张文本流程图，与 `core/` 的实际代码路径一一对应。
> *Eight text flow diagrams, each mapping to a real code path in `core/`.*

---

## ① GPU 检测 / GPU detection

```
core/hardware.py :: detect_gpus()
│
├─ Windows ──┬─ nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
│            │     └─ 解析成功 → GpuInfo(vendor=nvidia, vram=…, backend=cuda)
│            └─ 失败 → wmic path win32_VideoController get Name,AdapterRAM,DriverVersion
│                        ├─ 有 NVIDIA 且驱动就绪 → cuda
│                        ├─ AMD/Intel  + 驱动    → directml
│                        └─ 只有集显            → compute_backend=none
│
├─ macOS ──── system_profiler SPDisplaysDataType
│            ├─ Apple Silicon → GpuInfo(vendor=apple, backend=metal)   [跳过 AVX2]
│            └─ Intel Mac     → 解析 Chipset Model / VRAM (Total)
│                              └─ 有独显 → metal ；无独显 → 仅 CPU
│
├─ Linux ────┬─ nvidia-smi  → cuda
│            └─ 失败 → lspci -nn | grep -i 'vga|3d controller'
│                       ├─ NVIDIA → cuda
│                       └─ AMD/Intel → vulkan
│
├─ Android ── Vulkan / ro.hardware.egl        （由客户端注入）
└─ iOS ────── Metal                          （由客户端注入）

每个探测 2 秒超时 + try/except
      │
      └─ 异常 → 返回空列表 → 判定走 CPU 分支（不阻塞启动）

GPU 加速 → 状态栏「GPU 加速 · <设备名>」
无 GPU  → 状态栏「CPU 模式」
有 GPU 无驱动 → 桌面弹引导（移动端/macOS 仅状态栏提示）
```

**判定如何影响档位 / How this feeds the tiers**
```
无 NVIDIA GPU        → vllm 模式全部 disabled（原因「vLLM 需要 NVIDIA GPU，当前未检测到」）
显存 < 6 GB（builtin）→ L3 = warning（不是 disabled）
显存 ≥ 6 GB（builtin）→ L3 = ready
```

---

## ② 本地 → 联网 → 降级 / Local → online → fallback

```
core/decision.py :: decide()
│
├─ ① 本地推理（永远先跑）
│     build_decision_prompt() → backend.complete()
│     parse_decision() → selected / reason / confidence
│
├─ ② 判断是否需要联网  core/online.py :: needs_online()
│     ├─ mode == local              → 不联网，直接返回
│     ├─ mode == online / force     → 必须联网
│     ├─ confidence < threshold     → 联网（默认 0.6）
│     ├─ 命中时效性关键词            → 联网（今天/最新/价格/汇率/…）
│     └─ 都不满足                    → 不联网，直接返回
│
├─ ③ 联网（RAG 两级）
│     ├─ rewrite_query()      问题 → 空格分隔的关键词（模型改写 + 确定性兜底）
│     ├─ cached_search()      先查 24 小时缓存
│     │     └─ 未命中 → core/search.py :: search()
│     │           serpapi → bing → brave → bing-web → mojeek → duckduckgo
│     │           首个成功即返回 top3 标题 + 摘要
│     ├─ 有结果 → build_decision_prompt(sources=…) 再跑一次本地模型（带依据决策）
│     │             source = hybrid
│     └─ 无结果 → source 保持 local
│
└─ ④ 降级（静默，不弹错误框）
      ├─ 改写失败        → 用原问题
      ├─ 搜索超时 5 秒   → 用本地结果
      ├─ 全部源失败       → 用本地结果
      └─ 状态栏提示「联网不可用，已用本地结果」
```

**隐私 / Privacy**
```
upload_options = true（默认）→ 只发问题，选项不上传
upload_options = false      → 问题 + 选项一起发（用户已确认）
```

---

## ③ API 请求生命周期（含推理后端选择分支）

```
客户端 POST /v1/decide
│
├─ ① FastAPI 依赖链
│     rate_limit()   60 req/min/IP（默认）
│     check_auth()   可选 Bearer
│     DecideRequest  校验：question 1–2000、options 2–20、tier auto/L0–L3
│           └─ 校验失败 → 400 INVALID_REQUEST
│
├─ ② _resolve_tier()
│     model_tier == auto → 运行时档位（data/runtime.json）
│            └─ 该档 disabled → 取 recommend_tier() 的推荐档
│     model_tier == L0–L3 → 直接用
│
├─ ③ _validate()  ← 三态判定（含降级）
│     ├─ 请求的模式 disabled
│     │     ├─ 该档 ONNX 已下载 → 回退 builtin，记 note「已回退内置推理」
│     │     └─ 未下载            → 422 MODEL_UNSUPPORTED（附硬件快照）
│     ├─ builtin 且未下载 → 409 MODEL_NOT_READY
│     └─ warning → 允许执行，记 warning_reason
│
├─ ④ resolve_backend()  ← 降级链
│     ① 指定模式引擎可达且模型就绪 → 用该模式
│     ② 不可达 → 回退 builtin（若 ONNX 已下载）+ note
│     ③ builtin 也不可用 → 503 ENGINE_UNAVAILABLE（不 500、不崩溃）
│
├─ ⑤ 推理 → 本地 → （按需）联网 → 结果
│
├─ ⑥ 组装 DecisionResponse
│     selected{id,text,confidence} · reason · alternatives[降序]
│     sources[]（本地为空）· usage · latency_ms
│
└─ ⑦ ctx.usage.record()  写入 data/usage.jsonl
```

**SSE 变体 / SSE variant**：校验必须在返回 StreamingResponse **之前**完成，否则错误只能以断流形式出现。
> *Validation must complete before StreamingResponse is returned, or an error surfaces as a truncated stream.*

---

## ④ 硬件检测 → 档位判定 → UI 呈现（含部署模式分支）

```
core/hardware.py :: detect_hardware()
   ├─ 可用内存（Windows GlobalMemoryStatusEx / macOS vm_stat / Linux MemAvailable）
   ├─ CPU 架构 + 核数 + AVX2（x86）
   ├─ 磁盘剩余（shutil.disk_usage；路径不存在时回退到最近的上级目录）
   └─ GPU 列表
        │
        ▼
core/tiers.py :: evaluate_tier_with_engines(tier, mode, hw, …)
   │
   ├─ mode == builtin
   │     32 位 CPU                     → disabled「需要 64 位 CPU」
   │     x86 且需 AVX2 但 CPU 不支持    → disabled「需要 AVX2」
   │     可用内存 < 下限                → disabled「需要 X GB，当前 Y GB」
   │     未下载且磁盘 < 下限           → disabled「需要 X MB」
   │     L3 无 GPU / 显存 < 6 GB       → warning（不禁用）
   │     其余                          → ready
   │
   ├─ mode == ollama / llamacpp
   │     引擎未启动                    → disabled「Ollama 未启动」
   │     引擎可达但未拉模型            → disabled「未拉取模型 X」
   │     可用内存 < 下限               → disabled
   │     L3 无 GPU / 显存 < 6 GB      → warning
   │     其余                         → ready
   │
   ├─ mode == vllm
   │     无 NVIDIA GPU                → disabled「vLLM 需要 NVIDIA GPU，当前未检测到」
   │     L3 显存 ≥ 8 GB              → ready
   │     L3 显存 4–8 GB              → warning「建议调小 max-model-len」
   │     L3 显存 < 4 GB              → disabled
   │     L0–L2 显存 ≥ 3 GB           → ready
   │
   └─ mode == docker
         容器不健康                    → disabled「Docker 容器未运行或健康检查未通过」
         宿主机再按对应引擎规则判定
        │
        ▼
TierStatus{tier, deploy_mode, state, unavailable_reason, warning_reason, requirements}
        │
        ▼
UI（desktop/ 或 mobile/pwa/）
   ├─ ready    → 绿卡，可下载 / 可切换加载
   ├─ warning  → 黄卡 + 原因 + 二次确认
   └─ disabled → 灰卡 + 🔒，点击只弹「为什么不可用」
                  ├─ 需要什么
                  ├─ 当前是什么
                  └─ 如何解决
```

**推荐算法 / Recommendation**：在 ready ∪ warning 中取参数最大档；首装取 `min(推荐档, L0)`；理由一句话（可用内存 + GPU 名）。
> *Pick the largest tier among ready ∪ warning; on first install use `min(winner, L0)`; one-line reason with available RAM and GPU name.*

---

## ⑤ 下载源选择 → 多源降级 → SHA256 校验 → 失败回退

```
core/download.py :: DownloadManager.download(tier, provider, mirror)
│
├─ ① 选择 provider → 选择 mirror（UI 先确认预计 URL / 文件大小 / SHA256 前 8 位）
│
├─ ② 组 fallback 列表
│     manual mirror 失败也自动并入
│     github:     [official, ghproxy, ghfast]
│     huggingface:[official, hf-mirror]
│
├─ ③ 逐源尝试
│     ├─ HEAD 校验可达（5 秒超时）
│     │     401 → 「该仓库为私有，请在设置中登录 Hugging Face」
│     │     404 → 「该源暂无此文件」
│     ├─ GET 下载（30 秒超时，Range 断点续传，.part 临时文件）
│     │     416 → 删 .part 重新下载
│     ├─ 进度回调 → 进度 / 速度 / 剩余时间 / 可取消
│     └─ 完成 → SHA256 校验
│           匹配 → 重命名 .part → 目标文件 ✅
│           不匹配 → 删该源产物 → 切下一源
│
├─ ④ 全部失败
│     → 502 ALL_MIRRORS_FAILED
│     → details.attempts 列出每源结果
│     → UI 弹错误面板 + 「复制直链」+ 手动放置路径 models/<tier>/
│
└─ ⑤ 日志（不含 token）
      provider · mirror · 耗时 · 结果 · 是否带 token
```

**安全 / Safety**
```
URL 模板必须 https（http 需显式开启 allow_http_mirrors）
自定义镜像首次保存 → 弹「请确认该镜像为可信来源」
HF token 只发 huggingface.co（除显式开启 hf_mirror_send_token）
```

---

## ⑥ HF 登录 → 选择内容 → 创建仓库 → 上传 → 设为下载源

```
core/hf.py
│
├─ ① 登录
│     POST /v1/hf/auth/login {token}
│     → GET https://huggingface.co/api/whoami-v2 校验
│     → 存系统密钥库（core/keys.py）
│     → 回 HFAuthStatus{username, avatar, token_write_scope}
│     → 401 → HF_UNAUTHORIZED ；无 write → 提示
│
├─ ② 选择内容
│     GET /v1/upload/candidates
│     → 已下载档位 + models/custom/ 里的 GGUF/ONNX
│
├─ ③ 选择仓库
│     GET /v1/hf/repos?author=<me>   → 下拉已有仓库
│     POST /v1/hf/repos {name, private, create_readme}
│       仓库名规则：字母数字 - _ . ，≤96，字母或数字开头
│       401 → HF_UNAUTHORIZED ；403 → HF_FORBIDDEN（无 write 权限）
│
├─ ④ 预览
│     POST /v1/upload/tasks/preview
│     → 文件清单 + 总大小 + 远端可跳过数
│     （远端索引来自仓库 SHA256SUMS）
│
├─ ⑤ 上传
│     POST /v1/upload/tasks → 后台任务
│     每档一个子目录：L1/  model.onnx · tokenizer.json · manifest.json · SHA256SUMS
│     远端已存在同 SHA256 → 跳过，只补缺失
│     进度：总进度 + 单文件进度，可取消，可最小化到状态栏
│     失败分类：401 / 403 / 404 / 413 / 429 / 500 / 507
│           403 → HF_FORBIDDEN      413 → HF_FILE_TOO_LARGE
│           507 → HF_QUOTA_EXCEEDED 500 → UPLOAD_FAILED（附每文件结果）
│
└─ ⑥ 设为下载源
      POST /v1/hf/repos/set-download-source {repo}
      → 写回 config.download.providers.huggingface.{repo, revision=main}
      → default_provider = huggingface
      → 记录 docs/upload-log.json（不含 token）
      → 其他设备选 HF provider + 该 mirror 即可下载
```

**闭环 / The loop**：上传 → 分发 → 下载 → 别的设备跑通决策。
> *Upload → distribute → download → another device decides.*

---

## ⑦ 推理后端抽象层与外部引擎数据流

```
                  ┌──────────────────────────────────┐
                  │  core/decision.py                │
                  │  只调用 InferenceBackend 接口      │
                  └───────────────┬──────────────────┘
                                  │ complete() / stream()
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │  core/inference/base.py :: InferenceBackend      │
        │  ready() · kind() · complete() · stream() · close │
        └───────────────────────┬─────────────────────────┘
                                │
      ┌─────────────┬───────────┼───────────┬─────────────────┐
      ▼             ▼           ▼           ▼                 ▼
 BuiltinOnnx   OllamaBackend  VllmBackend  LlamaCppBackend  OpenAIClientBackend
      │             │           │           │                 │
      │             └───────────┴───────────┘                 │
      │                    │                                 │
      │                    ▼                                 │
      │         OpenAIClientBackend（同一个 HTTP 客户端）       │
      │         POST {base}/v1/chat/completions             │
      │         GET  {base}/models   （探测）                 │
      │                    │                                 │
      ▼                    ▼                                 ▼
 onnxruntime        外部引擎进程                          任意兼容服务
 (进程内)            Ollama :11434                        (云网关 / LM Studio)
                     vLLM   :8000
                     llama-server :8080

外部引擎探测（core/engines.py，并行、各 2 秒超时）
  Ollama    GET /api/tags 或 /v1/models  → 200 且目标模型在列表 → ready
           不可达 → 该引擎所有档 disabled，原因「Ollama 未启动」
  vLLM      GET /health 或 /v1/models    → 200 且模型匹配 → ready
           无 NVIDIA GPU → 该模式整体 disabled
  llama.cpp GET /health 或 /v1/models    → 200 → ready
  Docker    docker ps --format json      → 容器 running 且健康 → ready
           未安装 → 模式隐藏
```

**为什么统一走 OpenAI 兼容接口 / Why one contract**
> *One HTTP client, one request shape and one error map cover every external engine; any third-party OpenAI-compatible service plugs in with zero changes. Engine-specific knobs (vLLM's `max-model-len`) never leak into the App layer.*

---

## ⑧ 外部引擎不可达时降级链

```
用户请求 /v1/decide（当前模式 = ollama）
│
├─ ① _validate() 用最新引擎状态判定
│     ├─ ollama 可达且模型就绪 → 正常走 ollama
│     └─ ollama 不可达
│           ├─ 该档 ONNX 已下载 → 判定为「回退 builtin」，记 note
│           └─ 未下载            → 422 MODEL_UNSUPPORTED（附硬件快照）
│
├─ ② resolve_backend() 再确认一次
│     ├─ 指定模式可达 → 用该模式
│     ├─ 不可达 + builtin 已下载 → 用 builtin
│     │     note「Ollama 不可达，已回退内置推理」
│     └─ builtin 也不可用 → 抛错
│
├─ ③ 抛错分支（按优先级）
│     ├─ 引擎未安装            → 422 ENGINE_UNSUPPORTED
│     ├─ 引擎不可达            → 503 ENGINE_UNAVAILABLE
│     ├─ 引擎缺该档模型        → 409 ENGINE_MODEL_NOT_READY
│     └─ builtin 未下载        → 409 MODEL_NOT_READY
│
└─ ④ 兜底原则
      ├─ 绝不返回 500
      ├─ 绝不崩溃
      ├─ 绝不静默假装成功（note 里写明实际生效的后端）
      └─ 响应里 backend 字段如实反映实际后端（可能是 cpu）

引擎在「验证通过之后」才挂掉的情况
  → resolve_backend 抛 BackendUnavailable
  → 503 ENGINE_UNAVAILABLE（不是 500，不是崩溃）
```

**本机实测 / Measured here**
```
ollama 未启动：四档全 disabled，原因「Ollama 未启动」
vllm 无 NVIDIA GPU：全档 disabled，原因「vLLM 需要 NVIDIA GPU，当前未检测到」
ollama 模式 + 引擎挂掉 + ONNX 已下载 → 回退 builtin，note「已回退内置推理」
ollama 模式 + 引擎挂掉 + ONNX 未下载 → 503 ENGINE_UNAVAILABLE
```
