"""``GET /v1/engines`` and ``POST /v1/engines/{engine}/test``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from core.engines import engine_health_payload, probe_all
from core.errors import AppError, ErrorCode
from core.schemas import DeployMode, EngineKind
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["engines"])

_PROBES = {
    "ollama": "ollama",
    "vllm": "vllm",
    "llamacpp": "llamacpp",
    "llama.cpp": "llamacpp",
    "docker": "docker",
}


@router.get("/engines")
def list_engines(request: Request, refresh: bool = False, _: None = Depends(rate_limit)) -> dict:
    ctx = get_ctx()
    if refresh:
        ctx.refresh_engines()
    return engine_health_payload(ctx.engines)


@router.post("/engines/{engine}/test")
def test_engine(engine: str, request: Request, _: None = Depends(rate_limit)) -> dict:
    """Re-probe one engine on demand and report reachability."""

    ctx = get_ctx()
    key = _PROBES.get(engine.lower())
    if key is None:
        raise AppError(ErrorCode.ENGINE_UNSUPPORTED, f"未知引擎：{engine}", {"engine": engine})

    from core.engines import probe_llamacpp, probe_ollama, probe_vllm

    probe = {"ollama": probe_ollama, "vllm": probe_vllm, "llamacpp": probe_llamacpp}[key]
    status = probe(ctx.cfg)
    ctx.engines[status.engine] = status
    return {
        "engine": status.engine.value,
        "state": status.state.value,
        "reachable": status.reachable,
        "enabled": status.enabled,
        "latency_ms": status.latency_ms,
        "base_url": status.base_url,
        "models": status.models,
        "message": status.message,
        "details": status.details,
    }
