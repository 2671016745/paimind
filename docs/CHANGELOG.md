# 变更日志 / Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
> *This project follows [Semantic Versioning](https://semver.org/).*

---

## [0.0.1] - 2026-11-05

### 修复 / Fixed

- **PWA 弹窗遮罩常驻 / PWA modal scrim was always visible**：`.modal { display:flex }` 覆盖了 `hidden` 属性，60% 黑色遮罩一直盖在页面上，整个界面被压暗一层（实测平均亮度 13.9/255）。补 `[hidden]{display:none!important}` 修复。
  *`.modal { display:flex }` overrode the `hidden` attribute, leaving a 60% black scrim over the whole page (measured average brightness 13.9/255). Fixed with `[hidden]{display:none!important}`.*
- **顶栏/底栏背景写死深色 / Topbar and tabbar backgrounds were hard-coded**：`rgba(20,22,27,.94)` 不随主题变化，浅色模式下仍是黑条。改为 `var(--bar)`。
  *They did not follow the theme, so they stayed black in light mode. Now driven by `var(--bar)`.*

### 新增 / Added

- **PWA 浅色主题 / PWA light theme**：底栏「浅色 / 深色」切换，选择持久化到 `localStorage['paimind.theme']`，并在首次绘制前应用以避免闪屏。
  *A light/dark toggle in the tab bar, persisted in `localStorage['paimind.theme']` and applied before first paint to avoid a flash.*
- **桌面端自选深浅色 / Desktop light-dark switch**：状态栏右上角「浅色 / 深色」按钮，选择存 `QSettings('paimind','paimind')` 的 `ui/theme`；可用 `python -m desktop --theme light` 覆盖。颜色全部收敛到 `THEMES` 调色板，`apply_theme()` 重新绑定常量，切换时重建窗口（控件样式在构造时就固化，仅换常量不会生效）。
  *A light/dark button in the status bar, persisted in `QSettings('paimind','paimind').ui/theme` and overridable with `--theme`. All colours live in a `THEMES` palette; `apply_theme()` rebinds the constants and the window is rebuilt, because widget styles are frozen at construction.*

### 修复 / Fixed（主题化时发现 / found while theming）

- **桌面端白字压在浅色卡片上 / White text on a light card**：结果标题用字面量 `color:#FFFFFF`，浅色主题下不可见。改为 `TEXT`。
  *The result headline used a literal `#FFFFFF`, invisible on a light card. Now `TEXT`.*
- **桌面端边框与徽章底色写死 / Hard-coded borders and badge colours**：`#2A2E37`、`#06281F`、`#0B0D10` 散落在样式中，浅色主题下割裂。收敛为 `LINE` / `ON_ACCENT`。
  *`#2A2E37`, `#06281F` and `#0B0D10` were sprinkled through the stylesheets and broke under a light theme. Now `LINE` / `ON_ACCENT`.*

---

## [0.0.1] - 2026-11-04

首个版本。功能已完整跑通，但尚未经过真实用户与多机验证，因此按 0.x 发布。
>*First release. Everything runs end to end, but it has not been validated by real users or on many machines — hence 0.x.*

### 新增验证 / Newly verified

- **档位「已下载」检测只认 ONNX / Tier "downloaded" detection only looked at ONNX**：`is_downloaded()` 与 `tier_size_bytes()` 只扫 `models/L{x}/*.onnx`，而本机模型实际由外部引擎提供（Ollama / GGUF / HF 目录）。结果是上传向导第一步永远是空的、磁盘占用恒为 0。现按各引擎 `model_map` 精确匹配。
  *Both helpers only scanned `models/L{x}/*.onnx`, but the tiers are actually served by external engines (Ollama / GGUF / HF). The upload wizard therefore started empty and always reported 0 bytes. Now matched precisely against each engine's `model_map`.*
- **llama.cpp 的 model_map 文件名与实际 GGUF 不一致 / llama.cpp model_map did not match the real GGUF filename**：配置写 `qwen2.5-1.5b-instruct-q4_k_m.gguf`，磁盘上是 `qwen2.5-1.5b-q4_k_m.gguf`。已对齐 `config.yaml` 与 `config.example.yaml`。
  *The config named a file that does not exist on disk. Both config files aligned.*

### 新增验证 / Newly verified

- **Docker + Ollama 端到端 / Docker + Ollama end-to-end**：容器内 `ollama/ollama` 起服务，App 切 ollama 模式后完成真实决策。
  *Ollama runs in a container; the app switches to ollama mode and completes a real decision.*
- **Flutter APK / Flutter APK**：`flutter analyze` 零问题，产出已签名 `app-release.apk`（72.6 MB）。
  *`flutter analyze` reports no issues; a signed `app-release.apk` (72.6 MB) is produced.*

---

## [0.0.1] - 2026-11-04（续）

### 修复 / Fixed（实测中发现 / found by real-machine testing）

- **PWA 无法配置 API 地址 / PWA had no configurable API address**：原先写死 `window.location.origin`，从任何非 API 同源打开都用不了。现支持 `localStorage` 覆盖，并在「开发者」面板加了输入框。
  *Previously hardcoded `window.location.origin`, so opening the PWA from any other origin could not reach the API. Now overridable via `localStorage` with an input in the developer panel.*
- **CORS 白名单漏了 PWA 端口 / CORS allow-list missed the PWA origin**：`api.cors_origins` 只列了 API 与桌面端口，跨源访问被浏览器拦掉（症状：界面一直「尚未决策」，而服务端日志里根本没有请求）。已在 `config.example.yaml` 补全并加症状对照，附回归测试。
  *The allow-list omitted the PWA origin, so the browser blocked requests (symptom: the UI stays on "not yet decided" with no request in the server log). Documented with a regression test.*
- **网络层失败返回 500 / Transport failures surfaced as HTTP 500**：DNS / TLS / 拒绝连接等传输层异常未被接住，冒泡成 `INTERNAL_ERROR`。现映射为 `503 NETWORK_ERROR`（可重试），并在 `whoami` 与全局兜底中统一处理。
  *DNS, TLS and refused-connection errors escaped as a generic 500. They now map to `503 NETWORK_ERROR` (retryable), both in `whoami` and the last-resort handler.*
- **`online_used` 语义错误 / `online_used` meant the wrong thing**：搜索失败时也置 True，等于声称答案来自网络。现仅当联网结果真正进入答案时才为 True。
  *It was set when the online branch was entered, even if the search failed. It now means "online evidence actually reached the answer".*
- **Docker 构建随机失败 / Flaky Docker builds**：镜像直连官方 PyPI，在不稳定网络下会在不同依赖包上随机失败。现支持 `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_RETRIES` / `PIP_TIMEOUT` 构建参数。
  *The image hit the official PyPI directly and failed on random packages where that connection is unreliable. Build args now allow a mirror, retries and a longer timeout.*
- **桌面设置页启动时空白 / Desktop settings tab was empty on startup**：`MainWindow` 初始化时没有调用 `settings.refresh()`，档位卡片一张都不渲染，必须手动点「重新检测硬件」才出现。
  *`MainWindow` never called `settings.refresh()` on startup, so no tier cards rendered until the user clicked "重新检测硬件".*

### 新增 / Added

- **核心决策 / Core decision**：问题 + 动态选项（≥2，回车添加），输出仅「选中项 + ≤20 字理由 + 置信度 + 来源徽标」；支持重新决策、清空、强制联网；空问题或选项 < 2 时按钮置灰并给行内提示。
- **智能联网求助 / Online fallback**：置信度 < 0.6、含时效性关键词或手动强制时触发；RAG 两级流程（改写 query → top3 标题+摘要 → 带依据再决策）；SerpAPI / Bing / Brave / DuckDuckGo 四级降级；5 秒超时静默回退本地；相同 query 缓存 24 小时；首次联网前隐私确认 + 「仅联网、不上传选项」开关（默认开）。
- **档位与三态 / Tiers and tri-state**：L0–L3 四档；ready / warning / disabled；disabled 卡片置灰 + 锁图标，点击只弹「为什么不可用」；使用中高亮。
- **五种部署模式 / Five deploy modes**：builtin（ONX Runtime）/ ollama / vllm / llamacpp / docker，每档可独立切换，切换不影响已下载文件。
- **硬件检测 / Hardware detection**：可用内存、总内存、CPU 架构与核数、AVX2、磁盘剩余、GPU 型号与显存；Apple Silicon 保守系数 0.85；外部引擎 2 秒超时探测。
- **自动推荐 / Auto recommendation**：在 ready/warning 中选参数最大一档；只有 warning 时提示风险；首装推荐「推荐档与 L0 较小者」。
- **动态刷新 / Live refresh**：设置页自动重新检测；「重新检测硬件」；使用中档位变 disabled 时顶部橙条 + 一键切换；切换/下载/加载前再次校验。
- **自定义模型导入 / Custom import**：GGUF / ONNX 按文件大小推算峰值内存并判定；一键导入 Ollama / llama.cpp。
- **下载源管理 / Download sources**：GitHub + HF 双主源；provider + mirror 两层；内置 ghproxy / ghfast / hf-mirror；自定义源增删改测；fallback_order 多源降级；SHA256 校验；Range 断点续传；全部失败给复制直链与手动放置说明。
- **HF 上传闭环 / HF upload loop**：Write Token 登录校验；仓库列出与创建；上传清单预览（含可跳过数）；同 SHA256 自动跳过；进度与取消；上传后一键设为 HF 下载源。
- **HTTP API / HTTP API**：FastAPI + Uvicorn，默认 `127.0.0.1:8686`；OpenAPI 3.1 挂 `/docs` 与 `/redoc`；CORS 白名单；可选 Bearer 鉴权；60 req/min/IP 限流；SSE 流式端点。
- **桌面版 / Desktop**：PySide6，900×600 起，Win / macOS / Linux；左输入右结果；顶部状态栏；设置页含档位卡片、下载源、HF、推理后端管理区。
- **手机版 / Mobile**：Flutter 原生版 + 响应式 PWA（零安装，手机浏览器可用）；外部引擎模式默认隐藏，开发者选项可填局域网 Ollama 地址。
- **macOS 支持 / macOS**：Apple Silicon 与 Intel 双架构；Metal → CoreML → CPU 后端优先级；Apple Silicon 跳过 AVX2 检查，Intel Mac 仍检测；Keychain 存 token；打包与公证说明。
- **文档 / Docs**：架构、API、模型、档位、下载、上传、GPU、联网、部署、用户手册、开发指南共 13 篇，双语。

### 修复 / Fixed

- 档位判定在「已下载」时不再校验磁盘下限；
  *the disk floor is skipped once a tier is downloaded;*
- 联网失败静默回退本地，不弹错误框；
  *online failures degrade silently;*
- 外部引擎不可达返回 503 `ENGINE_UNAVAILABLE`，不返回 500 或崩溃；
  *unreachable engines return 503, never a 500;*
- 指定 disabled 档位返回 422 并附硬件快照；
  *a disabled tier returns 422 with a hardware snapshot;*
- **Python 不信任系统根证书 / Python ignored the OS trust store**：Watt Toolkit 等本地代理会往系统证书库装自己的根证书，Python 的 `ssl` 默认不读，导致 `huggingface.co` 直连报 `CERTIFICATE_VERIFY_FAILED`（而镜像站正常，看起来像「官方站挂了」）。启动时注入 `truststore` 解决。
  *Local proxies install their own root CA into the system store, which Python's `ssl` does not read — so `huggingface.co` failed with `CERTIFICATE_VERIFY_FAILED` while the mirror worked, looking like the official site was down. Fixed by injecting `truststore` at startup.*
- **App 无法复用 hf CLI 的登录 / The app could not reuse the `hf` CLI login**：现在可以从 `$HF_HOME/stored_tokens` 直接导入并校验，用户不必复制粘贴 token。
  *The token stored by `hf auth login` is now imported and validated directly, so the user never has to copy a secret into the app.*
- **Android 构建被插件的 compileSdk 卡住 / Android build blocked by a plugin's compileSdk**：`onnxruntime` 固定 `compileSdkVersion 33`，而其 AndroidX 依赖要求 34+/36+。现统一把所有 library 模块抬到 36。
  *`onnxruntime` pins `compileSdkVersion 33` while its AndroidX dependencies require 34+/36+. Library modules are now lifted to 36.*
- **Windows 上 Kotlin 增量缓存无法关闭 / Kotlin incremental cache could not be closed on Windows**：`Could not close incremental caches ... class-fq-name-to-source.tab`。现禁用增量编译。
  *The incremental cache could not be closed on Windows. Incremental compilation is now disabled.*
- 磁盘检测指向不存在目录时回退到最近的已存在上级目录。
  *disk detection falls back to the nearest existing ancestor.*
