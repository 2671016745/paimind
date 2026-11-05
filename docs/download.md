# 下载源管理 / Download sources

## provider + mirror 两层模型 / Two layers

```
provider（主源 / main source）
  └─ mirror（镜像 / mirror）：一个 URL 模板 + 优先级 + 是否鉴权
```

- **GitHub** 与 **Hugging Face** 是**同等级的两个主源**，下载时可自由选择；
  *GitHub and Hugging Face are two equally-ranked main sources, freely selectable;*
- 每个 provider 都有内置镜像 + 自定义镜像，切换方式一致；
  *every provider has built-in and custom mirrors, switched the same way;*
- 下载失败按 `fallback_order` 依次降级，**绝不因单源挂掉阻塞**。
  *failures degrade through `fallback_order` — one dead source never blocks the download.*

### 内置镜像 / Built-in mirrors

| provider | mirror | URL 模板 / template | 用途 / purpose |
|---|---|---|---|
| github | `official` | `https://github.com/{repo}/releases/download/{tag}/{file}` | 默认主源 / default |
| github | `ghproxy` | `https://ghproxy.com/https://github.com/{repo}/releases/download/{tag}/{file}` | 国内加速 / CN acceleration |
| github | `ghfast` | `https://ghfast.top/https://github.com/{repo}/releases/download/{tag}/{file}` | 国内加速 / CN acceleration |
| huggingface | `official` | `https://huggingface.co/{repo}/resolve/{revision}/{path}` | 默认主源 / default |
| huggingface | `hf-mirror` | `https://hf-mirror.com/{repo}/resolve/{revision}/{path}` | 镜像加速 / mirror |

---

## URL 模板变量 / Template variables

两个 provider 共用同一套变量。
> *Both providers share the same variables.*

| 变量 / Variable | 含义 / Meaning |
|---|---|
| `{repo}` | `owner/name` |
| `{tag}` | GitHub Release tag |
| `{revision}` | HF 分支或 tag / HF branch or tag |
| `{file}` | 文件名 / file name |
| `{path}` | 仓库相对路径（HF 必须）/ repo-relative path (required for HF) |
| `{branch}` | 分支名 / branch name |

模板必须是 **https**；http 会被拒绝保存，除非显式开启 `allow_http_mirrors`。
> *Templates must be* https*; http is refused unless `allow_http_mirrors` is explicitly enabled.*

---

## 配置结构 / Configuration

```yaml
download:
  default_provider: github
  default_mirror_by_provider: {github: official, huggingface: official}
  fallback_order:
    github: [official, ghproxy, ghfast]
    huggingface: [official, hf-mirror]
  timeout_seconds: 30
  retry_per_source: 1
  verify_sha256: true
  resume_support: true
  allow_http_mirrors: false
  providers:
    github: {enabled: true, repo: qq2671016745/paimind-models, tag: v0.0.1}
    huggingface: {enabled: true, repo: qq2671016745/paimind-models, revision: main,
                  private: false, auth_token_ref: hf_token}
  custom_mirrors: []
```

**Token 安全 / Token safety**：`hf_token` 存系统密钥库（macOS Keychain / Windows Credential Manager / Linux Secret Service / iOS Keychain / Android Keystore），**绝不写入配置或日志**，配置只存引用名。
> *`hf_token` lives in the system keyring and never in config or logs; config holds only the reference name.*

---

## 下载流程 / Download flow

```
选择档位 → 选择 provider → 选择 mirror → 显示预计 URL / 文件大小 / SHA256 前 8 位
        ↓
按 fallback_order 依次尝试（手动指定 mirror 失败也自动转入）
        ↓
单源流程：HEAD 校验可达 → Range 断点续传（.part）→ 完成校验 SHA256
        ↓
失败条件：超时 30s / HTTP 4xx-5xx / SHA256 不匹配（删该源产物）→ 切下一源
        ↓
全部失败：错误面板列出每源结果 + 「复制直链」供手动下载
```

进度区展示：当前 provider + mirror、进度、速度、剩余时间、可取消。
> *The progress area shows the current provider and mirror, progress, speed, ETA and a cancel button.*

切换源时显示「源 X 失败，正在尝试源 Y」。
> *Switching sources shows "source X failed, trying source Y".*

---

## HF 下载细节 / Hugging Face details

- `revision` 默认 `main`；有 tag 时下载选择器可切换；
  *`revision` defaults to `main`; a tag selector appears when tags exist;*
- 文件清单优先从仓库 `manifest.json` 读，没有则退回内置 `models/checksums.json`；
  *the file list comes from the repo `manifest.json`, falling back to the bundled manifest;*
- 私有仓库未配置 token 且返回 401 → 提示「该仓库为私有，请在设置中登录 Hugging Face」并提供跳转；
  *a private repo returning 401 without a token prompts you to log in, with a link;*
- 大文件走 LFS 自动生效（`huggingface_hub` 处理）；
  *large files use LFS automatically;*
- `resolve` 会 302 到 CDN，跟随重定向最多 5 跳。
  *`resolve` 302s to the CDN; up to 5 redirects are followed.*

---

## 安全与校验 / Safety

| 规则 / Rule | 说明 / Notes |
|---|---|
| SHA256 必校验 / always verified | 清单随 App 内置，不匹配即删该源产物 |
| 模板必须 https / https only | http 需显式开启 `allow_http_mirrors` |
| 自定义源首次保存弹提示 / trust prompt | 「请确认该镜像为可信来源，模型文件将被执行加载」 |
| token 不发第三方 / token stays home | 只发 `huggingface.co`，除非用户显式开启 `hf_mirror_send_token` |
| 下载记录日志 / download log | provider、mirror、耗时、结果、是否带 token（**不含 token 本身**） |

---

## 全部失败时 / When every source fails

错误面板列出每个源的结果，并提供「复制直链」供手动下载。
> *The error panel lists every source's result and offers a copyable direct link.*

手动放置路径 / manual placement:
```
models/<tier>/<filename>     # 例如 / e.g. models/L1/model.onnx
```

---

## 端点 / Endpoints

| 端点 / Endpoint | 说明 / Description |
|---|---|
| `GET /v1/download-sources` | 返回 GitHub 与 HF 两组 mirror / both groups |
| `PUT /v1/download-sources` | 增删改镜像、改默认源 / add, edit, remove mirrors |
| `POST /v1/download-sources/{provider}/{mirror}/test` | HEAD 测试（5 秒超时）/ HEAD test (5s) |
| `POST /v1/download-sources/restore-defaults` | 恢复默认 / restore defaults |
| `GET /v1/download-sources/template-variables` | 变量说明 / variable reference |
