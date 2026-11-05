"""FastAPI application factory.

Run it three ways:

* ``python -m server``                      — standalone API server
* ``uvicorn server.app:create_app --reload`` — development
* from the desktop app                      — in-process, same port

The OpenAPI 3.1 document is served at ``/docs`` and ``/redoc``; the mobile PWA is
mounted at ``/app`` so a phone can open ``http://<host>:8686/app``.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .deps import check_auth, get_ctx, rate_limit
from .routes import config, decide, download_sources, engines, health, hf, models, upload, usage

LOGGER = logging.getLogger("paimind.server")

API_VERSION = "0.0.1"


def create_app(enable_auth: Optional[bool] = None) -> FastAPI:
    ctx = get_ctx()
    cfg = ctx.cfg

    app = FastAPI(
        title="paimind API",
        version=API_VERSION,
        description=(
            "本地决策模型 API：本地小模型优先，置信度不足或问题有时效性时联网检索再决策。"
            "Local-first decision API: a small local model decides; when confidence is low "
            "or the question is time-sensitive, it searches online and decides with evidence."
        ),
        openapi_tags=[
            {"name": "decide", "description": "决策 Decision"},
            {"name": "models", "description": "档位与下载 Tiers & downloads"},
            {"name": "engines", "description": "推理引擎 Inference engines"},
            {"name": "download-sources", "description": "下载源 Download sources"},
            {"name": "huggingface", "description": "Hugging Face 登录与仓库"},
            {"name": "upload", "description": "上传到 Hugging Face"},
            {"name": "hardware", "description": "硬件检测 Hardware"},
            {"name": "config", "description": "配置 Configuration"},
            {"name": "usage", "description": "用量 Usage"},
        ],
    )

    origins = list(cfg.get("api.cors_origins", []) or []) or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)
    _register_routes(app)
    _mount_pwa(app)

    async def _warm() -> None:
        try:
            ctx.refresh_hardware()
            ctx.refresh_engines()
            LOGGER.info("paimind API ready on %s:%s", cfg.get("api.host"), cfg.get("api.port"))
        except Exception as exc:  # startup must never crash on a probe failure
            LOGGER.warning("startup probe failed: %s", exc)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await _warm()
        yield

    app.router.lifespan_context = lifespan
    return app


def _register_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(health.router, prefix="", include_in_schema=False)
    app.include_router(engines.router)
    app.include_router(models.router)
    app.include_router(download_sources.router)
    app.include_router(decide.router)
    app.include_router(hf.router)
    app.include_router(upload.router)
    app.include_router(config.router)
    app.include_router(usage.router)
    # hardware routes live in their own module but under /v1
    from .routes import hardware

    app.include_router(hardware.router)


def _jsonable(value: Any) -> Any:
    """Make a pydantic error dict JSON-serialisable.

    ``ctx.error`` holds the raw exception object, which ``json.dumps`` refuses to
    serialise — stringify it instead.
    """

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _register_error_handlers(app: FastAPI) -> None:
    from core.errors import AppError, http_status_for
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_envelope())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [_jsonable(e) for e in exc.errors()]
        first = errors[0] if errors else {}
        param = ".".join(str(p) for p in (first.get("loc") or []) if str(p) != "body")
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": f"请求参数错误：{first.get('msg', 'invalid')}",
                    "type": "invalid_request_error",
                    "param": param or None,
                    "details": {"errors": errors[:10]},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler.

        Transport failures (DNS / TLS / refused connection) are a service-availability
        problem, not a bug — report them as NETWORK_ERROR so the caller can retry
        instead of treating it as a broken server.
        """

        network_types = ()
        try:
            import httpx
            network_types = (httpx.HTTPError,)
        except Exception:  # pragma: no cover - httpx always installed
            pass

        if network_types and isinstance(exc, network_types):
            LOGGER.warning("network error on %s: %s", request.url.path, exc)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "NETWORK_ERROR",
                        "message": f"网络请求失败：{type(exc).__name__}",
                        "type": "network_error",
                        "param": None,
                        "details": {"detail": str(exc)[:200]},
                    }
                },
            )

        LOGGER.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误",
                    "type": "internal_error",
                    "param": None,
                }
            },
        )


def _mount_pwa(app: FastAPI) -> None:
    """Serve the mobile PWA from ``mobile/pwa`` when present."""

    candidates = [
        Path(__file__).resolve().parent.parent / "mobile" / "pwa",
        Path(os.environ.get("PAIMIND_PWA", "")) if os.environ.get("PAIMIND_PWA") else None,
    ]
    for directory in candidates:
        if directory and directory.is_dir():
            app.mount("/app", StaticFiles(directory=str(directory), html=True), name="pwa")
            break


def run_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
    log_level: Optional[str] = None,
) -> None:
    """Blocking entry point (``python -m server``)."""

    import uvicorn

    ctx = get_ctx()
    cfg = ctx.cfg
    uvicorn.run(
        "server.app:create_app",
        factory=True,
        host=host or str(cfg.get("api.host", "127.0.0.1")),
        port=int(port or cfg.get("api.port", 8686)),
        log_level=log_level or str(cfg.get("app.log_level", "info")).lower(),
    )
