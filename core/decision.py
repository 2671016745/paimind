"""The decision pipeline.

    question + options
        -> resolve tier / deploy mode
        -> validate availability (422 disabled, 409 not-ready)
        -> resolve backend (with the degradation chain)
        -> local decision
        -> maybe online: rewrite query -> search -> decide with evidence
        -> selected + reason + confidence + source badge

Everything the UI and the API need is in :func:`decide`; :func:`decide_stream`
wraps it for SSE.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import Config, get_config
from .context import AppContext
from .errors import AppError, ErrorCode
from .hardware import hardware_snapshot
from .model_files import downloaded_map, is_downloaded
from .online import (
    cached_search,
    needs_online,
    online_sources_payload,
    source_domains,
    upload_options_enabled,
)
from .search import rewrite_query
from .prompt import build_decision_prompt, parse_decision
from .runtime import current_mode, current_tier, load_state
from .schemas import (
    Alternative,
    DecisionMode,
    DecideRequest,
    DecisionResponse,
    DeployMode,
    Language,
    Option,
    Selected,
    SourceKind,
    SourceRef,
    TierState,
    Usage,
    utc_now_epoch,
)
from .tiers import evaluate_tier_with_engines, recommend_tier


@dataclass
class DecisionOutcome:
    response: DecisionResponse
    online_used: bool = False
    online_reason: Optional[str] = None
    from_cache: bool = False
    degraded: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.response.model_dump()


def _resolve_tier(req: DecideRequest, ctx: AppContext) -> str:
    if str(req.model_tier).lower() != "auto":
        return str(req.model_tier).upper()

    state = load_state(ctx.cfg)
    tier = state.get("current_tier", "L0")
    status = evaluate_tier_with_engines(
        tier, current_mode(ctx.cfg), ctx.hardware, ctx.cfg,
        ctx.engines, downloaded_map(ctx.cfg),
    )
    if status.state != TierState.DISABLED:
        return tier

    recommendation = recommend_tier(ctx.hardware, ctx.cfg, ctx.engines, downloaded_map(ctx.cfg))
    return str(recommendation.get("tier", "L0"))


def _validate(tier_id: str, ctx: AppContext) -> tuple[TierState, DeployMode, List[str]]:
    """Return (state, mode, notes) or raise the right HTTP error.

    Degradation (``docs/deploy.md`` §10.3 step 2): when the *requested* mode is
    disabled but the bundled ONNX model for that tier is already on disk, we fall
    back to builtin instead of failing — the user gets an answer plus a note.
    Only when nothing can serve the tier do we raise ``MODEL_UNSUPPORTED``.
    """

    mode = current_mode(ctx.cfg)
    downloaded = downloaded_map(ctx.cfg)
    status = evaluate_tier_with_engines(tier_id, mode, ctx.hardware, ctx.cfg, ctx.engines, downloaded)
    notes: List[str] = []

    if status.state == TierState.DISABLED:
        if mode != DeployMode.BUILTIN and downloaded.get(tier_id) and is_downloaded(ctx.cfg, tier_id):
            builtin_status = evaluate_tier_with_engines(
                tier_id, DeployMode.BUILTIN, ctx.hardware, ctx.cfg, ctx.engines, downloaded
            )
            if builtin_status.state != TierState.DISABLED:
                reason = status.unavailable_reason or f"{mode.value} 不可用"
                notes.append(f"{reason}，已回退内置推理")
                return builtin_status.state, DeployMode.BUILTIN, notes

        raise AppError(
            ErrorCode.MODEL_UNSUPPORTED,
            f"档位 {tier_id}（{mode.value} 模式）当前不可用：{status.unavailable_reason}",
            {
                "tier": tier_id,
                "deploy_mode": mode.value,
                "reason": status.unavailable_reason,
                "hardware": hardware_snapshot(ctx.hardware),
            },
        )

    if status.state == TierState.WARNING and status.warning_reason:
        notes.append(status.warning_reason)

    if mode == DeployMode.BUILTIN and not is_downloaded(ctx.cfg, tier_id):
        raise AppError(
            ErrorCode.MODEL_NOT_READY,
            f"档位 {tier_id} 尚未下载，无法加载",
            {"tier": tier_id, "path": str(ctx.cfg.models_dir / tier_id)},
        )

    return status.state, mode, notes


def decide(
    req: DecideRequest,
    ctx: AppContext,
    *,
    force_online: bool = False,
    allow_online: Optional[bool] = None,
    upload_options: Optional[bool] = None,
    deploy_mode: Optional[DeployMode] = None,
) -> DecisionOutcome:
    cfg = ctx.cfg
    started = time.perf_counter()
    notes: List[str] = []

    if deploy_mode is not None:
        from .runtime import set_current_mode

        set_current_mode(deploy_mode.value, cfg)
        notes.append(f"已切换部署模式为 {deploy_mode.value}")

    tier_id = _resolve_tier(req, ctx)
    state, mode, validate_notes = _validate(tier_id, ctx)
    notes.extend(validate_notes)

    resolution = ctx.backend_for(tier_id, mode, downloaded_map(ctx.cfg))
    backend = resolution.backend
    if resolution.notes:
        notes.extend(resolution.notes)
    actual_mode = resolution.actual_mode

    lang = req.language
    allow = req.allow_online if allow_online is None else allow_online
    if not cfg.get("decision.allow_online", True):
        allow = False
        notes.append("配置已禁用联网，仅使用本地结果")

    upload_opts = upload_options if upload_options is not None else upload_options_enabled(cfg)

    # ---- pass 1: local decision ----------------------------------------
    system, user = build_decision_prompt(
        req.question, req.options, req.context, lang,
        max_reason_length=req.max_reason_length,
    )
    completion = backend.complete(
        _messages(system, user),
        max_tokens=256,
        temperature=0.3,
        stop=["\n\n"],
    )
    parsed = parse_decision(completion.text, req.options, req.max_reason_length)
    latency_so_far = int((time.perf_counter() - started) * 1000)

    source = SourceKind.LOCAL
    sources: List[SourceRef] = []
    online_used = False
    online_reason: Optional[str] = None
    from_cache = False
    prompt_tokens = completion.prompt_tokens
    completion_tokens = completion.completion_tokens

    # ---- pass 2: online, when the local answer is not good enough -------
    if allow:
        wanted, reason = needs_online(
            req.question, parsed.confidence,
            DecisionMode.ONLINE if force_online else req.mode,
            force=force_online, cfg=cfg,
        )
        if wanted:
            # ``online_used`` means "online evidence actually reached the answer",
            # not "the online branch was entered" — a failed search must not claim
            # the answer came from the network.
            online_reason = reason
            try:
                query = rewrite_query(req.question, backend, cfg, req.language.value)
                if not upload_opts:
                    # Privacy switch off: the user accepted sending options too.
                    texts = [o.text.strip() for o in req.options if o.text.strip()]
                    if texts:
                        query = f"{query}（选项：{' / '.join(texts)}）"
                results, from_cache = cached_search(query, cfg)
                if results:
                    online_used = True
                    system2, user2 = build_decision_prompt(
                        req.question, req.options, req.context, lang,
                        sources=online_sources_payload(results),
                        max_reason_length=req.max_reason_length,
                    )
                    second = backend.complete(
                        _messages(system2, user2), max_tokens=256, temperature=0.2, stop=["\n\n"]
                    )
                    parsed2 = parse_decision(second.text, req.options, req.max_reason_length)
                    if parsed2.selected_id:
                        parsed = parsed2
                        prompt_tokens += second.prompt_tokens
                        completion_tokens += second.completion_tokens
                    source = SourceKind.HYBRID if state != TierState.WARNING else SourceKind.HYBRID
                    sources = [
                        SourceRef(title=r.get("title", ""), url=r.get("url", ""),
                                  domain=r.get("domain", ""))
                        for r in results
                    ]
                    notes.append(f"已联网检索（{reason}）")
                else:
                    notes.append("联网未返回结果，已用本地结果")
            except AppError:
                notes.append("联网不可用，已用本地结果")
            except Exception:
                notes.append("联网不可用，已用本地结果")
    elif force_online:
        notes.append("联网已被配置禁用，仅使用本地结果")

    selected = next((o for o in req.options if o.id == parsed.selected_id), req.options[0])
    alternatives = _rank_alternatives(parsed.alternatives, req.options, selected.id)

    total_latency = int((time.perf_counter() - started) * 1000)
    response = DecisionResponse(
        id=f"dec-{uuid.uuid4().hex[:16]}",
        created=utc_now_epoch(),
        request_id=req.request_id,
        model=completion.model or f"{actual_mode.value}:{tier_id}",
        tier=tier_id,
        backend=backend.kind().value,
        source=source,
        selected=Selected(
            id=selected.id, text=selected.text,
            confidence=round(parsed.confidence, 3),
        ),
        reason=_sanitize_reason(parsed.reason, selected, req.options, lang),
        alternatives=alternatives,
        sources=sources,
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        latency_ms=total_latency,
    )

    ctx.usage.record(
        tier=tier_id, backend=backend.kind().value, source=source.value,
        latency_ms=total_latency, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, cached=from_cache,
    )

    return DecisionOutcome(
        response=response,
        online_used=online_used,
        online_reason=online_reason,
        from_cache=from_cache,
        degraded=resolution.degraded,
        notes=notes,
    )


def decide_stream(
    req: DecideRequest,
    ctx: AppContext,
    *,
    force_online: bool = False,
    allow_online: Optional[bool] = None,
    upload_options: Optional[bool] = None,
    deploy_mode: Optional[DeployMode] = None,
    _precomputed=None,
):
    """Yield SSE events: ``meta`` -> ``token``* -> ``result`` -> ``done``.

    ``_precomputed`` lets the caller run tier resolution and validation *before*
    the response starts, so an error can still be returned as a proper HTTP
    status instead of a truncated stream.
    """

    started = time.perf_counter()
    if deploy_mode is not None:
        from .runtime import set_current_mode

        set_current_mode(deploy_mode.value, ctx.cfg)

    if _precomputed is not None:
        tier_id, state, mode, notes = _precomputed
    else:
        tier_id = _resolve_tier(req, ctx)
        state, mode, notes = _validate(tier_id, ctx)
    resolution = ctx.backend_for(tier_id, mode, downloaded_map(ctx.cfg))
    backend = resolution.backend
    if resolution.notes:
        notes.extend(resolution.notes)

    request_id = f"dec-{uuid.uuid4().hex[:16]}"
    yield {"event": "meta", "data": {
        "id": request_id,
        "model": f"{mode.value}:{tier_id}",
        "tier": tier_id,
        "backend": backend.kind().value,
        "source": "local",
        "notes": notes,
    }}

    system, user = build_decision_prompt(
        req.question, req.options, req.context, req.language,
        max_reason_length=req.max_reason_length,
    )
    buffer: List[str] = []
    try:
        for delta in backend.stream(_messages(system, user), max_tokens=256, temperature=0.3,
                                    stop=["\n\n"]):
            buffer.append(delta)
            yield {"event": "token", "data": {"delta": delta}}
    except Exception:
        completion = backend.complete(_messages(system, user), max_tokens=256, temperature=0.3,
                                      stop=["\n\n"])
        buffer = [completion.text]

    parsed = parse_decision("".join(buffer), req.options, req.max_reason_length)
    selected = next((o for o in req.options if o.id == parsed.selected_id), req.options[0])
    response = DecisionResponse(
        id=request_id,
        created=utc_now_epoch(),
        request_id=req.request_id,
        model=f"{mode.value}:{tier_id}",
        tier=tier_id,
        backend=backend.kind().value,
        source=SourceKind.LOCAL,
        selected=Selected(id=selected.id, text=selected.text, confidence=round(parsed.confidence, 3)),
        reason=_sanitize_reason(
            parsed.reason, selected, req.options, req.language
        ),
        alternatives=_rank_alternatives(
            parsed.alternatives, req.options, selected.id
        ),
        sources=[],
        usage=Usage(),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    yield {"event": "result", "data": response.model_dump()}
    yield {"event": "done", "data": {"latency_ms": response.latency_ms}}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _messages(system: str, user: str) -> List[Any]:
    from .inference.base import ChatMessage

    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def _rank_alternatives(
    parsed: Sequence[Alternative],
    options: Sequence[Option],
    selected_id: Optional[str] = None,
) -> List[Alternative]:
    known = {o.id for o in options}
    out = [a for a in parsed if a.id in known]
    seen = {a.id for a in out}
    for o in options:
        if o.id not in seen:
            out.append(Alternative(id=o.id, text=o.text, score=0.0))
    # 小模型常给出「选了 A 却把最高分给 B」的自相矛盾输出；
    # 这里保证选中项分数严格高于其它选项，候选排序才与结论一致。
    if selected_id:
        others = [a.score for a in out if a.id != selected_id]
        top_other = max(others) if others else 0.0
        for i, a in enumerate(out):
            if a.id == selected_id and a.score <= top_other:
                out[i] = a.model_copy(
                    update={"score": round(min(1.0, top_other + 0.01), 3)}
                )
    return sorted(out, key=lambda a: a.score, reverse=True)


def _fallback_reason(selected: Option, lang: Language) -> str:
    if lang == Language.EN:
        return "Selected as the best available match."
    return "综合判断后选择该项。"


def _sanitize_reason(
    reason: str,
    selected: Option,
    options: Sequence[Option],
    lang: Language,
) -> str:
    """小模型会写「选了 A 却解释 B」的理由，这里保证理由不指向别的选项。

    判据：理由里没有选中项的文字、却出现了别的选项的文字 → 判定跑偏，
    退回通用理由，胜过展示自相矛盾的结论。
    """
    text = (reason or "").strip()
    if not text:
        return _fallback_reason(selected, lang)
    sel_text = selected.text.strip()
    if sel_text and sel_text not in text:
        others = [
            o.text.strip()
            for o in options
            if o.id != selected.id and o.text.strip()
        ]
        if any(t in text for t in others):
            return _fallback_reason(selected, lang)
    return text


def explain_unavailable(tier_id: str, ctx: AppContext, mode: Optional[DeployMode] = None) -> Dict[str, Any]:
    """The "why is this grey" payload for the UI explainer layer."""

    mode = mode or current_mode(ctx.cfg)
    status = evaluate_tier_with_engines(
        tier_id, mode, ctx.hardware, ctx.cfg, ctx.engines, downloaded_map(ctx.cfg)
    )
    req = status.requirements
    hw = ctx.hardware
    gpu = next((g for g in hw.gpus if g.driver_ok), None)

    needs: List[str] = []
    current: List[str] = []
    fixes: List[str] = []

    if req:
        needs.append(f"可用内存 ≥ {req.min_available_memory_gb:g} GB")
        current.append(f"可用内存 {hw.memory.available_gb:.1f} GB")
        if req.cpu_x86_avx2:
            needs.append("CPU 支持 AVX2")
            current.append("CPU " + ("支持" if hw.cpu.has_avx2 else "不支持") + " AVX2")
        needs.append(f"磁盘剩余 ≥ {req.min_disk_mb:g} MB（未下载时）")
        current.append(f"磁盘剩余 {hw.disk.free_gb * 1024:.0f} MB")
        if mode == DeployMode.VLLM:
            needs.append("NVIDIA GPU + 足够显存")
            current.append(gpu.name if gpu else "未检测到 NVIDIA GPU")
            fixes.append("安装 NVIDIA GPU 并安装驱动，或使用内置/Ollama 模式")

    if mode == DeployMode.BUILTIN:
        fixes.append("关闭其它占用内存的程序后重试")
        fixes.append("或改用更小的档位（L0/L1）")
    elif mode == DeployMode.OLLAMA:
        fixes.append("启动 Ollama：ollama serve")
        fixes.append("拉取模型：ollama pull qwen2.5:1.5b")
    elif mode == DeployMode.VLLM:
        fixes.append("vLLM 需要 NVIDIA GPU + Linux，Windows 请用 builtin/Ollama")
    elif mode == DeployMode.LLAMACPP:
        fixes.append("启动 llama-server 并加载 GGUF 模型")

    return {
        "tier": tier_id,
        "deploy_mode": mode.value,
        "state": status.state.value,
        "reason": status.unavailable_reason,
        "needs": needs,
        "current": current,
        "how_to_fix": fixes,
    }
