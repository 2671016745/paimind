"""``GET /v1/hardware`` — the detected machine and the tier verdicts derived from it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from core.hardware import detect_hardware, hardware_snapshot
from core.schemas import DeployMode
from core.tiers import evaluate_all, recommend_tier
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["hardware"])


@router.get("/hardware")
def get_hardware(request: Request, refresh: bool = False, _: None = Depends(rate_limit)) -> dict:
    ctx = get_ctx()
    if refresh:
        ctx.refresh_hardware()
    hw = ctx.hardware
    return {
        "hardware": hw.model_dump(),
        "snapshot": hardware_snapshot(hw),
        "memory_factor_applied": hw.memory_factor_applied,
        "apple_silicon": hw.apple_silicon,
        "providers": _builtin_provider_status(),
    }


def _builtin_provider_status() -> dict:
    from core.inference.builtin import describe_providers

    return describe_providers()


@router.get("/hardware/tiers")
def hardware_tiers(request: Request, mode: str = "builtin", _: None = Depends(rate_limit)) -> dict:
    """Tier verdicts for one deploy mode — what the settings page renders."""

    ctx = get_ctx()
    try:
        deploy = DeployMode(mode)
    except ValueError:
        deploy = DeployMode.BUILTIN
    statuses = evaluate_all([deploy], ctx.hardware, ctx.cfg, ctx.engines)
    recommendation = recommend_tier(ctx.hardware, ctx.cfg, ctx.engines)
    return {
        "mode": deploy.value,
        "tiers": [s.model_dump() for s in statuses],
        "recommendation": recommendation,
    }
