# 上传 Hugging Face / Upload to Hugging Face

## 设计目标 / Goal

把已下载档位或自定义导入的模型上传到**自己的 HF 仓库**，支持新建、更新、断点续传与进度展示。上传成功后一键设为 HF 下载源，形成「上传 → 分发 → 下载」闭环。
> *Upload a downloaded tier (or an imported model) to your own HF repo, then make it a download source — a full upload → distribute → download loop.*

**全程用官方 HF API，镜像不参与上传。**
> *Only the official HF API is used; mirrors never participate in uploads.*

---

## 前置条件 / Prerequisites

1. HF 账号；
   *an HF account;*
2. **Write Token**：<https://huggingface.co/settings/tokens>，权限选 `write`；
   *a **Write Token** from the settings page, with the `write` role;*
3. 设置页「Hugging Face 登录」输入 token → 调用 `GET /api/whoami-v2` 校验 → 存入系统密钥库。
   *paste the token in Settings; it is validated against `GET /api/whoami-v2` and stored in the keyring.*

大文件自动走 LFS，用官方 `huggingface_hub`（`HfApi.upload_folder` / `upload_file` / `create_repo`），**不需要装 git-lfs**。
> *Large files use LFS via the official `huggingface_hub`; git-lfs is not required.*

---

## 上传内容清单 / What gets uploaded

每档放仓库对应子目录（如 `L1/`）/ one subdirectory per tier (e.g. `L1/`):

| 文件 / File | 说明 / Notes |
|---|---|
| `model.onnx` | 主模型 / main graph |
| `tokenizer.json` | 分词器 / tokenizer |
| `tokenizer_config.json` | 分词配置 / tokenizer config |
| `config.json` | 模型配置 / model config |
| `manifest.json` | 档位元信息 / tier metadata |
| `SHA256SUMS` | 校验和 / checksums |

### `manifest.json` 结构 / structure

```json
{
  "tier": "L1",
  "display_name": "L1 基础档",
  "params": "1.5B",
  "quant": "onnx-int4",
  "disk_size_bytes": 1048576000,
  "peak_memory_bytes": 3006477107,
  "files": [
    {"name": "model.onnx", "sha256": "9f2c…", "size": 1048576000}
  ],
  "min_requirements": {
    "available_memory_gb": 3.0,
    "cpu_x86_avx2": true,
    "gpu_optional": true
  },
  "created_by": "your-username",
  "app_version": "0.0.1",
  "created_at": "2026-11-01T21:00:00+0800"
}
```

---

## 上传流程 / Flow

```
① 选择内容（已下载档位 + 自定义导入，可多选）
② 选择仓库（已有：GET /api/models?author=<me> 下拉；新建：名校验 + 公开/私有 + 勾选 README）
③ 预览清单（文件、总大小、目标路径、远端可跳过数）
④ 开始上传（upload_folder 带 repo_id / path_in_repo / commit_message，总进度 + 单文件进度，可取消）
⑤ 完成后显示仓库 URL，询问「是否设为 HF 默认下载源」
```

### 仓库名规则 / Repo name rules

- 字母、数字、`-` `_` `.`；以字母或数字开头；长度 ≤ 96；
  *letters, digits, `-` `_` `.`; must start alphanumeric; ≤ 96 chars;*
- 校验在客户端做，服务端再校验一次。
  *validated client-side, re-validated server-side.*

---

## 断点续传与跳过 / Resume and skip

- 远端已存在**同 SHA256**的文件 → 跳过上传，只补缺失；
  *files with the same SHA256 already on the remote are skipped;*
- 远端索引来自仓库的 `SHA256SUMS`；
  *the remote index comes from the repo's `SHA256SUMS`;*
- 中断后重跑自动只补缺失文件；
  *rerunning after an interruption only uploads what is missing;*
- 上传中可最小化到状态栏显示圆形进度。
  *the upload can be minimised to a circular progress in the status bar.*

---

## 上传后设为下载源 / Set as download source

成功后自动把 `repo`、`revision=main` 写回 `config.download.providers.huggingface`，并把 `default_provider` 切到 `huggingface`。
> *On success the repo and `revision=main` are written back into `download.providers.huggingface` and `default_provider` becomes `huggingface`.*

本地 `models/checksums.json` **不变**（以本地为准，远端仅作分发）。
> *The local `models/checksums.json` stays unchanged — local is the source of truth, the remote only distributes.*

上传记录写入 `docs/upload-log.json`（不含 token）。
> *Upload events are appended to `docs/upload-log.json` (no token).*

---

## 安全与隐私 / Safety and privacy

- Token 只存密钥库，输入框密码方式展示**不回显**；
  *the token lives in the keyring and the input is masked;*
- 上传前提示「将把模型文件上传到 Hugging Face 公开/私有仓库，请确认无敏感信息」；
  *you are warned to confirm nothing sensitive is included;*
- 自定义导入模型若含用户数据 → 二次确认；
  *custom imports that may contain user data need a second confirmation;*
- 上传日志不记录 token。
  *upload logs never contain the token.*

---

## 失败排查 / Troubleshooting

| 错误 / Error | 处置 / Fix |
|---|---|
| `401 HF_UNAUTHORIZED` | token 无效或过期 → 重新登录 / re-login |
| `403 HF_FORBIDDEN` | token 缺 write 权限 → 在设置页建 Write Token |
| | token lacks write scope → create a Write Token |
| `404 HF_REPO_NOT_FOUND` | 仓库名拼错或 namespace 不对 / wrong name or namespace |
| `413 HF_FILE_TOO_LARGE` | 单文件超限 → 拆分或压缩 / split or compress |
| `429 RATE_LIMITED` | 稍后重试 / retry later |
| `500 UPLOAD_FAILED` | 看 `details.file_results` 的逐文件原因 |
| | inspect `details.file_results` for per-file causes |
| `502 HF_UPSTREAM_ERROR` | HF 服务异常 → 稍后重试 / HF outage, retry |
| `507 HF_QUOTA_EXCEEDED` | 存储配额满 → 清理仓库或升级套餐 |
| | storage quota exceeded → clean up repos or upgrade |

无 Write Token 时上传返回 **403 `HF_FORBIDDEN`**；超配额返回 **507 `HF_QUOTA_EXCEEDED`**。
> *Without a Write Token the upload returns **403 `HF_FORBIDDEN`**; over quota returns **507 `HF_QUOTA_EXCEEDED`**.*

---

## `examples/hf_upload.py` / Usage

```bash
python examples/hf_upload.py --token $HF_TOKEN --tier L1 --repo yourname/paimind-models --create
```

参数 / flags：
- `--tier`：要上传的档位（可多次指定）/ tier(s) to upload;
- `--repo`：目标 `namespace/name`；
- `--create`：仓库不存在时创建；
- `--private`：建成私有仓库；
- `--set-source`：上传后写回配置当作下载源。
  *write the repo back as a download source after uploading.*
