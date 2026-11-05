"""Tier definitions, tri-state availability rules and the recommendation algorithm.

The rules implemented here are exactly the tables in ``docs/tiers.md``:

* **builtin** — memory floor, disk floor (skipped once downloaded), 64-bit CPU,
  AVX2 on x86 for L1+; L3 without a GPU or with < 6 GB VRAM is *warning*, never
  disabled.
* **ollama / llamacpp** — engine reachable *and* model pulled, then the same
  memory ladder; L3 without a GPU is warning, with >= 6 GB VRAM ready.
* **vllm** — NVIDIA GPU mandatory (otherwise disabled); L3 AWQ needs >= 8 GB for
  ready, 4–8 GB is warning, < 4 GB disabled.
* **docker** — container healthy *and* the host satisfies the underlying engine's
  rules.

Short-circuit order matters: a hard failure (no GPU for vLLM, engine down) is
evaluated before the memory ladder so the user sees the *real* reason.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .config import Config, get_config
from .schemas import (
    DeployMode,
    EngineKind,
    EngineState,
    EngineStatus,
    HardwareInfo,
    TierId,
    TierInfo,
    TierRequirements,
    TierState,
    TierStatus,
)

# vLLM per-tier VRAM thresholds (AWQ INT4), in GB
_VLLM_VRAM_READY_GB: Dict[str, float] = {"L0": 1.5, "L1": 2.0, "L2": 3.0, "L3": 8.0}
_VLLM_VRAM_WARN_FLOOR_GB: Dict[str, float] = {"L0": 1.0, "L1": 1.5, "L2": 2.0, "L3": 4.0}

# Tier ordering by parameter count (used for "largest available tier")
TIER_ORDER: List[str] = ["L0", "L1", "L2", "L3"]

IS_64BIT_ARCHS = {"amd64", "x86_64", "arm64", "aarch64", "armv8l", "ppc64le", "riscv64"}


def _is_64bit(arch: str) -> bool:
    return arch.lower() in IS_64BIT_ARCHS


def tier_requirements(cfg: Config, tier_id: str) -> TierRequirements:
    raw = cfg.tier(tier_id)
    return TierRequirements(
        min_available_memory_gb=float(raw.get("min_available_memory_gb", 0)),
        min_disk_mb=float(raw.get("min_disk_mb", 0)),
        cpu_x86_avx2=bool(raw.get("cpu_x86_avx2", False)),
        gpu_vram_gb=float(raw.get("gpu_vram_gb", 0.0)),
        gpu_optional=bool(raw.get("gpu_optional", True)),
    )


def build_tier_info(cfg: Config, tier_id: str, downloaded: bool, files: List[str]) -> TierInfo:
    raw = cfg.tier(tier_id)
    return TierInfo(
        tier=TierId(tier_id),
        params=str(raw.get("params", "")),
        disk_mb=float(raw.get("disk_mb", 0)),
        peak_memory_gb=float(raw.get("peak_memory_gb", 0)),
        display_name=str(raw.get("display_name", tier_id)),
        suggestion=str(raw.get("suggestion", "")),
        requirements=tier_requirements(cfg, tier_id),
        downloaded=downloaded,
        files_present=files,
    )


# --------------------------------------------------------------------------
# builtin
# --------------------------------------------------------------------------


def _eval_builtin(
    cfg: Config,
    tier_id: str,
    hw: HardwareInfo,
    downloaded: bool,
) -> Tuple[TierState, Optional[str], Optional[str]]:
    """Returns (state, unavailable_reason, warning_reason)."""

    req = tier_requirements(cfg, tier_id)

    if not _is_64bit(hw.cpu.architecture):
        return TierState.DISABLED, "需要 64 位 CPU，当前为 32 位", None

    if req.cpu_x86_avx2 and hw.cpu.architecture.lower() in ("amd64", "x86_64"):
        if not hw.cpu.has_avx2:
            return (
                TierState.DISABLED,
                "需要支持 AVX2 指令集的 CPU（当前 CPU 未检测到 AVX2）",
                None,
            )

    if hw.memory.available_bytes and hw.memory.available_gb < req.min_available_memory_gb:
        return (
            TierState.DISABLED,
            f"需要可用内存 ≥ {req.min_available_memory_gb:g} GB，当前仅 {hw.memory.available_gb:.1f} GB",
            None,
        )

    if not downloaded and hw.disk.free_gb * 1024 < req.min_disk_mb:
        return (
            TierState.DISABLED,
            f"需要磁盘剩余 ≥ {req.min_disk_mb:g} MB，当前仅 {hw.disk.free_gb * 1024:.0f} MB",
            None,
        )

    # L3: no GPU or small VRAM is a warning, never a hard block
    if tier_id == "L3":
        gpu = next((g for g in hw.gpus if g.driver_ok and g.vram_bytes > 0), None)
        if gpu is None:
            return (
                TierState.WARNING,
                None,
                "未检测到可用 GPU，L3 将使用 CPU 推理，速度较慢",
            )
        if gpu.vram_gb < req.gpu_vram_gb:
            return (
                TierState.WARNING,
                None,
                f"显存 {gpu.vram_gb:.1f} GB 小于建议的 {req.gpu_vram_gb:g} GB，将使用 CPU 推理",
            )

    if tier_id == "L2":
        gpu = next((g for g in hw.gpus if g.driver_ok and g.vram_bytes > 0), None)
        if gpu is not None and gpu.vram_gb < 4.0:
            return TierState.READY, None, "将用 CPU 推理"

    return TierState.READY, None, None


# --------------------------------------------------------------------------
# External engines
# --------------------------------------------------------------------------


def _engine_block_reason(engine: EngineStatus) -> Optional[str]:
    """Return the reason a tier is disabled because of its engine, else None."""

    if not engine.enabled:
        return f"{engine.engine.value} 模式已在配置中禁用"
    if engine.state == EngineState.NOT_INSTALLED:
        return f"{engine.engine.value} 未安装"
    if not engine.reachable:
        label = {"ollama": "Ollama", "vllm": "vLLM", "llamacpp": "llama-server"}.get(
            engine.engine.value, engine.engine.value
        )
        return f"{label} 未启动"
    if engine.state == EngineState.MODEL_MISSING or not engine.models:
        return None  # handled per-tier below
    return None


def _model_missing_reason(engine: EngineStatus, tier_id: str, cfg: Config) -> Optional[str]:
    if engine.reachable and engine.state != EngineState.MODEL_MISSING:
        return None
    if not engine.reachable:
        return None
    model_map = cfg.engine(engine.engine.value).get("model_map", {})
    wanted = model_map.get(tier_id, "")
    if wanted:
        return f"{engine.engine.value} 中未拉取模型 {wanted}，请先拉取后重试"
    return f"{engine.engine.value} 中未找到 {tier_id} 对应模型"


def _eval_ollama_like(
    cfg: Config,
    tier_id: str,
    hw: HardwareInfo,
    engine: EngineStatus,
    mode: DeployMode,
) -> Tuple[TierState, Optional[str], Optional[str]]:
    label = "Ollama" if mode == DeployMode.OLLAMA else "llama-server"

    if not engine.enabled:
        return TierState.DISABLED, f"{label} 模式已在配置中禁用", None
    if engine.state == EngineState.NOT_INSTALLED:
        return TierState.DISABLED, f"{label} 未安装", None
    if not engine.reachable:
        return TierState.DISABLED, f"{label} 未启动", None

    reason = _model_missing_reason(engine, tier_id, cfg)
    if reason:
        return TierState.DISABLED, reason, None

    req = tier_requirements(cfg, tier_id)
    if hw.memory.available_bytes and hw.memory.available_gb < req.min_available_memory_gb:
        return (
            TierState.DISABLED,
            f"需要可用内存 ≥ {req.min_available_memory_gb:g} GB，当前仅 {hw.memory.available_gb:.1f} GB",
            None,
        )

    if tier_id == "L3":
        gpu = next((g for g in hw.gpus if g.driver_ok and g.vram_bytes > 0), None)
        if gpu is None:
            return TierState.WARNING, None, "未检测到可用 GPU，L3 将使用 CPU 推理，速度较慢"
        if gpu.vram_gb < req.gpu_vram_gb:
            return (
                TierState.WARNING,
                None,
                f"显存 {gpu.vram_gb:.1f} GB 小于建议的 {req.gpu_vram_gb:g} GB，将使用 CPU 推理",
            )

    return TierState.READY, None, None


def _eval_vllm(
    cfg: Config,
    tier_id: str,
    hw: HardwareInfo,
    engine: EngineStatus,
) -> Tuple[TierState, Optional[str], Optional[str]]:
    gpu = next((g for g in hw.gpus if g.vendor == "nvidia" and g.driver_ok), None)
    if gpu is None:
        return TierState.DISABLED, "vLLM 需要 NVIDIA GPU，当前未检测到", None

    if not engine.enabled:
        return TierState.DISABLED, "vLLM 模式已在配置中禁用", None
    if not engine.reachable:
        return TierState.DISABLED, "vLLM 服务未启动", None

    reason = _model_missing_reason(engine, tier_id, cfg)
    if reason:
        return TierState.DISABLED, reason, None

    ready_vram = _VLLM_VRAM_READY_GB.get(tier_id, 8.0)
    warn_floor = _VLLM_VRAM_WARN_FLOOR_GB.get(tier_id, 4.0)

    if gpu.vram_gb >= ready_vram:
        return TierState.READY, None, None
    if gpu.vram_gb >= warn_floor:
        return (
            TierState.WARNING,
            None,
            f"显存 {gpu.vram_gb:.1f} GB，建议调小 max-model-len 以适配",
        )
    return (
        TierState.DISABLED,
        f"vLLM {tier_id} 需要显存 ≥ {ready_vram:g} GB，当前仅 {gpu.vram_gb:.1f} GB",
        None,
    )


def _eval_docker(
    cfg: Config,
    tier_id: str,
    hw: HardwareInfo,
    engine: EngineStatus,
    container_ok: bool,
) -> Tuple[TierState, Optional[str], Optional[str]]:
    if not container_ok:
        return TierState.DISABLED, "Docker 容器未运行或健康检查未通过", None

    # Host hardware governs; the container just isolates the engine.
    inner_mode = DeployMode.OLLAMA if engine.engine == EngineKind.OLLAMA else DeployMode.VLLM
    return _eval_ollama_like(cfg, tier_id, hw, engine, inner_mode) if inner_mode == DeployMode.OLLAMA \
        else _eval_vllm(cfg, tier_id, hw, engine)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def evaluate_tier(
    tier_id: str,
    mode: DeployMode,
    hw: HardwareInfo,
    cfg: Optional[Config] = None,
    engine_status: Optional[EngineStatus] = None,
    downloaded: bool = False,
    container_ok: bool = False,
) -> TierStatus:
    """Evaluate one (tier, deploy mode) pair into the tri-state."""

    cfg = cfg or get_config()
    state, unavailable, warning = TierState.READY, None, None

    if mode == DeployMode.BUILTIN:
        state, unavailable, warning = _eval_builtin(cfg, tier_id, hw, downloaded)
    elif mode in (DeployMode.OLLAMA, DeployMode.LLAMACPP):
        engine = engine_status or EngineStatus(
            engine=EngineKind.OLLAMA if mode == DeployMode.OLLAMA else EngineKind.LLAMACPP,
            state=EngineState.NOT_INSTALLED, base_url="", enabled=False,
        )
        state, unavailable, warning = _eval_ollama_like(cfg, tier_id, hw, engine, mode)
    elif mode == DeployMode.VLLM:
        engine = engine_status or EngineStatus(
            engine=EngineKind.VLLM, state=EngineState.NOT_INSTALLED, base_url="", enabled=False,
        )
        state, unavailable, warning = _eval_vllm(cfg, tier_id, hw, engine)
    elif mode == DeployMode.DOCKER:
        engine = engine_status or EngineStatus(
            engine=EngineKind.OLLAMA, state=EngineState.NOT_INSTALLED, base_url="", enabled=False,
        )
        state, unavailable, warning = _eval_docker(cfg, tier_id, hw, engine, container_ok)

    if unavailable is None and warning is None and state == TierState.READY:
        state = TierState.READY

    return TierStatus(
        tier=TierId(tier_id),
        deploy_mode=mode,
        state=state,
        downloaded=downloaded,
        unavailable_reason=unavailable,
        warning_reason=warning,
        requirements=tier_requirements(cfg, tier_id),
        backend_hint=_backend_hint(mode, hw),
        engine_state=engine_status.state if engine_status else None,
        engine_model_ready=bool(engine_status.models) if engine_status else None,
        engine_model=engine_status.models[0] if engine_status and engine_status.models else None,
    )


def _backend_hint(mode: DeployMode, hw: HardwareInfo) -> str:
    if mode == DeployMode.BUILTIN:
        for gpu in hw.gpus:
            if gpu.driver_ok and gpu.compute_backend in ("cuda", "metal", "coreml"):
                return f"gpu:{gpu.compute_backend}"
        return "cpu"
    return mode.value


def pick_engine(
    engine_statuses: Optional[Dict[EngineKind, EngineStatus]], mode: DeployMode
) -> Optional[EngineStatus]:
    """The engine status relevant to *mode* (builtin needs none)."""

    if mode == DeployMode.BUILTIN:
        return None
    if not engine_statuses:
        return None
    kind = {
        DeployMode.OLLAMA: EngineKind.OLLAMA,
        DeployMode.VLLM: EngineKind.VLLM,
        DeployMode.LLAMACPP: EngineKind.LLAMACPP,
        DeployMode.DOCKER: EngineKind.OLLAMA,
    }[mode]
    return engine_statuses.get(kind)


def evaluate_tier_with_engines(
    tier_id: str,
    mode: DeployMode,
    hw: HardwareInfo,
    cfg: Optional[Config] = None,
    engine_statuses: Optional[Dict[EngineKind, EngineStatus]] = None,
    downloaded: Optional[Dict[str, bool]] = None,
    container_ok: bool = False,
) -> TierStatus:
    """Convenience wrapper: pick the right engine from the full status map."""

    if isinstance(downloaded, dict):
        tier_downloaded = bool(downloaded.get(tier_id, False))
    else:
        tier_downloaded = bool(downloaded)

    return evaluate_tier(
        tier_id, mode, hw, cfg, pick_engine(engine_statuses, mode),
        downloaded=tier_downloaded, container_ok=container_ok,
    )


def evaluate_all(
    modes: Sequence[DeployMode],
    hw: HardwareInfo,
    cfg: Optional[Config] = None,
    engine_statuses: Optional[Dict[EngineKind, EngineStatus]] = None,
    downloaded: Optional[Dict[str, bool]] = None,
    container_ok: bool = False,
) -> List[TierStatus]:
    cfg = cfg or get_config()
    engine_statuses = engine_statuses or {}
    downloaded = downloaded or {}
    out: List[TierStatus] = []
    for mode in modes:
        for tier_id in TIER_ORDER:
            engine = engine_statuses.get(
                EngineKind.OLLAMA if mode == DeployMode.OLLAMA else
                EngineKind.LLAMACPP if mode == DeployMode.LLAMACPP else
                EngineKind.VLLM if mode == DeployMode.VLLM else EngineKind.DOCKER
            ) if mode != DeployMode.BUILTIN else None
            out.append(evaluate_tier(
                tier_id, mode, hw, cfg, engine,
                downloaded=downloaded.get(tier_id, False),
                container_ok=container_ok,
            ))
    return out


# --------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------


def recommend_tier(
    hw: HardwareInfo,
    cfg: Optional[Config] = None,
    engine_statuses: Optional[Dict[EngineKind, EngineStatus]] = None,
    downloaded: Optional[Dict[str, bool]] = None,
    first_install: bool = False,
    prefer_mode: DeployMode = DeployMode.BUILTIN,
    container_ok: bool = False,
) -> Dict[str, object]:
    """Pick the best tier for this machine and explain why in one sentence.

    Algorithm (see ``docs/tiers.md`` §3.5):

    1. Evaluate every (tier, mode) pair, keep ``ready`` and ``warning``.
    2. Take the largest parameter count among the survivors.
    3. If the winner is only a ``warning``, flag the risk.
    4. On a first install, recommend ``min(winner, L0)`` so the user gets a
       working model immediately and downloads the rest later.
    5. Build a one-line reason from the hardware facts.
    """

    cfg = cfg or get_config()
    engine_statuses = engine_statuses or {}
    downloaded = downloaded or {}

    statuses = evaluate_all([prefer_mode], hw, cfg, engine_statuses, downloaded, container_ok)
    survivors = [s for s in statuses if s.state in (TierState.READY, TierState.WARNING)]
    if not survivors:
        # Fall back to L0 with an honest explanation rather than recommending nothing.
        reason = "当前硬件不满足任何档位，建议升级内存或启用外部引擎"
        return {"tier": "L0", "state": TierState.DISABLED.value, "reason": reason,
                "risk": True, "first_install_tier": "L0"}

    winner = max(survivors, key=lambda s: TIER_ORDER.index(s.tier.value))
    risk = winner.state == TierState.WARNING

    reason = _reason_line(hw, winner.tier.value, cfg, engine_statuses)

    first_install_tier = "L0" if first_install else winner.tier.value
    if first_install and winner.tier.value != "L0":
        reason += "；首次安装先下载 L0，可立即使用"

    return {
        "tier": winner.tier.value,
        "state": winner.state.value,
        "reason": reason,
        "risk": risk,
        "first_install_tier": first_install_tier,
        "deploy_mode": prefer_mode.value,
        "warning_reason": winner.warning_reason,
    }


def _reason_line(
    hw: HardwareInfo,
    tier_id: str,
    cfg: Config,
    engine_statuses: Dict[EngineKind, EngineStatus],
) -> str:
    parts = [f"可用内存 {hw.memory.available_gb:.1f}GB"]
    if hw.gpus:
        gpu = hw.gpus[0]
        parts.append(gpu.name or "集显")
    elif hw.cpu.architecture:
        parts.append(f"{hw.cpu.architecture} CPU")

    reason = f"{'、'.join(parts)}，推荐 {tier_id}"

    ollama = engine_statuses.get(EngineKind.OLLAMA)
    if ollama and ollama.reachable and ollama.models:
        reason += "；Ollama 已就绪，可优先使用"

    vllm = engine_statuses.get(EngineKind.VLLM)
    if vllm and vllm.reachable and vllm.models:
        reason += "；vLLM 已就绪，服务端场景可优先使用"

    return reason
