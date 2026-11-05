"""Backend registry and the unified degradation chain.

``resolve_backend`` implements the chain from ``docs/deploy.md`` §10.3:

1. the user's chosen deploy mode is reachable and its model is present -> use it;
2. that mode is unreachable -> fall back to ``builtin`` *if* the tier's ONNX
   model is already downloaded, and record a note for the status bar;
3. ``builtin`` is unavailable too -> raise a precise error (the UI then offers
   the download / engine-start guidance);
4. never crash, never silently pretend an engine worked.

The returned :class:`Resolution` always carries ``actual_mode`` and ``notes`` so
the caller can show *what really happened* rather than what was asked for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Config, get_config
from ..errors import AppError, ErrorCode
from ..hardware import detect_gpus
from ..schemas import BackendKind, DeployMode, EngineKind, EngineState, EngineStatus, TierId
from .base import InferenceBackend
from .builtin import BuiltinOnnxBackend
from .openai_client import LlamaCppBackend, OllamaBackend, OpenAIClientBackend, VllmBackend

__all__ = [
    "Resolution",
    "resolve_backend",
    "build_backend",
    "engine_backend_class",
    "describe_backend",
]


@dataclass
class Resolution:
    backend: InferenceBackend
    requested_mode: DeployMode
    actual_mode: DeployMode
    tier: str
    notes: List[str] = field(default_factory=list)
    degraded: bool = False

    @property
    def backend_kind(self) -> BackendKind:
        return self.backend.kind()


def engine_backend_class(mode: DeployMode):
    return {
        DeployMode.OLLAMA: OllamaBackend,
        DeployMode.VLLM: VllmBackend,
        DeployMode.LLAMACPP: LlamaCppBackend,
    }.get(mode)


def build_backend(
    tier_id: str,
    mode: DeployMode,
    cfg: Optional[Config] = None,
    model_override: Optional[str] = None,
) -> InferenceBackend:
    """Construct a backend for *tier_id* under *mode* (no reachability checks)."""

    cfg = cfg or get_config()

    if mode == DeployMode.BUILTIN:
        model_dir = os.path.join(str(cfg.models_dir), tier_id)
        return BuiltinOnnxBackend(model_dir, cfg)

    if mode == DeployMode.DOCKER:
        # Docker mode talks to the same OpenAI-compatible endpoint; the container
        # is just where the engine lives.
        inner = DeployMode.OLLAMA  # default; overridden by the engine status below
        return _build_external(inner, tier_id, cfg, model_override)

    return _build_external(mode, tier_id, cfg, model_override)


def _build_external(
    mode: DeployMode,
    tier_id: str,
    cfg: Config,
    model_override: Optional[str],
) -> InferenceBackend:
    cls = engine_backend_class(mode)
    if cls is None:
        raise AppError(ErrorCode.ENGINE_UNSUPPORTED, f"不支持的部署模式：{mode.value}")
    engine_cfg = cfg.engine(mode.value)
    model_map = engine_cfg.get("model_map", {})
    model = model_override or model_map.get(tier_id)
    if not model:
        raise AppError(
            ErrorCode.ENGINE_MODEL_NOT_READY,
            f"{mode.value} 未配置 {tier_id} 对应模型",
            {"engine": mode.value, "tier": tier_id},
        )
    return cls(engine_cfg, model)


def resolve_backend(
    tier_id: str,
    mode: DeployMode,
    cfg: Optional[Config] = None,
    engine_statuses: Optional[Dict[EngineKind, EngineStatus]] = None,
    downloaded: Optional[Dict[str, bool]] = None,
    container_ok: bool = False,
    model_override: Optional[str] = None,
) -> Resolution:
    """Pick the best available backend, applying the degradation chain."""

    cfg = cfg or get_config()
    engine_statuses = engine_statuses or {}
    downloaded = downloaded or {}

    if mode == DeployMode.DOCKER:
        return _resolve_docker(tier_id, cfg, engine_statuses, downloaded, container_ok, model_override)

    if mode == DeployMode.BUILTIN:
        backend = build_backend(tier_id, DeployMode.BUILTIN, cfg, model_override)
        if backend.ready():
            return Resolution(backend, mode, mode, tier_id)
        raise AppError(
            ErrorCode.MODEL_NOT_READY,
            f"{tier_id} 的内置 ONNX 模型未下载，无法加载",
            {"tier": tier_id, "path": os.path.join(str(cfg.models_dir), tier_id)},
        )

    engine_kind = {
        DeployMode.OLLAMA: EngineKind.OLLAMA,
        DeployMode.VLLM: EngineKind.VLLM,
        DeployMode.LLAMACPP: EngineKind.LLAMACPP,
    }[mode]
    status = engine_statuses.get(engine_kind)

    if status is not None and status.reachable and status.state != EngineState.MODEL_MISSING:
        backend = build_backend(tier_id, mode, cfg, model_override)
        return Resolution(backend, mode, mode, tier_id)

    # (2) fall back to builtin when the ONNX file is already on disk
    if downloaded.get(tier_id):
        backend = build_backend(tier_id, DeployMode.BUILTIN, cfg, model_override)
        if backend.ready():
            reason = _engine_down_reason(status)
            return Resolution(
                backend, mode, DeployMode.BUILTIN, tier_id,
                notes=[f"{reason}，已回退内置推理"],
                degraded=True,
            )

    # (3) nothing works -> precise error, the UI turns it into guidance
    if status is None:
        raise AppError(
            ErrorCode.ENGINE_UNSUPPORTED,
            f"未知引擎：{mode.value}",
            {"engine": mode.value},
        )
    if not status.enabled:
        raise AppError(
            ErrorCode.ENGINE_UNSUPPORTED,
            f"{mode.value} 模式已在配置中禁用",
            {"engine": mode.value},
        )
    if not status.reachable:
        raise AppError(
            ErrorCode.ENGINE_UNAVAILABLE,
            f"{mode.value} 不可达：{status.base_url}",
            {"engine": mode.value, "url": status.base_url},
        )
    if status.state == EngineState.MODEL_MISSING:
        raise AppError(
            ErrorCode.ENGINE_MODEL_NOT_READY,
            f"{mode.value} 中未找到 {tier_id} 对应模型",
            {"engine": mode.value, "tier": tier_id},
        )
    raise AppError(
        ErrorCode.ENGINE_UNAVAILABLE,
        f"{mode.value} 不可用",
        {"engine": mode.value},
    )


def _resolve_docker(
    tier_id: str,
    cfg: Config,
    engine_statuses: Dict[EngineKind, EngineStatus],
    downloaded: Dict[str, bool],
    container_ok: bool,
    model_override: Optional[str],
) -> Resolution:
    if not container_ok:
        raise AppError(
            ErrorCode.ENGINE_UNAVAILABLE,
            "Docker 容器未运行或健康检查未通过",
            {"engine": "docker"},
        )
    ollama = engine_statuses.get(EngineKind.OLLAMA)
    if ollama and ollama.reachable:
        backend = _build_external(DeployMode.OLLAMA, tier_id, cfg, model_override)
        return Resolution(backend, DeployMode.DOCKER, DeployMode.OLLAMA, tier_id,
                          notes=["通过 Docker 容器内的 Ollama 推理"])
    vllm = engine_statuses.get(EngineKind.VLLM)
    if vllm and vllm.reachable:
        backend = _build_external(DeployMode.VLLM, tier_id, cfg, model_override)
        return Resolution(backend, DeployMode.DOCKER, DeployMode.VLLM, tier_id,
                          notes=["通过 Docker 容器内的 vLLM 推理"])
    if downloaded.get(tier_id):
        backend = build_backend(tier_id, DeployMode.BUILTIN, cfg, model_override)
        if backend.ready():
            return Resolution(backend, DeployMode.DOCKER, DeployMode.BUILTIN, tier_id,
                              notes=["容器引擎不可达，已回退内置推理"], degraded=True)
    raise AppError(
        ErrorCode.ENGINE_UNAVAILABLE,
        "Docker 容器健康但未发现可用的推理引擎",
        {"engine": "docker"},
    )


def _engine_down_reason(status: Optional[EngineStatus]) -> str:
    if status is None:
        return "外部引擎不可达"
    label = {"ollama": "Ollama", "vllm": "vLLM", "llamacpp": "llama-server"}.get(
        status.engine.value, status.engine.value
    )
    if status.state == EngineState.NOT_INSTALLED:
        return f"{label} 未安装"
    if not status.reachable:
        return f"{label} 不可达"
    return f"{label} 未就绪"


def describe_backend(backend: InferenceBackend) -> Dict[str, Any]:
    return {
        "name": backend.name,
        "engine_key": backend.engine_key,
        "kind": backend.kind().value,
        "requires_model_file": backend.requires_model_file,
        "gpus": [
            {"name": g.name, "vram_gb": g.vram_gb, "backend": g.compute_backend}
            for g in detect_gpus()
        ],
    }
