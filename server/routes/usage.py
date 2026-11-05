"""``GET /v1/usage`` — local request accounting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from core.schemas import UsageSummary
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["usage"])


@router.get("/usage", response_model=UsageSummary)
def read_usage(request: Request, recent: int = 50, _: None = Depends(rate_limit)) -> UsageSummary:
    ctx = get_ctx()
    return ctx.usage.summary(recent=recent)


@router.delete("/usage")
def clear_usage(request: Request, _: None = Depends(rate_limit)) -> dict:
    ctx = get_ctx()
    ctx.usage.clear()
    return {"cleared": True}
