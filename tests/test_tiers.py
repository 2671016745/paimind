"""Tier tri-state rules against synthetic hardware — the acceptance matrix."""

from __future__ import annotations

import pytest

from core.schemas import (
    CpuInfo,
    DeployMode,
    DiskInfo,
    EngineKind,
    EngineState,
    EngineStatus,
    GpuInfo,
    HardwareInfo,
    MemoryInfo,
)


def make_hw(
    *,
    avail_gb: float = 32.0,
    total_gb: float = 32.0,
    arch: str = "amd64",
    avx2: bool = True,
    free_gb: float = 500.0,
    gpus=None,
) -> HardwareInfo:
    return HardwareInfo(
        platform="windows", platform_release="10", hostname="test",
        memory=MemoryInfo(total_bytes=int(total_gb * 1024 ** 3),
                          available_bytes=int(avail_gb * 1024 ** 3),
                          total_gb=total_gb, available_gb=avail_gb),
        cpu=CpuInfo(architecture=arch, cores_physical=8, cores_logical=16,
                    flags=["avx2"] if avx2 else [], has_avx2=avx2),
        disk=DiskInfo(path="C:/", total_bytes=1000 * 1024 ** 3,
                      free_bytes=int(free_gb * 1024 ** 3), free_gb=free_gb),
        gpus=gpus or [],
    )


RTX_3060 = [GpuInfo(name="RTX 3060", vram_bytes=12 * 1024 ** 3, vram_gb=12.0,
                    vendor="nvidia", driver_ok=True, compute_backend="cuda")]
QUADRO_4G = [GpuInfo(name="Quadro T1000", vram_bytes=4 * 1024 ** 3, vram_gb=4.0,
                     vendor="nvidia", driver_ok=True, compute_backend="cuda")]


def test_builtin_all_ready_on_strong_machine(cfg):
    from core.tiers import evaluate_all

    hw = make_hw(avail_gb=32.0, free_gb=500.0, gpus=RTX_3060)
    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert states == {"L0": "ready", "L1": "ready", "L2": "ready", "L3": "ready"}


def test_builtin_l3_warning_without_gpu(cfg):
    from core.tiers import evaluate_all

    hw = make_hw(avail_gb=16.0, gpus=[])
    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert states["L3"] == "warning", "无 GPU 时 L3 应为 warning 而非 disabled"


def test_builtin_4gb_machine_disables_l2_and_l3(cfg):
    """验收：4GB 内存机器上 L2/L3 自动置灰。"""

    from core.tiers import evaluate_all

    hw = make_hw(avail_gb=4.0, total_gb=4.0, free_gb=10.0)
    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert states["L0"] == "ready"
    assert states["L1"] == "ready"
    assert states["L2"] == "disabled"
    assert states["L3"] == "disabled"


def test_2gb_phone_loads_l0_only(cfg):
    """验收：2GB 内存手机能加载 L0。"""

    from core.tiers import evaluate_all

    hw = make_hw(avail_gb=2.0, total_gb=2.0, free_gb=1.0, arch="aarch64", avx2=False)
    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert states["L0"] == "ready", "L0 只需 1.5GB 可用内存"
    assert states["L1"] == "disabled"


def test_32bit_cpu_disabled(cfg):
    from core.tiers import evaluate_all

    hw = make_hw(arch="i386", avail_gb=8.0)
    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert all(v == "disabled" for v in states.values())


def test_x86_without_avx2_disables_l1_plus(cfg):
    hw = make_hw(avail_gb=32.0, arch="amd64", avx2=False)
    from core.tiers import evaluate_all

    states = {s.tier.value: s.state.value for s in evaluate_all([DeployMode.BUILTIN], hw, cfg)}
    assert states["L0"] == "ready"
    assert states["L1"] == "disabled"
    assert "AVX2" in next(s.unavailable_reason for s in
                         evaluate_all([DeployMode.BUILTIN], hw, cfg) if s.tier.value == "L1")


def test_disk_floor_enforced_only_when_not_downloaded(cfg):
    from core.tiers import evaluate_tier

    hw = make_hw(avail_gb=32.0, free_gb=0.2)
    assert evaluate_tier("L1", DeployMode.BUILTIN, hw, cfg).state.value == "disabled"
    assert evaluate_tier("L1", DeployMode.BUILTIN, hw, cfg, downloaded=True).state.value == "ready"


