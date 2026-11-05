"""External engine discovery.

Probes Ollama, vLLM, llama-server and Docker *in parallel* with a 2 s timeout per
probe so a dead engine never delays startup.  Results are exposed at
``GET /v1/engines`` and consumed by the tier rules.

Readiness means two things at once:

* the endpoint answers, **and**
* the model the app wants for the tier is actually present.

An engine that answers but lacks the model reports ``model_missing`` — the UI
shows "engine is up, model is not pulled" and offers the one-click pull, instead
of pretending the tier is usable.
"""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import httpx

from .config import Config, get_config
from .errors import AppError, ErrorCode
from .hardware import detect_gpus
from .schemas import DeployMode, EngineKind, EngineState, EngineStatus

PROBE_TIMEOUT = 2.0


def _probe_http(url: str, timeout: float = PROBE_TIMEOUT) -> tuple[bool, Optional[int], float]:
    import time

    start = time.perf_counter()
    try:
        r = httpx.get(url, timeout=timeout)
        return r.status_code == 200, r.status_code, int((time.perf_counter() - start) * 1000)
    except Exception:
        return False, None, int((time.perf_counter() - start) * 1000)


def _model_names_from_models_endpoint(body: bytes) -> List[str]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
            if name:
                out.append(str(name))
    return out


def probe_ollama(cfg: Config) -> EngineStatus:
    engine_cfg = cfg.engine("ollama")
    base = str(engine_cfg.get("base_url", "http://localhost:11434")).rstrip("/")
    timeout = float(engine_cfg.get("timeout_seconds", PROBE_TIMEOUT))
    enabled = bool(engine_cfg.get("enabled", True))

    ok, status, latency = _probe_http(f"{base}/api/tags", timeout)
    models: List[str] = []
    if ok:
        try:
            body = httpx.get(f"{base}/api/tags", timeout=timeout).content
            data = json.loads(body)
            for m in data.get("models", []):
                name = m.get("name") or m.get("model")
                if name:
                    models.append(str(name))
        except Exception:
            pass
    if not models:
        ok2, _, _ = _probe_http(f"{base}/v1/models", timeout)
        if ok2:
            try:
                models = _model_names_from_models_endpoint(
                    httpx.get(f"{base}/v1/models", timeout=timeout).content
                )
            except Exception:
                pass
            ok = True

    if not enabled:
        state = EngineState.NOT_INSTALLED
        message = "已在配置中禁用"
    elif ok and models:
        state = EngineState.READY
        message = None
    elif ok:
        state = EngineState.MODEL_MISSING
        message = "Ollama 已启动，但尚未拉取任何模型"
    else:
        state = EngineState.UNREACHABLE
        message = "Ollama 未启动"

    return EngineStatus(
        engine=EngineKind.OLLAMA, state=state, base_url=base, enabled=enabled,
        reachable=ok, latency_ms=latency, models=models, message=message,
    )


def probe_vllm(cfg: Config) -> EngineStatus:
    engine_cfg = cfg.engine("vllm")
    base = str(engine_cfg.get("base_url", "http://localhost:8000")).rstrip("/")
    timeout = float(engine_cfg.get("timeout_seconds", PROBE_TIMEOUT))
    enabled = bool(engine_cfg.get("enabled", True))

    gpus = [g for g in detect_gpus() if g.vendor == "nvidia" and g.driver_ok]
    no_gpu_reason = "vLLM 需要 NVIDIA GPU，当前未检测到"

    ok, status, latency = _probe_http(f"{base}/health", timeout)
    models: List[str] = []
    if ok:
        ok2, _, _ = _probe_http(f"{base}/v1/models", timeout)
        if ok2:
            try:
                models = _model_names_from_models_endpoint(
                    httpx.get(f"{base}/v1/models", timeout=timeout).content
                )
            except Exception:
                pass
    else:
        ok2, _, _ = _probe_http(f"{base}/v1/models", timeout)
        if ok2:
            ok = True
            try:
                models = _model_names_from_models_endpoint(
                    httpx.get(f"{base}/v1/models", timeout=timeout).content
                )
            except Exception:
                pass

    if not enabled:
        state, message = EngineState.NOT_INSTALLED, "已在配置中禁用"
    elif not gpus:
        state, message = EngineState.NOT_INSTALLED, no_gpu_reason
    elif ok and models:
        state, message = EngineState.READY, None
    elif ok:
        state, message = EngineState.MODEL_MISSING, "vLLM 已启动，但未找到匹配模型"
    else:
        state, message = EngineState.UNREACHABLE, "vLLM 服务未启动"

    return EngineStatus(
        engine=EngineKind.VLLM, state=state, base_url=base, enabled=enabled,
        reachable=ok, latency_ms=latency, models=models, message=message,
        details={"requires_nvidia_gpu": True, "nvidia_gpu": gpus[0].name if gpus else None},
    )


