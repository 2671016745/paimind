"""Hugging Face endpoints: auth, repositories, set-as-download-source."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from core.errors import AppError, ErrorCode
from core.hf import (
    HFAuthStatus,
    HFRepo,
    create_repo,
    import_from_hf_cli,
    list_repos,
    login,
    logout,
    set_as_download_source,
    status,
    validate_repo_name,
)
from core.keys import backend_name
from core.schemas import HFRepoCreateInfo
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1/hf", tags=["huggingface"])


@router.post("/auth/login", response_model=HFAuthStatus)
def hf_login(payload: Dict[str, Any], request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    token = str(payload.get("token") or "").strip()
    if not token:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少 token")
    return login(token, ctx.cfg).model_dump()


@router.post("/auth/import-cli", response_model=HFAuthStatus)
def hf_import_from_cli(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Adopt the token stored by the local ``hf`` CLI (no copy-paste)."""

    ctx = get_ctx()
    return import_from_hf_cli(ctx.cfg).model_dump()


@router.get("/auth/status", response_model=HFAuthStatus)
def hf_status(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    payload = status(ctx.cfg).model_dump()
    payload["storage_backend"] = backend_name()
    return payload


@router.post("/auth/logout")
def hf_logout(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    logout(ctx.cfg)
    return {"logged_in": False}


@router.get("/repos")
def hf_repos(request: Request, username: Optional[str] = None,
             _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    if not username:
        current = status(ctx.cfg)
        if not current.username:
            raise AppError(ErrorCode.HF_UNAUTHORIZED, "未登录，无法列出仓库")
        username = current.username
    repos = list_repos(str(username), ctx.cfg)
    return {"username": username, "repos": [r.model_dump() for r in repos]}


@router.post("/repos", response_model=HFRepo)
def hf_create_repo(payload: HFRepoCreateInfo, request: Request,
                   _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    repo = create_repo(payload.name, payload.private, payload.create_readme, ctx.cfg)
    return repo.model_dump()


@router.get("/repos/validate-name")
def hf_validate_name(name: str, request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ok, reason = validate_repo_name(name)
    return {"name": name, "valid": ok, "reason": reason}


@router.post("/repos/set-download-source")
def hf_set_download_source(payload: Dict[str, Any], request: Request,
                           _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    repo = str(payload.get("repo") or "").strip()
    if "/" not in repo:
        raise AppError(ErrorCode.INVALID_REPO_NAME, "仓库名需为 <namespace>/<name>",
                       {"repo": repo})
    return set_as_download_source(repo, ctx.cfg)


@router.get("/config")
def hf_config(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """HF provider settings: repo, revision, privacy, token *status* only."""

    ctx = get_ctx()
    dl = ctx.cfg.section("download")
    providers = dl.get("providers", {}) or {}
    hf = providers.get("huggingface", {}) or {}
    current = status(ctx.cfg)
    return {
        "enabled": bool(hf.get("enabled", True)),
        "repo": hf.get("repo", ""),
        "revision": hf.get("revision", "main"),
        "private": bool(hf.get("private", False)),
        "token_ref": hf.get("auth_token_ref", "hf_token"),
        "token_stored": current.token_stored,
        "logged_in": current.logged_in,
        "username": current.username,
        "mirrors": [m for m in hf.get("mirrors", []) or []],
        "upload_endpoint": "https://huggingface.co",
    }
