"""Upload endpoints: preview, start, status, cancel."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from core.errors import AppError, ErrorCode
from core.model_files import scan_custom_models
from core.schemas import UploadTask
from server.deps import get_ctx, get_upload_manager, rate_limit

router = APIRouter(prefix="/v1/upload", tags=["upload"])


@router.get("/tasks")
def list_tasks(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    manager = get_upload_manager()
    return {"tasks": [t.model_dump() for t in manager.tasks.values()]}


@router.post("/tasks/preview")
def preview_upload(payload: Dict[str, Any], request: Request,
                   _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Dry-run: what would be uploaded, what can be skipped, total size."""

    ctx = get_ctx()
    manager = get_upload_manager()
    repo = str(payload.get("repo") or "").strip()
    if not repo:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少目标仓库")
    tiers = [str(t).upper() for t in (payload.get("tiers") or [])]
    return manager.preview(repo, tiers, bool(payload.get("include_custom", False)))


@router.post("/tasks")
def start_upload(
    payload: Dict[str, Any],
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(rate_limit),
) -> Dict[str, Any]:
    ctx = get_ctx()
    manager = get_upload_manager()
    repo = str(payload.get("repo") or "").strip()
    if not repo:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少目标仓库")
    tiers = [str(t).upper() for t in (payload.get("tiers") or [])]
    if not tiers and not payload.get("include_custom"):
        raise AppError(ErrorCode.INVALID_REQUEST, "请选择要上传的内容")

    task_id = f"up-{id(background) % 100000}"
    background.add_task(
        manager.start, repo, tiers, bool(payload.get("include_custom", False)),
        str(payload.get("commit_message") or "Upload model tier"), None, task_id,
    )
    return {"task_id": task_id, "repo": repo, "state": "queued"}


@router.get("/tasks/{task_id}", response_model=UploadTask)
def task_status(task_id: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    return get_upload_manager().task(task_id).model_dump()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    manager = get_upload_manager()
    if not manager.cancel(task_id):
        raise AppError(ErrorCode.MODEL_NOT_FOUND, f"上传任务不存在：{task_id}", {"task_id": task_id})
    return {"task_id": task_id, "state": "cancelled"}


@router.get("/candidates")
def upload_candidates(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Downloaded tiers plus imported custom models — the wizard's first step."""

    ctx = get_ctx()
    downloaded = []
    for tier_id in ctx.cfg.tier_ids():
        from core.model_files import is_downloaded, tier_size_bytes

        if is_downloaded(ctx.cfg, tier_id):
            downloaded.append({
                "tier": tier_id,
                "size_bytes": tier_size_bytes(ctx.cfg, tier_id),
                "size_mb": round(tier_size_bytes(ctx.cfg, tier_id) / 1024 ** 2, 1),
            })
    return {"tiers": downloaded, "custom": scan_custom_models(ctx.cfg)}
