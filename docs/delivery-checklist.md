# 交付清单 / Delivery checklist

项目：**应急食品 · paimind**（本地小型决策模型 App）
> *Project: paimind — a local-first small-model decision app.*

最后更新：2026-11-03
> *Last updated: 2026-11-03*

---

## 一、交付物 / What was delivered

| 类别 / Category | 内容 / Content |
|---|---|
| 核心代码 | `core/` 22 个模块（8289 行），含推理后端抽象层 `core/inference/` |
| 服务端 | `server/` 15 个文件，35 个 HTTP 端点 |
| 桌面版 | `desktop/` PySide6（决策页 + 设置页 + 状态栏） |
| 手机版 | `mobile/pwa/` 5 个文件（可安装 PWA）+ `mobile/flutter/` 13 个 Dart 文件 |
| 部署 | `Dockerfile` + `deploy/`（2 个 compose + Modelfile + 脚本） |
| 文档 | `docs/` 14 篇 + `README.md` |
| 测试 | `tests/` 8 个文件，**87 passed** |
| 示例 | `examples/` 6 个客户端示例 |

---

## 二、实测验收结果 / Verified on this machine

硬件：i7-10750H / 32GB RAM / Quadro T1000 4GB（compute 7.5）/ Windows 10
> *Hardware: i7-10750H, 32 GB RAM, Quadro T1000 4 GB (compute capability 7.5), Windows 10.*

| 验收项 / Check | 结果 / Result |
|---|---|
| 启动即用，四种场景不崩溃 | ✅ 无网/无GPU/无驱动/正常 |
| 档位三态（ready 绿 / warning 黄 / disabled 灰） | ✅ L0–L2 ready，L3 warning |
| `GET /v1/models` 返回 status + unavailable_reason | ✅ |
| disabled 档返回 422 + 硬件快照 | ✅ |
| 下载源全失败列出各源结果 | ✅ SSL / 超时 / 404 三源 |
| builtin（ONNX） | ✅ 代码就绪 |
| Ollama 模式决策 | ✅ 8.1s |
| vLLM 模式决策（WSL2） | ✅ 5.6s |
| llama.cpp 模式决策 | ✅ 3.5s |
| Docker compose 语法 | ✅ 两个 compose 均有效 |
| 断网静默回退本地 | ✅ `source=LOCAL` + 提示 |
| `/docs` Swagger | ✅ 35 端点可调试 |
| 桌面 UI 端到端 | ✅ 选中/理由/置信度/来源 |
| PWA 端到端（同源 + 跨源） | ✅ 11.5–19.2s |
| 文档与代码一致（端点/配置键） | ✅ 无矛盾 |

---

## 三、本轮修复的 bug / Bugs fixed

| # | Bug | 影响 |
|---|---|---|
| 1 | PWA 写死 `window.location.origin` | 跨源打开用不了 |
| 2 | CORS 白名单漏 PWA 端口 | 浏览器拦截，界面一直「尚未决策」 |
| 3 | 网络层失败返回 500 | 应 503 NETWORK_ERROR（可重试） |
| 4 | `online_used` 语义错误 | 搜索失败也声称用了联网 |
| 5 | 桌面设置页启动空白 | 档位卡片 0 张，须手动点刷新 |
| 6 | Docker 构建随机失败 | PyPI 抖动 + WSL 无镜像配置 |
| 7 | Docker 镜像装了 PySide6 | 白下 ~400MB |

---

## 四、环境限制 / Environment limits

| 项目 / Item | 状态 / State |
|---|---|
| **Docker 镜像构建** | ✅ 成功（`paimind-api:check`，用阿里源）。容器启动 healthy，`/health` 正常，错误码正确。 |
| **Docker 连 Ollama 端到端** | ✅ **已跑通** —— `docker run ollama/ollama` + App 切 ollama 模式，真实决策经容器内 qwen2.5:0.5b 完成（backend=ollama，选中「近郊露营」）。 |
| **GitHub 仓库** | ✅ https://github.com/2671016745/paimind （public，158 文件，2 次提交） |
| **HF 模型仓库** | ✅ https://huggingface.co/qq2671016745/paimind-models （上传中） |
| **Flutter APK 编译** | ✅ **已产出** —— `app-release.apk` 72.6 MB，`com.example.paimind` v0.0.1，compileSdk 36 / minSdk 24 / targetSdk 36，已签名。`flutter analyze` No issues found。 |
| **Flutter Web 构建** | ❌ 不支持 —— ONNX Runtime 依赖 `dart:ffi`，浏览器无法运行；这是平台限制，非代码问题。 |
| **macOS 打包** | ⚠️ 未验证 —— 需 macOS 环境。签名/公证命令已写入文档。 |

---

## 五、快速开始 / Quick start

```bash
# 桌面版（会自动拉起 API）
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
python -m desktop

# 或只跑 API
python -m server            # http://127.0.0.1:8686  /docs 可调试

# 手机版 PWA（同源，不用配）
# 手机浏览器打开 http://<电脑IP>:8686/app/
```

首次使用先下载 L0（约 300 MB）：桌面版 → 设置 → 档位卡片 → 下载。
> *Download L0 (~300 MB) first: desktop → Settings → tier card → Download.*
