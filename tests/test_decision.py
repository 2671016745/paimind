"""The decision pipeline, including the degradation chain and the RAG second pass."""

from __future__ import annotations

import pytest

from core.config import Config
from core.context import AppContext
from core.decision import decide
from core.errors import AppError, ErrorCode
from core.runtime import save_state
from core.schemas import DecisionMode, DeployMode, EngineKind, EngineState, EngineStatus, Option


def make_req(**over):
    base = {"question": "周末去哪", "options": [Option(id="a", text="露营"), Option(id="b", text="古镇")],
            "model_tier": "L1", "mode": DecisionMode.AUTO}
    base.update(over)
    from core.schemas import DecideRequest

    return DecideRequest(**base)


def dead_config() -> Config:
    """A config pointing at a port where nothing listens, for degradation tests."""

    import tempfile

    data_dir = tempfile.mkdtemp(prefix="paimind-dead-")
    return Config.with_overrides({
        "app": {"data_dir": data_dir, "models_dir": f"{data_dir}/models"},
        "engines": {"ollama": {
            "base_url": "http://127.0.0.1:9",
            "openai_base_url": "http://127.0.0.1:9/v1",
            "timeout_seconds": 2,
        }},
        "api": {"rate_limit_per_minute": 0},
    })


def cfg_with_stub(stub) -> Config:
    """A config pointing at the stub engine, with its own private data dir.

    The data dir must be unique per test — a shared one lets the 24 h online
    cache leak between tests and silently change their outcome.
    """

    import tempfile

    data_dir = tempfile.mkdtemp(prefix="paimind-test-")
    return Config.with_overrides({
        "app": {"data_dir": data_dir, "models_dir": f"{data_dir}/models"},
        "engines": {"ollama": {
            "base_url": stub["base_url"],
            "openai_base_url": f"{stub['base_url']}/v1",
            "timeout_seconds": 5,
        }},
        "api": {"rate_limit_per_minute": 0},
    })


def test_decide_via_ollama_mode(ctx, stub_engine):
    """验收：ollama 已就绪时切到 ollama 模式能完成一次决策，后端为 ollama。"""

    ctx.cfg = cfg_with_stub(stub_engine)
    ctx._engines = {}
    ctx._engines_ts = 0.0
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    outcome = decide(make_req(), ctx, allow_online=False)
    assert outcome.response.selected.id in ("a", "b")
    assert outcome.response.backend == "ollama"
    assert outcome.response.source.value == "local"
    assert outcome.response.reason
    assert 0.0 <= outcome.response.selected.confidence <= 1.0
    assert outcome.response.alternatives, "应返回排序后的候选"


def test_decide_reports_confidence_and_usage(ctx, stub_engine):
    ctx.cfg = cfg_with_stub(stub_engine)
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    outcome = decide(make_req(), ctx, allow_online=False)
    assert outcome.response.usage.total_tokens >= 0
    assert outcome.response.latency_ms >= 0


def test_degradation_falls_back_to_builtin(ctx, stub_engine, tmp_path):
    """验收：指定引擎不可达时回退内置推理，并记录提示（不崩溃、不静默失败）。"""

    ctx.cfg = dead_config()
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    # builtin 未下载 -> 回退也救不了，必须给出明确错误而不是崩溃
    with pytest.raises(AppError) as exc:
        decide(make_req(), ctx, allow_online=False)
    assert exc.value.code in (ErrorCode.ENGINE_UNAVAILABLE, ErrorCode.MODEL_NOT_READY,
                              ErrorCode.MODEL_UNSUPPORTED)
    assert exc.value.status in (409, 422, 503)


def test_degradation_note_when_builtin_available(ctx, stub_engine, tmp_path):
    """When the ONNX file exists, an unreachable engine falls back with a note."""

    ctx.cfg = dead_config()
    models = ctx.cfg.models_dir / "L1"
    models.mkdir(parents=True, exist_ok=True)
    (models / "model.onnx").write_bytes(b"not-a-real-onnx")
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    from core.decision import _validate

    # _validate 应判定为「回退到 builtin」而不是直接 422
    state, mode, notes = _validate("L1", ctx)
    assert mode == DeployMode.BUILTIN
    assert any("已回退内置推理" in n for n in notes)

    # 真跑时因为 ONNX 是假的，会给出明确错误而不是崩溃
    with pytest.raises(AppError) as exc:
        decide(make_req(), ctx, allow_online=False)
    assert exc.value.code in (ErrorCode.MODEL_NOT_READY, ErrorCode.INFERENCE_ERROR,
                              ErrorCode.MODEL_LOADING)


def test_online_second_pass_uses_sources(ctx, stub_engine, monkeypatch):
    """验收：置信度不足时自动联网，结果标 hybrid 且带来源。"""

    ctx.cfg = cfg_with_stub(stub_engine)
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    import core.online as online
    from core.search import SearchResult

    monkeypatch.setattr(online, "search", lambda *a, **k: [
        SearchResult(title="天气", url="https://weather.com/x", snippet="晴", domain="weather.com")
    ])
    monkeypatch.setattr(online, "online_consent_given", lambda cfg: True)

    outcome = decide(make_req(question="今天天气怎么样"), ctx, allow_online=True)
    assert outcome.online_used is True
    assert outcome.response.source.value in ("hybrid", "online")
    assert outcome.response.sources, "联网结果必须带来源域名"
    assert outcome.response.sources[0].domain == "weather.com"


