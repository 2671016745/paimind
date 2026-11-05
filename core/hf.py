"""Hugging Face auth, repository listing and model upload.

Upload path
-----------
1. the user pastes a *write* token; it is validated against
   ``GET /api/whoami-v2`` and stored in the system keyring (never in config or
   logs);
2. the user picks tiers (or custom imports) and a repo (existing or new);
3. the app builds the upload set: the model files plus ``manifest.json`` and
   ``SHA256SUMS``;
4. ``huggingface_hub`` uploads with progress callbacks; files whose SHA256
   already exists remotely are skipped;
5. on success the repo can be written back into
   ``download.providers.huggingface`` so other devices can pull it.

Mirrors never participate in uploads — only ``huggingface.co``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from .config import Config, get_config
from .errors import AppError, ErrorCode
from .keys import get_secret, redact, set_secret
from .model_files import (
    custom_models_dir,
    load_checksums,
    scan_custom_models,
    sha256_file,
    tier_dir,
    tier_size_bytes,
)
from .schemas import HFAuthStatus, HFRepo, UploadFileResult, UploadTask

HF_API = "https://huggingface.co/api"
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def _token(cfg: Config) -> Optional[str]:
    ref = cfg.get("security.hf_token_ref", "hf_token")
    return get_secret(ref)


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"User-Agent": "paimind/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def whoami(token: str, timeout: float = 10.0) -> Dict[str, Any]:
    try:
        r = httpx.get(f"{HF_API}/whoami-v2", headers=_headers(token), timeout=timeout)
    except httpx.TimeoutException as exc:
        raise AppError(
            ErrorCode.NETWORK_ERROR,
            f"连接 Hugging Face 超时（{timeout:g}s）",
            {"endpoint": f"{HF_API}/whoami-v2"},
        ) from exc
    except httpx.HTTPError as exc:
        # Transport-level failures (DNS, TLS verification, refused connection)
        # must never surface as a 500 — they are a service-availability problem.
        raise AppError(
            ErrorCode.NETWORK_ERROR,
            f"无法连接 Hugging Face：{type(exc).__name__}",
            {"endpoint": f"{HF_API}/whoami-v2", "detail": str(exc)[:200]},
        ) from exc
    if r.status_code in (401, 403):
        raise AppError(
            ErrorCode.HF_UNAUTHORIZED,
            "Hugging Face token 无效或权限不足",
            {"http_status": r.status_code},
        )
    if r.status_code == 404:
        raise AppError(ErrorCode.HF_REPO_NOT_FOUND, "whoami-v2 端点不可用")
    r.raise_for_status()
    return r.json()


def login(token: str, cfg: Optional[Config] = None) -> HFAuthStatus:
    cfg = cfg or get_config()
    if not token.strip():
        raise AppError(ErrorCode.INVALID_REQUEST, "token 不能为空")
    token = token.strip()
    info = whoami(token)
    set_secret(cfg.get("security.hf_token_ref", "hf_token"), token)

    auth = info.get("auth", {}) or {}
    access = auth.get("accessToken", {}) or {}
    roles = access.get("role", "") if isinstance(access, dict) else ""
    # fine-grained tokens carry scopes instead of a plain role
    scopes = (info.get("auth", {}) or {}).get("accessToken", {}) or {}
    fine = info.get("auth", {}).get("fineGrained", {}) if isinstance(info.get("auth"), dict) else None

    write_scope = _has_write_scope(info)
    return HFAuthStatus(
        logged_in=True,
        username=info.get("name"),
        full_name=info.get("fullname") or info.get("name"),
        avatar_url=(info.get("avatarUrl") or None),
        token_stored=True,
        token_valid=True,
        token_write_scope=write_scope,
        message=None if write_scope else "token 已保存，但可能缺少 write 权限（上传会返回 403）",
    )


def import_from_hf_cli(cfg: Optional[Config] = None) -> HFAuthStatus:
    """Adopt the token the ``hf`` CLI already stored locally.

    The CLI (``hf auth login``) keeps its OAuth token in
    ``$HF_HOME/stored_tokens`` (INI) with a plain ``$HF_HOME/token`` beside it.
    Rather than asking the user to copy a secret into the app, we read it from
    there, validate it against the Hub, and store it in the app's own keyring.
    Nothing is printed or logged.
    """

    import configparser

    cfg = cfg or get_config()
    home = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )

    candidates: List[str] = []
    env_token = os.environ.get("HF_TOKEN")
    if env_token:
        candidates.append(env_token.strip())

    plain = Path(home) / "token"
    if plain.is_file():
        try:
            candidates.append(plain.read_text(encoding="utf-8").strip())
        except OSError:
            pass

    stored = Path(home) / "stored_tokens"
    if stored.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(stored, encoding="utf-8")
        except Exception:
            parser = None
        if parser is not None:
            for section in parser.sections():
                value = parser.get(section, "hf_token", fallback="").strip()
                if value:
                    candidates.append(value)

    for token in candidates:
        if not token:
            continue
        try:
            return login(token, cfg)
        except AppError:
            continue  # 试下一个来源

    raise AppError(
        ErrorCode.INVALID_REQUEST,
        "未在本地找到可用的 hf CLI token；请先运行 `hf auth login`",
        {"searched": str(home)},
    )


def _has_write_scope(info: Dict[str, Any]) -> Optional[bool]:
    auth = info.get("auth") or {}
    if not isinstance(auth, dict):
        return None
    access = auth.get("accessToken") or {}
    if isinstance(access, dict):
        role = str(access.get("role", "")).lower()
        if role:
            return role in ("write", "admin", "fine_grained", "full")
        scopes = access.get("scopes") or []
        if isinstance(scopes, list) and scopes:
            for s in scopes:
                if isinstance(s, dict) and str(s.get("type", "")).lower() in ("write", "create"):
                    return True
            return False
    return None


def status(cfg: Optional[Config] = None) -> HFAuthStatus:
    cfg = cfg or get_config()
    token = _token(cfg)
    if not token:
        return HFAuthStatus(logged_in=False, token_stored=False, token_valid=False,
                            message="未登录 Hugging Face")
    try:
        info = whoami(token, timeout=8.0)
    except AppError as exc:
        return HFAuthStatus(
            logged_in=False, token_stored=True, token_valid=False,
            message=f"token 校验失败：{exc.message}",
        )
    except Exception as exc:
        return HFAuthStatus(
            logged_in=False, token_stored=True, token_valid=False,
            message=f"无法连接 Hugging Face：{type(exc).__name__}",
        )
    return HFAuthStatus(
        logged_in=True,
        username=info.get("name"),
        full_name=info.get("fullname") or info.get("name"),
        avatar_url=(info.get("avatarUrl") or None),
        token_stored=True,
        token_valid=True,
        token_write_scope=_has_write_scope(info),
    )


def logout(cfg: Optional[Config] = None) -> None:
    from .keys import delete_secret

    cfg = cfg or get_config()
    delete_secret(cfg.get("security.hf_token_ref", "hf_token"))


# --------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------


def list_repos(username: str, cfg: Optional[Config] = None, limit: int = 100) -> List[HFRepo]:
    cfg = cfg or get_config()
    if not username:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少用户名")
    r = httpx.get(
        f"{HF_API}/models",
        params={"author": username, "limit": limit, "full": "false"},
        headers=_headers(_token(cfg)),
        timeout=15.0,
    )
    if r.status_code == 401:
        raise AppError(ErrorCode.HF_UNAUTHORIZED, "请先登录 Hugging Face")
    r.raise_for_status()
    out: List[HFRepo] = []
    for item in r.json():
        if not isinstance(item, dict):
            continue
        out.append(HFRepo(
            id=str(item.get("id", "")),
            private=bool(item.get("private", False)),
            downloads=int(item.get("downloads", 0) or 0),
            likes=int(item.get("likes", 0) or 0),
            last_modified=item.get("lastModified"),
        ))
    return out


def validate_repo_name(name: str) -> Tuple[bool, str]:
    if not name:
        return False, "仓库名不能为空"
    if len(name) > 96:
        return False, "仓库名长度不能超过 96"
    if not REPO_NAME_RE.match(name):
        return False, "仓库名只能包含字母、数字、- _ .，且以字母或数字开头"
    return True, ""


def create_repo(
    name: str,
    private: bool = False,
    create_readme: bool = True,
    cfg: Optional[Config] = None,
) -> HFRepo:
    cfg = cfg or get_config()
    ok, reason = validate_repo_name(name)
    if not ok:
        raise AppError(ErrorCode.INVALID_REPO_NAME, reason, {"name": name})
    token = _token(cfg)
    if not token:
        raise AppError(ErrorCode.HF_UNAUTHORIZED, "请先登录 Hugging Face")

    r = httpx.post(
        f"{HF_API}/repos/create",
        headers=_headers(token),
        json={"name": name, "private": private, "type": "model",
              **({"sdk": "ui"} if create_readme else {})},
        timeout=20.0,
    )
    if r.status_code in (401,):
        raise AppError(ErrorCode.HF_UNAUTHORIZED, "Hugging Face 未授权，请重新登录")
    if r.status_code == 403:
        raise AppError(
            ErrorCode.HF_FORBIDDEN,
            "当前 token 没有 write 权限，请在 huggingface.co/settings/tokens 创建 Write Token",
            {"http_status": 403},
        )
    if r.status_code == 404:
        raise AppError(ErrorCode.HF_REPO_NOT_FOUND, "创建仓库失败：用户不存在")
    if r.status_code == 412:
        raise AppError(ErrorCode.INVALID_REPO_NAME, "仓库已存在或名称不合法")
    if r.status_code == 429:
        raise AppError(ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后重试")
    if r.status_code >= 500:
        raise AppError(ErrorCode.HF_UPSTREAM_ERROR, f"Hugging Face 服务异常：HTTP {r.status_code}")
    if r.status_code >= 400:
        raise AppError(ErrorCode.UPLOAD_FAILED, f"创建仓库失败：HTTP {r.status_code}",
                       {"body": r.text[:300]})

    repo_id = f"{_username(token, cfg)}/{name}"
    return HFRepo(id=repo_id, private=private)


def _username(token: str, cfg: Config) -> str:
    try:
        return whoami(token, timeout=8.0).get("name") or ""
    except Exception:
        return ""


def repo_files(repo_id: str, cfg: Optional[Config] = None) -> List[str]:
    cfg = cfg or get_config()
    r = httpx.get(f"{HF_API}/models/{repo_id}/tree/main", headers=_headers(_token(cfg)), timeout=15.0)
    if r.status_code == 401:
        raise AppError(ErrorCode.HF_UNAUTHORIZED, "请先登录 Hugging Face")
    if r.status_code == 404:
        raise AppError(ErrorCode.HF_REPO_NOT_FOUND, f"仓库不存在或无法访问：{repo_id}")
    if r.status_code >= 400:
        raise AppError(ErrorCode.HF_UPSTREAM_ERROR, f"读取仓库失败：HTTP {r.status_code}")
    out: List[str] = []
    for item in r.json():
        if isinstance(item, dict) and item.get("type") == "file":
            out.append(str(item.get("path", "")))
    return out


def remote_sha256_index(repo_id: str, cfg: Optional[Config] = None) -> Dict[str, str]:
    """Parse the remote ``SHA256SUMS`` into ``{path: sha256}`` for skip logic."""

    cfg = cfg or get_config()
    r = httpx.get(
        f"https://huggingface.co/{repo_id}/resolve/main/SHA256SUMS",
        headers=_headers(_token(cfg)), timeout=20.0, follow_redirects=True,
    )
    if r.status_code != 200:
        return {}
    index: Dict[str, str] = {}
    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            digest = parts[0].lower()
            name = parts[-1].lstrip("*").strip()
            index[name] = digest
    return index


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def build_manifest(
    cfg: Config,
    tier_id: str,
    app_version: str,
    created_by: str,
) -> Dict[str, Any]:
    """The ``manifest.json`` payload for a tier (see docs/upload.md §5.3)."""

    from .config import APP_VERSION
    from .hardware import hardware_snapshot
    from .tiers import tier_requirements

    directory = tier_dir(cfg, tier_id)
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            rel = path.relative_to(directory).as_posix()
            files.append({
                "name": rel,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            })
    raw = cfg.tier(tier_id)
    req = tier_requirements(cfg, tier_id)
    return {
        "tier": tier_id,
        "display_name": str(raw.get("display_name", tier_id)),
        "params": str(raw.get("params", "")),
        "quant": "onnx-int4",
        "disk_size_bytes": tier_size_bytes(cfg, tier_id),
        "peak_memory_bytes": int(float(raw.get("peak_memory_gb", 0)) * 1024 ** 3),
        "files": files,
        "min_requirements": {
            "available_memory_gb": req.min_available_memory_gb,
            "cpu_x86_avx2": req.cpu_x86_avx2,
            "gpu_optional": req.gpu_optional,
        },
        "created_by": created_by,
        "app_version": app_version or APP_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def build_sha256sums(cfg: Config, tier_id: str) -> str:
    directory = tier_dir(cfg, tier_id)
    lines = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                rel = path.relative_to(directory).as_posix()
                lines.append(f"{sha256_file(path)}  {rel}")
    return "\n".join(lines) + ("\n" if lines else "")


def collect_upload_files(
    cfg: Config,
    tier_ids: Sequence[str],
    include_custom: bool = False,
) -> List[Tuple[str, Path]]:
    """``[(repo_relative_path, local_path)]`` for the wizard preview."""

    out: List[Tuple[str, Path]] = []
    for tier_id in tier_ids:
        directory = tier_dir(cfg, tier_id)
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name not in ("manifest.json", "SHA256SUMS"):
                out.append((f"{tier_id}/{path.relative_to(directory).as_posix()}", path))
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            tmp = _write_temp_manifest(cfg, tier_id)
            out.append((f"{tier_id}/manifest.json", tmp))
        else:
            out.append((f"{tier_id}/manifest.json", manifest_path))
        sums = directory / "SHA256SUMS"
        if not sums.exists():
            tmp = _write_temp_sums(cfg, tier_id)
            out.append((f"{tier_id}/SHA256SUMS", tmp))
        else:
            out.append((f"{tier_id}/SHA256SUMS", sums))

    if include_custom:
        for entry in scan_custom_models(cfg):
            path = Path(str(entry["path"]))
            out.append((f"custom/{path.name}", path))
    return out


def _write_temp_manifest(cfg: Config, tier_id: str) -> Path:
    from .keys import current_username

    path = Path(cfg.get("app.data_dir", "data")) / f"manifest-{tier_id}.json"
    if not path.parent.is_absolute():
        from .config import project_root
        path = project_root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        build_manifest(cfg, tier_id, "", current_username()), ensure_ascii=False, indent=2
    ), encoding="utf-8")
    return path


def _write_temp_sums(cfg: Config, tier_id: str) -> Path:
    from .config import project_root

    root = Path(cfg.get("app.data_dir", "data"))
    if not root.is_absolute():
        root = project_root() / root
    path = root / f"SHA256SUMS-{tier_id}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_sha256sums(cfg, tier_id), encoding="utf-8")
    return path


class UploadManager:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.tasks: Dict[str, UploadTask] = {}
        self._cancel: Dict[str, bool] = {}
        self._lock = threading.Lock()

    def task(self, task_id: str) -> UploadTask:
        with self._lock:
            task = self.tasks.get(task_id)
        if task is None:
            raise AppError(ErrorCode.MODEL_NOT_FOUND, f"上传任务不存在：{task_id}",
                           {"task_id": task_id})
        return task

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self.tasks:
                return False
            self._cancel[task_id] = True
        return True

    def preview(
        self,
        repo_id: str,
        tier_ids: Sequence[str],
        include_custom: bool = False,
    ) -> Dict[str, Any]:
        files = collect_upload_files(self.cfg, tier_ids, include_custom)
        remote_index = {}
        try:
            remote_index = remote_sha256_index(repo_id, self.cfg)
        except AppError:
            remote_index = {}

        entries = []
        total = 0
        skippable = 0
        for rel, path in files:
            size = path.stat().st_size
            total += size
            digest = sha256_file(path)
            skip = remote_index.get(rel) == digest
            if skip:
                skippable += 1
            entries.append({
                "path": rel, "size": size, "sha256": digest,
                "skip": skip, "reason": "远端已存在相同 SHA256" if skip else "",
            })
        return {
            "repo_id": repo_id,
            "files": entries,
            "total_bytes": total,
            "total_mb": round(total / 1024 ** 2, 1),
            "skippable_count": skippable,
            "to_upload": len(entries) - skippable,
        }

    def start(
        self,
        repo_id: str,
        tier_ids: Sequence[str],
        include_custom: bool = False,
        commit_message: str = "Upload model tier",
        progress: Optional[Callable[[float, int, int], None]] = None,
        task_id: Optional[str] = None,
    ) -> UploadTask:
        cfg = self.cfg
        token = _token(cfg)
        if not token:
            raise AppError(ErrorCode.HF_UNAUTHORIZED, "请先登录 Hugging Face（需要 Write Token）")

        task_id = task_id or f"up-{uuid.uuid4().hex[:12]}"
        files = collect_upload_files(cfg, tier_ids, include_custom)
        if not files:
            raise AppError(ErrorCode.INVALID_REQUEST, "没有可上传的模型文件（档位尚未下载）")

        task = UploadTask(
            task_id=task_id, repo_id=repo_id, state="running",
            total_bytes=sum(p.stat().st_size for _, p in files),
            files=[UploadFileResult(path=rel, size=p.stat().st_size) for rel, p in files],
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        with self._lock:
            self.tasks[task_id] = task
            self._cancel.pop(task_id, None)

        remote_index = {}
        try:
            remote_index = remote_sha256_index(repo_id, cfg)
        except AppError:
            remote_index = {}

        uploaded = 0
        skipped = 0
        try:
            from huggingface_hub import HfApi, upload_file

            api = HfApi(token=token)
            for index, (rel, path) in enumerate(files):
                if self._cancel.get(task_id):
                    self._finish(task, "cancelled", uploaded, skipped, repo_id=None)
                    return task

                digest = sha256_file(path)
                size = path.stat().st_size
                result = task.files[index]

                if remote_index.get(rel) == digest:
                    result.ok = True
                    result.skipped = True
                    result.sha256 = digest
                    skipped += 1
                    uploaded += size
                    self._bump(task, uploaded, progress)
                    continue

                started = time.perf_counter()
                try:
                    upload_file(
                        path_or_fileobj=str(path),
                        path_in_repo=rel,
                        repo_id=repo_id,
                        commit_message=f"{commit_message}: {rel}",
                    )
                    result.ok = True
                    result.sha256 = digest
                    uploaded += size
                except Exception as exc:
                    code = _classify_upload_error(exc)
                    result.ok = False
                    result.error = f"{code.value}: {str(exc)[:200]}"
                    if code in (ErrorCode.HF_FORBIDDEN, ErrorCode.HF_UNAUTHORIZED,
                                ErrorCode.HF_QUOTA_EXCEEDED, ErrorCode.HF_FILE_TOO_LARGE):
                        self._finish(task, "failed", uploaded, skipped, error=result.error)
                        _raise_upload_error(code, result.error)
                result.elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._bump(task, uploaded, progress)

            repo_url = f"https://huggingface.co/{repo_id}"
            self._finish(task, "done", uploaded, skipped, repo_url=repo_url)
            return task
        except AppError:
            raise
        except Exception as exc:
            self._finish(task, "failed", uploaded, skipped, error=str(exc)[:200])
            raise AppError(
                ErrorCode.UPLOAD_FAILED,
                f"上传失败：{type(exc).__name__}: {str(exc)[:200]}",
                {"repo_id": repo_id, "file_results": [f.model_dump() for f in task.files]},
            )

    # -- internals --------------------------------------------------------

    def _bump(self, task: UploadTask, uploaded: int, progress: Optional[Callable]) -> None:
        with self._lock:
            task.uploaded_bytes = uploaded
            task.progress = min(1.0, uploaded / task.total_bytes) if task.total_bytes else 1.0
            task.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if progress:
            progress(task.progress, uploaded, task.total_bytes)

    def _finish(
        self,
        task: UploadTask,
        state: str,
        uploaded: int,
        skipped: int,
        repo_url: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            task.state = state
            task.uploaded_bytes = uploaded
            task.skipped_count = skipped
            task.repo_url = repo_url
            task.error = error
            task.progress = 1.0 if state == "done" else task.progress
            task.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _classify_upload_error(exc: Exception) -> Any:
    text = f"{type(exc).__name__} {exc}".lower()
    if "401" in text or "unauthorized" in text or "invalid credentials" in text:
        return ErrorCode.HF_UNAUTHORIZED
    if "403" in text or "forbidden" in text or "write access" in text:
        return ErrorCode.HF_FORBIDDEN
    if "404" in text or "not found" in text:
        return ErrorCode.HF_REPO_NOT_FOUND
    if "413" in text or "too large" in text or "exceeds" in text:
        return ErrorCode.HF_FILE_TOO_LARGE
    if "quota" in text or "507" in text or "storage limit" in text or "no space" in text:
        return ErrorCode.HF_QUOTA_EXCEEDED
    if "429" in text or "rate" in text:
        return ErrorCode.RATE_LIMITED
    if "500" in text or "502" in text or "503" in text:
        return ErrorCode.HF_UPSTREAM_ERROR
    return ErrorCode.UPLOAD_FAILED


def _raise_upload_error(code: ErrorCode, message: str) -> None:
    raise AppError(code, message, {"code": code.value})


def set_as_download_source(repo_id: str, cfg: Optional[Config] = None) -> Dict[str, Any]:
    """Write the repo back into ``download.providers.huggingface``."""

    cfg = cfg or get_config()
    dl = cfg.section("download")
    providers = dict(dl.get("providers", {}) or {})
    hf = dict(providers.get("huggingface", {}) or {})
    hf["repo"] = repo_id
    hf["revision"] = "main"
    hf["private"] = hf.get("private", False)
    providers["huggingface"] = hf
    cfg.set("download.providers", providers)
    cfg.set("download.default_provider", "huggingface")
    cfg.set("download.default_mirror_by_provider.huggingface", "official")
    _append_upload_log(cfg, repo_id)
    return {"repo": repo_id, "revision": "main", "default_provider": "huggingface"}


def _append_upload_log(cfg: Config, repo_id: str) -> None:
    from .config import project_root

    if not cfg.get("logging.log_uploads", True):
        return
    path = project_root() / "docs" / "upload-log.json"
    entry = {"repo": repo_id, "revision": "main", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    data = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
    if isinstance(data, list):
        data.append(entry)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
