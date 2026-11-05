"""Model tier endpoints: inventory, download, deploy-mode switching."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from core.decision import explain_unavailable
from core.errors import AppError, ErrorCode
from core.model_files import downloaded_files, downloaded_map, is_downloaded, tier_dir
from core.runtime import current_mode, load_state, save_state, set_current_mode
from core.schemas import DeployMode, TierId, TierInfo, TierState
from core.tiers import build_tier_info, evaluate_tier_with_engines, recommend_tier
from server.deps import get_ctx, get_download_manager, rate_limit

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
def list_models(request: Request, refresh: bool = False, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Every tier with its state, reason and requirements."""

    ctx = get_ctx()
    if refresh:
        ctx.refresh_hardware()
        ctx.refresh_engines()
    downloaded = downloaded_map(ctx.cfg)
    mode = current_mode(ctx.cfg)

    tiers: List[Dict[str, Any]] = []
    for tier_id in ctx.cfg.tier_ids():
        status = evaluate_tier_with_engines(tier_id, mode, ctx.hardware, ctx.cfg, ctx.engines, downloaded)
        info = build_tier_info(ctx.cfg, tier_id, downloaded.get(tier_id, False),
                               downloaded_files(ctx.cfg, tier_id))
        payload = info.model_dump()
        payload.update({
            "status": status.state.value,
            "unavailable_reason": status.unavailable_reason,
            "warning_reason": status.warning_reason,
            "deploy_mode": status.deploy_mode.value,
            "backend_hint": status.backend_hint,
            "engine_state": status.engine_state.value if status.engine_state else None,
            "engine_model_ready": status.engine_model_ready,
            "current": load_state(ctx.cfg).get("current_tier") == tier_id,
        })
        tiers.append(payload)

    recommendation = recommend_tier(ctx.hardware, ctx.cfg, ctx.engines, downloaded)
    return {
        "tiers": tiers,
        "current_tier": load_state(ctx.cfg).get("current_tier", "L0"),
        "current_mode": mode.value,
        "recommendation": recommendation,
        "hardware": {
            "available_memory_gb": ctx.hardware.memory.available_gb,
            "free_disk_gb": ctx.hardware.disk.free_gb,
            "avx2": ctx.hardware.cpu.has_avx2,
            "gpus": [{"name": g.name, "vram_gb": g.vram_gb} for g in ctx.hardware.gpus],
        },
    }