def test_local_mode_never_searches(ctx, stub_engine, monkeypatch):
    ctx.cfg = cfg_with_stub(stub_engine)
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    import core.online as online

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("local 模式不应调用搜索")

    monkeypatch.setattr(online, "search", boom)
    outcome = decide(make_req(question="今天天气怎么样"), ctx, allow_online=False)
    assert outcome.online_used is False
    assert called["n"] == 0


def test_search_failure_degrades_silently(ctx, stub_engine, monkeypatch):
    """验收：联网失败静默回退本地，不抛错。"""

    ctx.cfg = cfg_with_stub(stub_engine)
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    import core.online as online

    def fail(*a, **k):
        raise AppError(ErrorCode.ALL_MIRRORS_FAILED, "全部搜索源失败")

    monkeypatch.setattr(online, "search", fail)
    monkeypatch.setattr(online, "online_consent_given", lambda cfg: True)

    outcome = decide(make_req(question="今天天气怎么样"), ctx, allow_online=True)
    assert outcome.response.selected.id in ("a", "b"), "搜索失败仍应给出本地结果"
    assert any("联网不可用" in n for n in outcome.notes)


def test_usage_recorded(ctx, stub_engine):
    ctx.cfg = cfg_with_stub(stub_engine)
    ctx.refresh_engines()
    save_state(tier="L1", mode="ollama", cfg=ctx.cfg)

    decide(make_req(), ctx, allow_online=False)
    summary = ctx.usage.summary()
    assert summary.total_requests >= 1
    assert "ollama" in summary.by_backend


# ---------------------------------------------------------------- online fallback


def _kill_all_search(monkeypatch):
    """Make every search provider fail — equivalent to being offline."""
    import core.search as search_module
    for fn in ("_serpapi", "_bing", "_brave", "_bing_web", "_mojeek", "_duckduckgo"):
        if hasattr(search_module, fn):
            monkeypatch.setattr(search_module, fn, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))


def test_offline_silently_falls_back_to_local(monkeypatch, tmp_path):
    """验收：断网时联网自动静默回退本地，不弹错误。

    The online branch is entered (the question is time-sensitive), every search
    provider fails, and the decision still succeeds from the local answer with an
    explicit note — no exception reaches the caller.
    """

    import core.runtime as runtime_module
    from core.config import get_config
    from core.context import AppContext
    from core.decision import decide
    from core.schemas import DecideRequest

    cfg = get_config()
    monkeypatch.setattr(cfg, "_data", {**cfg.as_dict(), "app": {**cfg.section("app"), "data_dir": str(tmp_path)}})
    runtime_module.set_current_mode("ollama", cfg)
    runtime_module.set_current_tier("L1", cfg)

    _kill_all_search(monkeypatch)

    ctx = AppContext(cfg)
    ctx.refresh_hardware()
    ctx.refresh_engines()

    out = decide(DecideRequest(
        question="今天北京天气怎么样？",      # time-sensitive → triggers online
        options=[{"id": "a", "text": "晴"}, {"id": "b", "text": "雨"}],
        model_tier="L1", mode="auto",
    ), ctx)

    d = out.response.model_dump()
    assert str(d.get("source")).endswith("LOCAL"), f"断网应回退本地，实际 {d.get('source')}"
    assert d["selected"]["text"], "必须给出本地答案"
    assert not out.online_used, "没拿到联网结果就不能声称用了联网"
    assert any("联网不可用" in n for n in out.notes), f"应有静默回退提示，实际 {out.notes}"


def test_online_used_only_when_results_arrive(monkeypatch, tmp_path):
    """online_used 的语义：只有联网结果真正进入答案时才为 True。"""

    import core.runtime as runtime_module
    import core.online as online_module
    from core.config import get_config
    from core.context import AppContext
    from core.decision import decide
    from core.schemas import DecideRequest

    cfg = get_config()
    monkeypatch.setattr(cfg, "_data", {**cfg.as_dict(), "app": {**cfg.section("app"), "data_dir": str(tmp_path)}})
    runtime_module.set_current_mode("ollama", cfg)
    runtime_module.set_current_tier("L1", cfg)

    # 搜索返回空列表（不是抛异常）—— 也算没拿到结果
    monkeypatch.setattr(online_module, "search", lambda *a, **k: [])

    ctx = AppContext(cfg)
    ctx.refresh_hardware()
    ctx.refresh_engines()

    out = decide(DecideRequest(
        question="今天北京天气怎么样？",
        options=[{"id": "a", "text": "晴"}, {"id": "b", "text": "雨"}],
        model_tier="L1", mode="auto",
    ), ctx)
    assert not out.online_used, "搜索返回空 → online_used 必须为 False"
    assert out.online_reason, "但仍然要记录「为什么尝试联网」"
