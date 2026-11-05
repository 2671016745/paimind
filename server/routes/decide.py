"""Decision endpoints: ``POST /v1/decide`` and the SSE variant."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from core.decision import decide, decide_stream, _resolve_tier, _validate
from core.errors import AppError, ErrorCode
from core.online import grant_online_consent, online_consent_given, upload_options_enabled
from core.schemas import DecideRequest, DecisionResponse, DeployMode
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["decide"])


@router.post("/decide", response_model=DecisionResponse)
def post_decide(payload: DecideRequest, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()

    if payload.stream:
        raise AppError(ErrorCode.INVALID_REQUEST, "stream=true 请使用 /v1/decide/stream")

    allow_online = payload.allow_online
    if payload.mode.value == "online" and not online_consent_given(ctx.cfg):
        # The UI asks for confirmation first; the API refuses politely instead.
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "首次联网需要用户确认，请先调用 /v1/hf/auth/online-consent 或在 UI 中确认",
            {"consent_required": True},
        )

    outcome = decide(
        payload, ctx,
        force_online=(payload.mode.value == "online"),
        allow_online=allow_online,
    )
    return outcome.response.model_dump()


@router.post("/decide/stream")
def post_decide_stream(payload: DecideRequest, request: Request,
                       _: None = Depends(rate_limit)) -> StreamingResponse:
    ctx = get_ctx()

    # Validate *before* the response starts: once SSE headers are on the wire an
    # AppError can only surface as a broken stream ("response already started").
    try:
        tier_id = _resolve_tier(payload, ctx)
        state, mode, notes = _validate(tier_id, ctx)
    except AppError:
        raise

    def event_source():
        for event in decide_stream(
            payload, ctx,
            force_online=(payload.mode.value == "online"),
            _precomputed=(tier_id, state, mode, notes),
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/decide/online-consent")
def online_consent(payload: Optional[Dict[str, Any]] = None, request: Request = None,
                   _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Record (or clear) the one-time privacy confirmation for online search."""

    ctx = get_ctx()
    granted = bool((payload or {}).get("granted", True))
    if granted:
        grant_online_consent(ctx.cfg)
    else:
        from core.online import _privacy_state, _save_privacy_state

        state = _privacy_state(ctx.cfg)
        state["online_ack"] = False
        _save_privacy_state(ctx.cfg, state)
    return {"consent": online_consent_given(ctx.cfg)}


@router.get("/decide/online-consent")
def online_consent_status(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    return {
        "consent": online_consent_given(ctx.cfg),
        "upload_options_enabled": upload_options_enabled(ctx.cfg),
    }