def test_vllm_disabled_without_nvidia_gpu(cfg):
    from core.tiers import evaluate_all

    hw = make_hw(gpus=[GpuInfo(name="Intel UHD", vram_bytes=1 * 1024 ** 3, vram_gb=1.0,
                              vendor="intel", driver_ok=True, compute_backend="directml")])
    statuses = evaluate_all([DeployMode.VLLM], hw, cfg)
    assert all(s.state.value == "disabled" for s in statuses)
    assert all("NVIDIA GPU" in (s.unavailable_reason or "") for s in statuses)


def test_vllm_l3_bands(cfg):
    """验收：vLLM L3 AWQ 显存 4–8GB → warning，<4GB → disabled。"""

    from core.tiers import evaluate_all

    ready = EngineStatus(engine=EngineKind.VLLM, state=EngineState.READY, base_url="x",
                         enabled=True, reachable=True, models=["m"])
    states_4g = {s.tier.value: s.state.value for s in
                 evaluate_all([DeployMode.VLLM], make_hw(gpus=QUADRO_4G), cfg,
                              {EngineKind.VLLM: ready})}
    assert states_4g["L3"] == "warning", "4GB 显存应为 warning"

    small = [GpuInfo(name="GT 1030", vram_bytes=2 * 1024 ** 3, vram_gb=2.0,
                     vendor="nvidia", driver_ok=True, compute_backend="cuda")]
    states_2g = {s.tier.value: s.state.value for s in
                 evaluate_all([DeployMode.VLLM], make_hw(gpus=small), cfg,
                              {EngineKind.VLLM: ready})}
    assert states_2g["L3"] == "disabled", "2GB 显存应为 disabled"


def test_ollama_disabled_when_not_running(cfg):
    """验收：ollama 未启动时所有 ollama 模式档置 disabled，原因「Ollama 未启动」。"""

    from core.tiers import evaluate_all

    down = EngineStatus(engine=EngineKind.OLLAMA, state=EngineState.UNREACHABLE,
                        base_url="http://localhost:11434", enabled=True, reachable=False)
    statuses = evaluate_all([DeployMode.OLLAMA], make_hw(avail_gb=32.0), cfg,
                            {EngineKind.OLLAMA: down})
    assert all(s.state.value == "disabled" for s in statuses)
    assert all((s.unavailable_reason or "").startswith("Ollama 未启动") for s in statuses)


def test_ollama_l3_warning_cpu_only(cfg):
    from core.tiers import evaluate_all

    ready = EngineStatus(engine=EngineKind.OLLAMA, state=EngineState.READY, base_url="x",
                         enabled=True, reachable=True, models=["qwen2.5:7b"])
    states = {s.tier.value: s.state.value for s in
              evaluate_all([DeployMode.OLLAMA], make_hw(avail_gb=32.0, gpus=[]), cfg,
                           {EngineKind.OLLAMA: ready})}
    assert states["L3"] == "warning"
    assert states["L0"] == "ready"


def test_recommendation_picks_largest_ready(cfg):
    from core.tiers import recommend_tier

    hw = make_hw(avail_gb=8.0, gpus=[])
    rec = recommend_tier(hw, cfg)
    assert rec["tier"] == "L2", "8GB 可用内存应推荐 L2"
    assert "可用内存" in rec["reason"]


def test_recommendation_first_install_prefers_l0(cfg):
    from core.tiers import recommend_tier

    hw = make_hw(avail_gb=32.0, gpus=RTX_3060)
    rec = recommend_tier(hw, cfg, first_install=True)
    assert rec["tier"] == "L3"
    assert rec["first_install_tier"] == "L0", "首装应先下 L0"


def test_recommendation_notes_ollama_ready(cfg):
    from core.tiers import recommend_tier

    ready = EngineStatus(engine=EngineKind.OLLAMA, state=EngineState.READY, base_url="x",
                         enabled=True, reachable=True, models=["qwen2.5:1.5b"])
    rec = recommend_tier(make_hw(avail_gb=8.0), cfg, {EngineKind.OLLAMA: ready})
    assert "Ollama 已就绪" in rec["reason"]


def test_warning_allows_execution(cfg):
    """warning 是「能跑但慢」，不是禁用。"""

    from core.tiers import evaluate_tier

    hw = make_hw(avail_gb=16.0, gpus=[])
    status = evaluate_tier("L3", DeployMode.BUILTIN, hw, cfg)
    assert status.state.value == "warning"
    assert status.unavailable_reason is None
    assert status.warning_reason
