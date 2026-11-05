"""``GET /v1/config`` and ``PUT /v1/config``."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from core.config import get_config
from core.schemas import ConfigPayload
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["config"])


@router.get("/config", response_model=ConfigPayload)
def read_config(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Everything the client may read.  Secret *values* are never included."""

    ctx = get_ctx()
    return ctx.cfg.to_public_dict()


@router.put("/config", response_model=ConfigPayload)
def update_config(payload: Dict[str, Any], request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Merge a partial config.  ``security`` is ignored here on purpose."""

    ctx = get_ctx()
    for section, value in payload.items():
        if section == "security":
            continue
        if isinstance(value, dict):
            existing = ctx.cfg.section(section)
            existing.update(value)
            ctx.cfg.set(section, existing)
    return ctx.cfg.to_public_dict()


@router.post("/config/reload")
def reload_config(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Re-read config.yaml from disk (e.g. after a manual edit)."""

    from core.config import Config, reset_config_cache

    reset_config_cache()
    ctx = get_ctx()
    ctx.cfg = Config.load()
    ctx.refresh_hardware()
    ctx.refresh_engines()
    return {"reloaded": True, "path": str(ctx.cfg.path)}
