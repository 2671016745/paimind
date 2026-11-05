"""Download-source endpoints: list, update, per-mirror connectivity test."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from core.config import (
    DEFAULT_MIRRORS,
    TEMPLATE_VARIABLES,
    validate_url_template,
)
from core.download import build_payload, list_mirrors, test_mirror
from core.errors import AppError, ErrorCode
from core.schemas import DownloadSourcesPayload, Mirror
from server.deps import get_ctx, rate_limit

router = APIRouter(prefix="/v1", tags=["download-sources"])


@router.get("/download-sources", response_model=DownloadSourcesPayload)
def read_sources(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    return build_payload(ctx.cfg).model_dump()


@router.put("/download-sources", response_model=DownloadSourcesPayload)
def update_sources(payload: Dict[str, Any], request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Add / edit / disable mirrors and change defaults.

    ``security``-relevant fields (token values) are never accepted here.
    """

    ctx = get_ctx()
    dl = ctx.cfg.section("download")

    if "default_provider" in payload:
        provider = str(payload["default_provider"])
        if provider not in ("github", "huggingface", "custom"):
            raise AppError(ErrorCode.INVALID_REQUEST, f"未知 provider：{provider}")
        ctx.cfg.set("download.default_provider", provider)

    if "default_mirror_by_provider" in payload:
        current = dict(dl.get("default_mirror_by_provider", {}) or {})
        current.update({k: str(v) for k, v in payload["default_mirror_by_provider"].items()})
        ctx.cfg.set("download.default_mirror_by_provider", current)

    if "fallback_order" in payload:
        ctx.cfg.set("download.fallback_order", {k: [str(x) for x in v]
                                                for k, v in payload["fallback_order"].items()})

    if "custom_mirrors" in payload:
        _replace_custom_mirrors(ctx, payload["custom_mirrors"])

    if "timeout_seconds" in payload:
        ctx.cfg.set("download.timeout_seconds", float(payload["timeout_seconds"]))
    if "verify_sha256" in payload:
        ctx.cfg.set("download.verify_sha256", bool(payload["verify_sha256"]))
    if "resume_support" in payload:
        ctx.cfg.set("download.resume_support", bool(payload["resume_support"]))
    if "allow_http_mirrors" in payload:
        ctx.cfg.set("download.allow_http_mirrors", bool(payload["allow_http_mirrors"]))

    return build_payload(ctx.cfg).model_dump()


def _replace_custom_mirrors(ctx, mirrors) -> None:
    cleaned = []
    for raw in mirrors or []:
        if not isinstance(raw, dict):
            continue
        template = str(raw.get("url_template", ""))
        problems = validate_url_template(template)
        if problems:
            raise AppError(
                ErrorCode.INVALID_URL_TEMPLATE,
                f"镜像 {raw.get('name', '?')} 的 URL 模板无效：{'; '.join(problems)}",
                {"template": template, "problems": problems},
            )
        if raw.get("kind") == "custom" and "trust_ack" not in raw:
            raise AppError(
                ErrorCode.INVALID_URL_TEMPLATE,
                f"自定义镜像 {raw.get('name', '?')} 需要先确认信任提示",
                {"template": template},
            )
        cleaned.append({
            "id": str(raw.get("id") or f"custom-{len(cleaned)}"),
            "name": str(raw.get("name", "")),
            "url_template": template,
            "auth_required": bool(raw.get("auth_required", False)),
            "enabled": bool(raw.get("enabled", True)),
            "priority": int(raw.get("priority", 500)),
            "kind": "custom",
            "provider": str(raw.get("provider", "custom")),
        })
    ctx.cfg.set("download.custom_mirrors", cleaned)


@router.post("/download-sources/{provider}/{mirror_id}/test")
def test_one_mirror(provider: str, mirror_id: str, request: Request,
                    tier: str = "L0", filename: str = "model.onnx",
                    _: None = Depends(rate_limit)) -> Dict[str, Any]:
    ctx = get_ctx()
    result = test_mirror(provider, mirror_id, ctx.cfg, tier, filename)
    return result.model_dump()


@router.get("/download-sources/template-variables")
def template_variables(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    return {"variables": TEMPLATE_VARIABLES, "builtin_mirrors": DEFAULT_MIRRORS}


@router.post("/download-sources/restore-defaults")
def restore_defaults(request: Request, _: None = Depends(rate_limit)) -> Dict[str, Any]:
    """Drop every custom mirror and reset the built-ins."""

    ctx = get_ctx()
    ctx.cfg.set("download.custom_mirrors", [])
    ctx.cfg.set("download.default_provider", "github")
    ctx.cfg.set("download.default_mirror_by_provider",
                {"github": "official", "huggingface": "official"})
    ctx.cfg.set("download.fallback_order",
                {"github": ["official", "ghproxy", "ghfast"],
                 "huggingface": ["official", "hf-mirror"]})
    return build_payload(ctx.cfg).model_dump()