def probe_llamacpp(cfg: Config) -> EngineStatus:
    engine_cfg = cfg.engine("llamacpp")
    base = str(engine_cfg.get("base_url", "http://localhost:8080")).rstrip("/")
    timeout = float(engine_cfg.get("timeout_seconds", PROBE_TIMEOUT))
    enabled = bool(engine_cfg.get("enabled", True))

    ok, status, latency = _probe_http(f"{base}/health", timeout)
    models: List[str] = []
    if ok:
        ok2, _, _ = _probe_http(f"{base}/v1/models", timeout)
        if ok2:
            try:
                models = _model_names_from_models_endpoint(
                    httpx.get(f"{base}/v1/models", timeout=timeout).content
                )
            except Exception:
                pass
    else:
        ok2, _, _ = _probe_http(f"{base}/v1/models", timeout)
        if ok2:
            ok = True
            try:
                models = _model_names_from_models_endpoint(
                    httpx.get(f"{base}/v1/models", timeout=timeout).content
                )
            except Exception:
                pass

    if not enabled:
        state, message = EngineState.NOT_INSTALLED, "已在配置中禁用"
    elif ok and models:
        state, message = EngineState.READY, None
    elif ok:
        state, message = EngineState.MODEL_MISSING, "llama-server 已启动，但未找到模型"
    else:
        state, message = EngineState.UNREACHABLE, "llama-server 未启动"

    return EngineStatus(
        engine=EngineKind.LLAMACPP, state=state, base_url=base, enabled=enabled,
        reachable=ok, latency_ms=latency, models=models, message=message,
    )


def probe_docker(cfg: Config) -> EngineStatus:
    """Inspect containers; the app never manages their lifecycle."""

    engine_cfg = cfg.engine("docker")
    enabled = bool(engine_cfg.get("enabled", True))
    containers = engine_cfg.get("containers", {}) or {}

    running: Dict[str, Dict[str, object]] = {}
    installed = True
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=float(engine_cfg.get("timeout_seconds", 3)),
        )
        if out.returncode != 0 and ("not recognized" in (out.stderr or "") or "command not found" in (out.stderr or "")):
            installed = False
        else:
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                name = str(obj.get("Names", "")).split(",")[0].strip("/")
                running[name] = {
                    "status": obj.get("Status", ""),
                    "state": obj.get("State", ""),
                    "image": obj.get("Image", ""),
                }
    except FileNotFoundError:
        installed = False
    except Exception:
        installed = False

    if not installed:
        return EngineStatus(
            engine=EngineKind.DOCKER, state=EngineState.NOT_INSTALLED,
            base_url="docker", enabled=enabled, reachable=False,
            message="Docker 未安装（docker 模式已隐藏）",
        )

    wanted = {v: k for k, v in containers.items()}
    matched = {k: v for k, v in running.items() if k in wanted}
    if matched:
        return EngineStatus(
            engine=EngineKind.DOCKER, state=EngineState.READY, base_url="docker",
            enabled=enabled, reachable=True, models=sorted(matched),
            message=None, details={"containers": matched},
        )
    return EngineStatus(
        engine=EngineKind.DOCKER, state=EngineState.MODEL_MISSING, base_url="docker",
        enabled=enabled, reachable=True, models=[],
        message="Docker 已安装，但未发现目标容器",
        details={"containers": running, "wanted": wanted},
    )


def probe_all(cfg: Optional[Config] = None, max_workers: int = 4) -> Dict[EngineKind, EngineStatus]:
    """Probe every engine concurrently; one slow engine never blocks the others."""

    cfg = cfg or get_config()
    probes = {
        EngineKind.OLLAMA: lambda: probe_ollama(cfg),
        EngineKind.VLLM: lambda: probe_vllm(cfg),
        EngineKind.LLAMACPP: lambda: probe_llamacpp(cfg),
        EngineKind.DOCKER: lambda: probe_docker(cfg),
    }
    results: Dict[EngineKind, EngineStatus] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn): kind for kind, fn in probes.items()}
        for future in as_completed(futures):
            kind = futures[future]
            try:
                results[kind] = future.result()
            except Exception as exc:  # a probe bug must not kill startup
                results[kind] = EngineStatus(
                    engine=kind, state=EngineState.UNREACHABLE, base_url="",
                    enabled=False, reachable=False, message=f"探测失败：{exc}",
                )
    return results


def engine_for_mode(mode: DeployMode) -> EngineKind:
    return {
        DeployMode.OLLAMA: EngineKind.OLLAMA,
        DeployMode.VLLM: EngineKind.VLLM,
        DeployMode.LLAMACPP: EngineKind.LLAMACPP,
        DeployMode.DOCKER: EngineKind.DOCKER,
    }.get(mode, EngineKind.OLLAMA)


def require_engine_reachable(
    mode: DeployMode,
    statuses: Dict[EngineKind, EngineStatus],
    cfg: Optional[Config] = None,
) -> EngineStatus:
    """Re-probe a single engine before the user switches to it (fail fast)."""

    cfg = cfg or get_config()
    kind = engine_for_mode(mode)
    probe = {
        EngineKind.OLLAMA: probe_ollama,
        EngineKind.VLLM: probe_vllm,
        EngineKind.LLAMACPP: probe_llamacpp,
        EngineKind.DOCKER: probe_docker,
    }[kind]
    fresh = probe(cfg)
    if not fresh.enabled:
        raise AppError(ErrorCode.ENGINE_UNSUPPORTED, f"{mode.value} 模式已在配置中禁用",
                       {"engine": mode.value})
    if not fresh.reachable:
        raise AppError(
            ErrorCode.ENGINE_UNAVAILABLE,
            f"{mode.value} 不可达：{fresh.base_url}",
            {"engine": mode.value, "url": fresh.base_url},
        )
    return fresh


def engine_health_payload(statuses: Dict[EngineKind, EngineStatus]) -> Dict[str, object]:
    return {
        "engines": [
            {
                "engine": s.engine.value,
                "state": s.state.value,
                "enabled": s.enabled,
                "reachable": s.reachable,
                "latency_ms": s.latency_ms,
                "base_url": s.base_url,
                "models": s.models,
                "message": s.message,
                "details": s.details,
            }
            for s in statuses.values()
        ]
    }
