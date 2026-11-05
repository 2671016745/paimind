"""Application error taxonomy.

Every error the app or API can raise maps to one :class:`ErrorCode` member and one
HTTP status.  The mapping is centralised here so the API layer, the desktop client
and the mobile client all report the same codes.

设计要点 / Design notes
-----------------------

* ``ErrorCode`` is a ``str`` enum, so members serialise straight into JSON.
* ``HTTP_STATUS_BY_CODE`` is the single source of truth for the status code.
* ``AppError`` carries an optional ``details`` payload (hardware snapshot, per-file
  upload results, per-mirror download attempts, ...) which the API layer embeds
  verbatim and the UIs render.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(str, Enum):
    """All error codes exposed by the app and the HTTP API."""

    # --- 400 Bad Request -------------------------------------------------
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_OPTIONS = "INVALID_OPTIONS"
    INVALID_REPO_NAME = "INVALID_REPO_NAME"
    INVALID_URL_TEMPLATE = "INVALID_URL_TEMPLATE"

    # --- 401 Unauthorized ------------------------------------------------
    UNAUTHORIZED = "UNAUTHORIZED"
    HF_UNAUTHORIZED = "HF_UNAUTHORIZED"

    # --- 403 Forbidden ---------------------------------------------------
    HF_FORBIDDEN = "HF_FORBIDDEN"

    # --- 404 Not Found ---------------------------------------------------
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MIRROR_NOT_FOUND = "MIRROR_NOT_FOUND"
    HF_REPO_NOT_FOUND = "HF_REPO_NOT_FOUND"

    # --- 409 Conflict ----------------------------------------------------
    MODEL_NOT_READY = "MODEL_NOT_READY"
    ENGINE_MODEL_NOT_READY = "ENGINE_MODEL_NOT_READY"

    # --- 413 Payload Too Large -------------------------------------------
    HF_FILE_TOO_LARGE = "HF_FILE_TOO_LARGE"

    # --- 422 Unprocessable Entity ---------------------------------------
    MODEL_UNSUPPORTED = "MODEL_UNSUPPORTED"
    ENGINE_UNSUPPORTED = "ENGINE_UNSUPPORTED"

    # --- 429 Too Many Requests -------------------------------------------
    RATE_LIMITED = "RATE_LIMITED"

    # --- 500 Internal Server Error ---------------------------------------
    INFERENCE_ERROR = "INFERENCE_ERROR"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"

    # --- 502 Bad Gateway -------------------------------------------------
    ALL_MIRRORS_FAILED = "ALL_MIRRORS_FAILED"
    HF_UPSTREAM_ERROR = "HF_UPSTREAM_ERROR"

    # --- 503 Service Unavailable -----------------------------------------
    MODEL_LOADING = "MODEL_LOADING"
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    NETWORK_ERROR = "NETWORK_ERROR"

    # --- 507 Insufficient Storage ----------------------------------------
    HF_QUOTA_EXCEEDED = "HF_QUOTA_EXCEEDED"

    # --- internal (never leaked to clients verbatim) ---------------------
    CONFIG_ERROR = "CONFIG_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS_BY_CODE: Dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.INVALID_OPTIONS: 400,
    ErrorCode.INVALID_REPO_NAME: 400,
    ErrorCode.INVALID_URL_TEMPLATE: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.HF_UNAUTHORIZED: 401,
    ErrorCode.HF_FORBIDDEN: 403,
    ErrorCode.MODEL_NOT_FOUND: 404,
    ErrorCode.MIRROR_NOT_FOUND: 404,
    ErrorCode.HF_REPO_NOT_FOUND: 404,
    ErrorCode.MODEL_NOT_READY: 409,
    ErrorCode.ENGINE_MODEL_NOT_READY: 409,
    ErrorCode.HF_FILE_TOO_LARGE: 413,
    ErrorCode.MODEL_UNSUPPORTED: 422,
    ErrorCode.ENGINE_UNSUPPORTED: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INFERENCE_ERROR: 500,
    ErrorCode.UPLOAD_FAILED: 500,
    ErrorCode.DOWNLOAD_FAILED: 500,
    ErrorCode.ALL_MIRRORS_FAILED: 502,
    ErrorCode.HF_UPSTREAM_ERROR: 502,
    ErrorCode.MODEL_LOADING: 503,
    ErrorCode.ENGINE_UNAVAILABLE: 503,
    ErrorCode.NETWORK_ERROR: 503,
    ErrorCode.HF_QUOTA_EXCEEDED: 507,
    ErrorCode.CONFIG_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


def http_status_for(code: "ErrorCode | str") -> int:
    """Return the HTTP status for *code* (defaults to 500 for unknown codes)."""

    if isinstance(code, str):
        try:
            code = ErrorCode(code)
        except ValueError:
            return 500
    return HTTP_STATUS_BY_CODE.get(code, 500)


class AppError(Exception):
    """Raised anywhere in ``core``; the API layer turns it into the error envelope.

    Parameters
    ----------
    code:
        Machine readable error code, see :class:`ErrorCode`.
    message:
        Human readable, safe-to-display message.  Never put secrets in here.
    details:
        Optional structured payload (hardware snapshot, per-file results, ...).
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status(self) -> int:
        return http_status_for(self.code)

    def to_envelope(self) -> Dict[str, Any]:
        """Render as ``{"error": {code, message, type, param}}``."""

        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "type": self.code.value.lower(),
                "param": None,
                **({"details": self.details} if self.details else {}),
            }
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AppError({self.code.value}, {self.message!r}, details={self.details!r})"


def invalid_options(reason: str) -> AppError:
    return AppError(ErrorCode.INVALID_OPTIONS, reason)


def model_not_ready(tier: str, path: str) -> AppError:
    return AppError(
        ErrorCode.MODEL_NOT_READY,
        f"模型档位 {tier} 尚未就绪：{path}",
        {"tier": tier, "path": path},
    )


def engine_unavailable(engine: str, url: str) -> AppError:
    return AppError(
        ErrorCode.ENGINE_UNAVAILABLE,
        f"推理引擎 {engine} 不可达：{url}",
        {"engine": engine, "url": url},
    )