@router.get("/models/{tier}")
def model_detail(tier: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    tier_id = _normalise(tier)
    downloaded = downloaded_map(ctx.cfg)
    status = evaluate_tier_with_engines(tier_id, current_mode(ctx.cfg), ctx.hardware, ctx.cfg, ctx.engines, downloaded)
    info = build_tier_info(ctx.cfg, tier_id, downloaded.get(tier_id, False),
                           downloaded_files(ctx.cfg, tier_id))
    payload = info.model_dump()
    payload.update({
        "status": status.state.value,
        "unavailable_reason": status.unavailable_reason,
        "warning_reason": status.warning_reason,
        "unavailable_explanation": (
            explain_unavailable(tier_id, ctx) if status.state == TierState.DISABLED else None
        ),
    })
    return payload


@router.post("/models/{tier}/download")
def start_download(
    tier: str,
    background: BackgroundTasks,
    request: Request,
    provider: Optional[str] = None,
    mirror: Optional[str] = None,
    filename: Optional[str] = None,
    _: None = Depends(rate_limit),
) -> Dict[str, Any]:
    """Queue a download and return immediately with a task id."""

    ctx = get_ctx()
    tier_id = _normalise(tier)
    status = evaluate_tier_with_engines(tier_id, current_mode(ctx.cfg), ctx.hardware, ctx.cfg, ctx.engines,
                                        downloaded_map(ctx.cfg))
    if status.state == TierState.DISABLED:
        raise AppError(
            ErrorCode.MODEL_UNSUPPORTED,
            f"档位 {tier_id} 不可用，无法下载：{status.unavailable_reason}",
            {"tier": tier_id, "reason": status.unavailable_reason,
             "hardware": {"available_memory_gb": ctx.hardware.memory.available_gb,
                          "free_disk_gb": ctx.hardware.disk.free_gb}},
        )

    manager = get_download_manager()
    task_id = f"dl-{tier_id.lower()}-{id(background) % 100000}"
    background.add_task(manager.download, tier_id, provider, mirror, filename, None, task_id)
    return {"task_id": task_id, "tier": tier_id, "state": "queued"}


@router.get("/downloads/{task_id}")
def download_status(task_id: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    manager = get_download_manager()
    return manager.task(task_id).model_dump()


@router.post("/downloads/{task_id}/cancel")
def cancel_download(task_id: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    manager = get_download_manager()
    ok = manager.cancel(task_id)
    if not ok:
        raise AppError(ErrorCode.MODEL_NOT_FOUND, f"下载任务不存在：{task_id}", {"task_id": task_id})
    return {"task_id": task_id, "state": "cancelled"}


@router.put("/models/{tier}/deploy-mode")
def set_deploy_mode(
    tier: str,
    payload: Dict[str, Any],
    request: Request,
    _: None = Depends(rate_limit),
) -> Dict[str, Any]:
    """Switch a tier's deploy mode after re-validating the engine."""

    ctx = get_ctx()
    tier_id = _normalise(tier)
    mode_raw = str(payload.get("mode", "builtin")).lower()
    try:
        mode = DeployMode(mode_raw)
    except ValueError:
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知部署模式：{mode_raw}", {"mode": mode_raw})

    from core.engines import require_engine_reachable

    if mode != DeployMode.BUILTIN:
        try:
            require_engine_reachable(mode, ctx.engines, ctx.cfg)
        except AppError as exc:
            raise exc

    status = evaluate_tier_with_engines(tier_id, mode, ctx.hardware, ctx.cfg, ctx.engines, downloaded_map(ctx.cfg))
    if status.state == TierState.DISABLED:
        raise AppError(
            ErrorCode.MODEL_UNSUPPORTED,
            f"{tier_id} 在 {mode.value} 模式下不可用：{status.unavailable_reason}",
            {"tier": tier_id, "mode": mode.value, "reason": status.unavailable_reason},
        )

    save_state(tier=tier_id, mode=mode.value, cfg=ctx.cfg)
    return {
        "tier": tier_id,
        "deploy_mode": mode.value,
        "state": status.state.value,
        "warning_reason": status.warning_reason,
    }


@router.post("/models/{tier}/activate")
def activate_tier(tier: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Mark a tier as the active one (after a hardware/availability re-check)."""

    ctx = get_ctx()
    tier_id = _normalise(tier)
    downloaded = downloaded_map(ctx.cfg)
    mode = current_mode(ctx.cfg)
    status = evaluate_tier_with_engines(tier_id, mode, ctx.hardware, ctx.cfg, ctx.engines, downloaded)

    if status.state == TierState.DISABLED:
        raise AppError(
            ErrorCode.MODEL_UNSUPPORTED,
            f"档位 {tier_id} 不可用：{status.unavailable_reason}",
            {"tier": tier_id, "reason": status.unavailable_reason},
        )
    if not downloaded.get(tier_id) and mode == DeployMode.BUILTIN:
        raise AppError(
            ErrorCode.MODEL_NOT_READY,
            f"档位 {tier_id} 尚未下载，无法加载",
            {"tier": tier_id, "path": str(tier_dir(ctx.cfg, tier_id))},
        )

    state = save_state(tier=tier_id, cfg=ctx.cfg)
    return {"tier": tier_id, "state": "activated", "current_tier": state.get("current_tier")}


@router.get("/models/{tier}/why-unavailable")
def why_unavailable(tier: str, request: Request, mode: Optional[str] = None,
                    _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    tier_id = _normalise(tier)
    deploy = DeployMode(mode) if mode else current_mode(ctx.cfg)
    return explain_unavailable(tier_id, ctx, deploy)


def _normalise(tier: str) -> str:
    value = tier.strip().upper()
    if value not in ("L0", "L1", "L2", "L3"):
        raise AppError(ErrorCode.MODEL_NOT_FOUND, f"未知档位：{tier}", {"tier": tier})
    return value
