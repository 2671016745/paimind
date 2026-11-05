"""Download source management and the multi-source fallback downloader.

Provider/mirror model
---------------------
A *provider* (``github``, ``huggingface``) owns a repository; a *mirror* is one
URL template for reaching it.  Selecting a download means picking a provider, then
a mirror, then confirming the concrete URL.

Fallback
--------
Downloads try the provider's ``fallback_order`` in sequence.  A source fails on
timeout (30 s), HTTP 4xx/5xx, or a SHA256 mismatch (the partial file is deleted).
When every source fails the error carries each attempt so the UI can show "source
X failed because Y" and offer a copy-paste direct link for manual download.

Safety
------
* URL templates must be ``https`` unless ``allow_http_mirrors`` is set.
* The HF token is only ever attached to ``huggingface.co`` and to custom mirrors
  the user explicitly opted into via ``hf_mirror_send_token``.
* Every attempt is logged with provider, mirror, elapsed time, result and whether
  a token was used — never the token itself.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from .config import Config, get_config
from .errors import AppError, ErrorCode
from .keys import get_secret, redact
from .model_files import expected_sha256, load_checksums, sha256_file
from .schemas import (
    DownloadAttempt,
    DownloadSourceGroup,
    DownloadSourcesPayload,
    DownloadTask,
    Mirror,
    MirrorTestResult,
    ProviderConfig,
)

GITHUB = "github"
HUGGINGFACE = "huggingface"

_PROVIDER_LABELS = {GITHUB: "GitHub", HUGGINGFACE: "HuggingFace"}


# --------------------------------------------------------------------------
# Source listing
# --------------------------------------------------------------------------


def list_mirrors(cfg: Optional[Config] = None) -> List[Mirror]:
    """All mirrors: built-ins for both providers plus user-defined custom mirrors."""

    cfg = cfg or get_config()
    from .config import DEFAULT_MIRRORS

    out: List[Mirror] = [Mirror(**m) for m in DEFAULT_MIRRORS]
    for raw in cfg.get("download.custom_mirrors", []) or []:
        out.append(Mirror(**raw))
    return out


def list_providers(cfg: Optional[Config] = None) -> List[ProviderConfig]:
    cfg = cfg or get_config()
    raw = cfg.section("download").get("providers", {}) or {}
    out: List[ProviderConfig] = []
    for name in (GITHUB, HUGGINGFACE):
        entry = raw.get(name, {}) or {}
        out.append(ProviderConfig(
            provider=name,
            enabled=bool(entry.get("enabled", True)),
            repo=str(entry.get("repo", "")),
            tag=str(entry.get("tag", "")),
            revision=str(entry.get("revision", "")),
            private=bool(entry.get("private", False)),
            auth_token_ref=entry.get("auth_token_ref"),
            mirrors=[str(m) for m in (entry.get("mirrors") or [])],
            files=[],
        ))
    return out


def build_payload(cfg: Optional[Config] = None) -> DownloadSourcesPayload:
    cfg = cfg or get_config()
    dl = cfg.section("download")
    mirrors = list_mirrors(cfg)
    groups: List[DownloadSourceGroup] = []
    for provider in (GITHUB, HUGGINGFACE):
        groups.append(DownloadSourceGroup(
            provider=provider,
            label=_PROVIDER_LABELS[provider],
            mirrors=[m for m in mirrors if m.provider == provider],
        ))
    custom = [m for m in mirrors if m.kind == "custom" and m.provider not in (GITHUB, HUGGINGFACE)]
    if custom:
        groups.append(DownloadSourceGroup(provider="custom", label="其他自定义", mirrors=custom))
    return DownloadSourcesPayload(
        default_provider=str(dl.get("default_provider", GITHUB)),
        default_mirror_by_provider=dict(dl.get("default_mirror_by_provider", {})),
        fallback_order={k: list(v) for k, v in (dl.get("fallback_order", {}) or {}).items()},
        groups=groups,
        custom_mirrors=custom,
    )


def find_mirror(cfg: Config, provider: str, mirror_id: str) -> Mirror:
    for mirror in list_mirrors(cfg):
        if mirror.provider == provider and mirror.id == mirror_id:
            return mirror
    for mirror in list_mirrors(cfg):
        if mirror.id == mirror_id:
            return mirror
    raise AppError(ErrorCode.MIRROR_NOT_FOUND, f"未找到镜像：{provider}/{mirror_id}",
                   {"provider": provider, "mirror": mirror_id})


def template_variables(cfg: Config, provider: str, tier_id: str, filename: str) -> Dict[str, str]:
    prov = next((p for p in list_providers(cfg) if p.provider == provider), None)
    return {
        "{repo}": prov.repo if prov else "",
        "{tag}": prov.tag if prov else "",
        "{revision}": prov.revision if prov else "",
        "{path}": filename,
        "{file}": filename,
        "{branch}": prov.revision if prov else "",
        "{tier}": tier_id,
    }


def render_download_url(
    cfg: Config, provider: str, mirror_id: str, tier_id: str, filename: str
) -> str:
    mirror = find_mirror(cfg, provider, mirror_id)
    variables = template_variables(cfg, provider, tier_id, filename)
    url = mirror.url_template
    for key, value in variables.items():
        url = url.replace(key, value)
    return url


# --------------------------------------------------------------------------
# Mirror testing
# --------------------------------------------------------------------------


def test_mirror(
    provider: str,
    mirror_id: str,
    cfg: Optional[Config] = None,
    tier_id: str = "L0",
    filename: str = "model.onnx",
    timeout: float = 5.0,
) -> MirrorTestResult:
    """HEAD the resolved URL; 5 s timeout; token only for HF when configured."""

    cfg = cfg or get_config()
    mirror = find_mirror(cfg, provider, mirror_id)
    url = render_download_url(cfg, provider, mirror_id, tier_id, filename)

    problems: List[str] = []
    if not url.startswith("https://") and not cfg.get("download.allow_http_mirrors", False):
        problems.append("镜像 URL 必须使用 https")

    used_token = False
    headers = {"User-Agent": "paimind/1.0"}
    prov = next((p for p in list_providers(cfg) if p.provider == provider), None)
    if prov and prov.private and _should_send_token(cfg, mirror):
        token = get_secret(prov.auth_token_ref or "hf_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
            used_token = True

    started = time.perf_counter()
    status: Optional[int] = None
    message = ""
    content_length: Optional[int] = None
    ok = False
    try:
        r = httpx.head(url, headers=headers, timeout=timeout, follow_redirects=True)
        status = r.status_code
        content_length = int(r.headers.get("content-length")) if r.headers.get("content-length") else None
        if r.status_code < 400:
            ok = True
            message = f"HTTP {r.status_code}"
        elif r.status_code == 401:
            message = "HTTP 401：该仓库为私有，请在设置中登录 Hugging Face"
        elif r.status_code == 404:
            message = "HTTP 404：该源暂无此文件"
        else:
            message = f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        message = "连接超时（5 秒）"
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:120]}"

    if problems:
        ok = False
        message = "; ".join(problems + [message])

    latency = int((time.perf_counter() - started) * 1000)
    return MirrorTestResult(
        provider=provider, mirror_id=mirror_id, ok=ok, url=url, status_code=status,
        latency_ms=latency, message=message, content_length=content_length,
        sha256_prefix=None, used_token=used_token,
    )


def _should_send_token(cfg: Config, mirror: Mirror) -> bool:
    """Never attach the HF token to anything but huggingface.co (or an explicit opt-in)."""

    if "huggingface.co" in mirror.url_template:
        return True
    return bool(cfg.get("security.hf_mirror_send_token", False))


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------


class DownloadManager:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.tasks: Dict[str, DownloadTask] = {}
        self._cancel: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # -- task bookkeeping -------------------------------------------------

    def task(self, task_id: str) -> DownloadTask:
        with self._lock:
            task = self.tasks.get(task_id)
        if task is None:
            raise AppError(ErrorCode.MODEL_NOT_FOUND, f"下载任务不存在：{task_id}",
                           {"task_id": task_id})
        return task

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self.tasks:
                return False
            self._cancel[task_id] = True
            self.tasks[task_id].state = "cancelled"
        return True

    def _is_cancelled(self, task_id: str) -> bool:
        return bool(self._cancel.get(task_id))

    def _update(self, task: DownloadTask, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = _now()

    # -- single-source download ------------------------------------------

    def download(
        self,
        tier_id: str,
        provider: Optional[str] = None,
        mirror_id: Optional[str] = None,
        filename: Optional[str] = None,
        progress: Optional[Callable[[float, int, int, float], None]] = None,
        task_id: Optional[str] = None,
    ) -> DownloadTask:
        """Download a tier with multi-source fallback.

        Returns the finished task; raises :class:`AppError` with code
        ``ALL_MIRRORS_FAILED`` and per-source ``details.attempts`` when every
        source fails.
        """

        cfg = self.cfg
        provider = provider or str(cfg.get("download.default_provider", GITHUB))
        filename = filename or _primary_filename(cfg, tier_id)
        task_id = task_id or f"dl-{uuid.uuid4().hex[:12]}"

        fallback = list((cfg.get("download.fallback_order", {}) or {}).get(provider, []) or [])
        if mirror_id and mirror_id not in fallback:
            fallback = [mirror_id] + fallback
        if not fallback:
            fallback = [str((cfg.get("download.default_mirror_by_provider", {}) or {}).get(provider, "official"))]

        task = DownloadTask(
            task_id=task_id, tier=tier_id, provider=provider, mirror_id=fallback[0],
            state="running", url="", created_at=_now(), updated_at=_now(),
        )
        with self._lock:
            self.tasks[task_id] = task
            self._cancel.pop(task_id, None)

        target_dir = Path(cfg.models_dir) / tier_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        partial = target_dir / f"{filename}.part"

        attempts: List[DownloadAttempt] = []
        total = 0
        expected = expected_sha256(cfg, tier_id, filename)

        for source in fallback:
            if self._is_cancelled(task_id):
                self._update(task, state="cancelled")
                return task

            mirror = find_mirror(cfg, provider, source)
            url = render_download_url(cfg, provider, source, tier_id, filename)
            self._update(task, provider=provider, mirror_id=source, url=url)

            attempt, ok, total = self._download_one(
                url=url, target=target, partial=partial, filename=filename,
                provider=provider, mirror=mirror, expected=expected,
                progress=progress, task=task, task_id=task_id,
            )
            attempts.append(attempt)
            self._update(task, attempts=list(attempts))

            if ok:
                self._update(
                    task, state="done", progress=1.0, total_bytes=total,
                    downloaded_bytes=total, error=None,
                )
                _log_download(cfg, task, attempts)
                return task

        self._update(task, state="failed", attempts=list(attempts))
        _log_download(cfg, task, attempts)
        raise AppError(
            ErrorCode.ALL_MIRRORS_FAILED,
            f"{provider} 的所有下载源均失败",
            {
                "provider": provider,
                "tier": tier_id,
                "filename": filename,
                "attempts": [a.model_dump() for a in attempts],
                "manual_hint": (
                    f"可手动下载后放入 {target_dir}（文件名 {filename}）"
                ),
            },
        )

    def _download_one(
        self,
        url: str,
        target: Path,
        partial: Path,
        filename: str,
        provider: str,
        mirror: Mirror,
        expected: Optional[str],
        progress: Optional[Callable[[float, int, int, float], None]],
        task: DownloadTask,
        task_id: str,
    ) -> Tuple[DownloadAttempt, bool, int]:
        cfg = self.cfg
        timeout = float(cfg.get("download.timeout_seconds", 30))
        verify = bool(cfg.get("download.verify_sha256", True))
        resume = bool(cfg.get("download.resume_support", True))
        max_redirects = int(cfg.get("download.follow_redirects", 5))

        headers = {"User-Agent": "paimind/1.0"}
        used_token = False
        prov = next((p for p in list_providers(cfg) if p.provider == provider), None)
        if prov and prov.private and _should_send_token(cfg, mirror):
            token = get_secret(prov.auth_token_ref or "hf_token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
                used_token = True

        started = time.perf_counter()
        downloaded = partial.stat().st_size if (resume and partial.exists()) else 0
        if downloaded:
            headers["Range"] = f"bytes={downloaded}-"

        status: Optional[int] = None
        error: Optional[str] = None
        total = 0

        try:
            with httpx.stream(
                "GET", url, headers=headers, timeout=timeout,
                follow_redirects=True,
            ) as resp:
                status = resp.status_code
                if status in (301, 302, 303, 307, 308) or resp.is_redirect:
                    error = f"重定向次数超过 {max_redirects} 跳"
                    raise RuntimeError(error)
                if status == 401:
                    error = "HTTP 401：私有仓库，请先登录 Hugging Face"
                    raise RuntimeError(error)
                if status == 404:
                    error = "HTTP 404：该源暂无此文件"
                    raise RuntimeError(error)
                if status == 416:
                    # Range not satisfiable — restart cleanly
                    downloaded = 0
                    partial.unlink(missing_ok=True)
                    headers.pop("Range", None)
                    raise RuntimeError("HTTP 416：断点不可用，将重新下载")
                if status >= 400:
                    error = f"HTTP {status}"
                    raise RuntimeError(error)

                if resp.headers.get("content-length"):
                    length = int(resp.headers["content-length"])
                    total = downloaded + length if status == 206 else length
                else:
                    total = 0

                mode = "ab" if (status == 206 and downloaded) else "wb"
                if mode == "wb":
                    downloaded = 0

                with partial.open(mode) as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        if self._is_cancelled(task_id):
                            raise RuntimeError("cancelled")
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self._update(
                                task, progress=min(0.999, downloaded / total),
                                downloaded_bytes=downloaded, total_bytes=total,
                                speed_bps=_speed(downloaded, started),
                            )
                            if progress:
                                progress(min(0.999, downloaded / total), downloaded, total,
                                         _speed(downloaded, started))

        except RuntimeError as exc:
            error = str(exc)
            if error == "cancelled":
                self._update(task, state="cancelled")
        except httpx.TimeoutException:
            error = f"超时（{timeout:g} 秒）"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:160]}"

        elapsed = int((time.perf_counter() - started) * 1000)

        if error is None:
            if verify and expected:
                actual = sha256_file(partial)
                if actual.lower() != expected.lower():
                    error = f"SHA256 不匹配（期望 {expected[:8]}…，实际 {actual[:8]}…）"
                    partial.unlink(missing_ok=True)
            if error is None:
                partial.replace(target)
                return (
                    DownloadAttempt(
                        provider=provider, mirror_id=mirror.id, url=url, ok=True,
                        bytes_written=downloaded, elapsed_ms=elapsed, used_token=used_token,
                    ),
                    True,
                    target.stat().st_size,
                )

        return (
            DownloadAttempt(
                provider=provider, mirror_id=mirror.id, url=url, ok=False,
                error=error, http_status=status, bytes_written=downloaded,
                elapsed_ms=elapsed, used_token=used_token,
            ),
            False,
            total,
        )


def _primary_filename(cfg: Config, tier_id: str) -> str:
    manifest = _manifest_files(cfg, tier_id)
    if manifest:
        return manifest[0]
    return "model.onnx"


def _manifest_files(cfg: Config, tier_id: str) -> List[str]:
    """File list for a tier: HF manifest.json wins, else the bundled checksums."""

    import json

    manifest_path = Path(cfg.models_dir) / tier_id / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = data.get("files") or []
            if isinstance(files, list) and files:
                if isinstance(files[0], dict):
                    return [str(f.get("name")) for f in files if f.get("name")]
                return [str(f) for f in files]
        except (OSError, ValueError):
            pass

    checksums = load_checksums(cfg).get(tier_id, {})
    return list(checksums.keys())


def _speed(downloaded: int, started: float) -> float:
    elapsed = max(time.perf_counter() - started, 1e-6)
    return round(downloaded / elapsed, 0)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _log_download(cfg: Config, task: DownloadTask, attempts: Sequence[DownloadAttempt]) -> None:
    if not cfg.get("logging.log_downloads", True):
        return
    import json
    import logging

    logger = logging.getLogger("paimind.download")
    payload = {
        "task_id": task.task_id,
        "tier": task.tier,
        "provider": task.provider,
        "mirror": task.mirror_id,
        "state": task.state,
        "url": redact(task.url),
        "attempts": [
            {
                "provider": a.provider, "mirror": a.mirror_id, "ok": a.ok,
                "error": redact(a.error or ""), "http_status": a.http_status,
                "elapsed_ms": a.elapsed_ms, "used_token": a.used_token,
            }
            for a in attempts
        ],
    }
    (logger.info if task.state == "done" else logger.warning)(json.dumps(payload, ensure_ascii=False))
