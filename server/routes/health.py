"""``GET /health`` — liveness plus a quick summary of what the server can do."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.config import APP_VERSION, DISPLAY_NAME, get_config
from server.deps import get_ctx, rate_limit
from fastapi import Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request, _: None = Depends(rate_limit)) -> dict:
    ctx = get_ctx()
    cfg = get_config()
    engines = ctx.engines
    return {
        "status": "ok",
        "app": "paimind",
        "display_name": DISPLAY_NAME,
        "version": APP_VERSION,
        "models_dir": str(cfg.models_dir),
        "api_host": cfg.get("api.host", "127.0.0.1"),
        "api_port": int(cfg.get("api.port", 8686)),
        "engines": {
            kind.value: {"state": s.state.value, "reachable": s.reachable, "models": s.models}
            for kind, s in engines.items()
        },
        "online_enabled": bool(cfg.get("decision.allow_online", True)),
    }
