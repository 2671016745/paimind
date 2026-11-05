"""Shared FastAPI dependencies: app context, managers, auth, rate limiting."""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from fastapi import Request

from core.config import Config, get_config
from core.context import AppContext
from core.errors import AppError, ErrorCode
from core.download import DownloadManager
from core.hf import UploadManager

_ctx: Optional[AppContext] = None
_download_manager: Optional[DownloadManager] = None
_upload_manager: Optional[UploadManager] = None

# naive per-IP sliding window: {ip: [timestamps]}
_rate_buckets: Dict[str, list] = {}
_rate_lock = __import__("threading").Lock()


def get_ctx() -> AppContext:
    global _ctx
    if _ctx is None:
        _ctx = AppContext(get_config())
    return _ctx


def reset_ctx() -> None:
    """Drop the singleton (used by tests)."""

    global _ctx
    if _ctx is not None:
        try:
            _ctx.close_all()
        except Exception:
            pass
    _ctx = None


def get_download_manager() -> DownloadManager:
    global _download_manager
    if _download_manager is None:
        _download_manager = DownloadManager(get_ctx().cfg)
    return _download_manager


def get_upload_manager() -> UploadManager:
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = UploadManager(get_ctx().cfg)
    return _upload_manager


def check_auth(request: Request) -> None:
    """Optional ``Authorization: Bearer`` gate for the local API."""

    cfg = get_config()
    if not cfg.get("api.enable_auth", False):
        return
    from core.keys import get_secret

    expected = get_secret(cfg.get("security.api_token_ref", "api_token"))
    if not expected:
        return
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or header[7:] != expected:
        raise AppError(ErrorCode.UNAUTHORIZED, "缺少或错误的 API token")


def rate_limit(request: Request) -> None:
    """Fixed-window limiter: 60 requests/minute/IP by default.

    Takes only ``request`` so FastAPI never treats a config object as a request
    parameter.
    """

    cfg = get_config()
    limit = int(cfg.get("api.rate_limit_per_minute", 60))
    if limit <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = 60.0
    with _rate_lock:
        stamps = [t for t in _rate_buckets.get(ip, []) if now - t < window]
        if len(stamps) >= limit:
            _rate_buckets[ip] = stamps
            raise AppError(
                ErrorCode.RATE_LIMITED,
                f"请求过于频繁，限流 {limit} 次/分钟/IP",
                {"limit": limit, "window_seconds": window},
            )
        stamps.append(now)
        _rate_buckets[ip] = stamps


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
