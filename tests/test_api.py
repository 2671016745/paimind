"""HTTP API surface: status codes, error envelope, key endpoints."""

from __future__ import annotations

import pytest

from core.errors import ErrorCode
from core.schemas import DecideRequest, DecisionMode, DeployMode, Option


def req(**over):
    base = {"question": "周末去哪", "options": [{"id": "a", "text": "露营"}, {"id": "b", "text": "古镇"}],
            "model_tier": "L1", "mode": "local"}
    base.update(over)
    return base


def test_health(api):
    r = api.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "paimind"


def test_openapi_docs_reachable(api):
    assert api.get("/openapi.json").status_code == 200
    assert api.get("/docs").status_code == 200


def test_models_returns_status_and_reason(api):
    r = api.get("/v1/models")
    assert r.status_code == 200
    tiers = {t["tier"]: t for t in r.json()["tiers"]}
    assert set(tiers) == {"L0", "L1", "L2", "L3"}
    for tier in tiers.values():
        assert "status" in tier
        assert tier["status"] in ("ready", "warning", "disabled")


def test_models_carries_recommendation(api):
    body = api.get("/v1/models").json()
    assert "recommendation" in body
    assert body["recommendation"]["tier"] in ("L0", "L1", "L2", "L3")


def test_engines_endpoint(api):
    r = api.get("/v1/engines")
    assert r.status_code == 200
    names = {e["engine"] for e in r.json()["engines"]}
    assert {"ollama", "vllm", "llamacpp"} <= names


def test_download_sources_two_providers(api):
    r = api.get("/v1/download-sources")
    assert r.status_code == 200
    groups = {g["provider"] for g in r.json()["groups"]}
    assert "github" in groups and "huggingface" in groups


def test_hf_auth_status_not_logged_in(api):
    r = api.get("/v1/hf/auth/status")
    assert r.status_code == 200
    assert r.json()["logged_in"] is False


def test_usage_empty(api):
    r = api.get("/v1/usage")
    assert r.status_code == 200
    assert r.json()["total_requests"] == 0


def test_config_roundtrip(api):
    r = api.get("/v1/config")
    assert r.status_code == 200
    assert r.json()["app"]["name"] == "paimind"


def test_decide_rejects_single_option(api):
    r = api.post("/v1/decide", json=req(options=[{"id": "a", "text": "A"}]))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_decide_rejects_blank_question(api):
    r = api.post("/v1/decide", json=req(question="   "))
    assert r.status_code == 400


def test_decide_unknown_tier_is_400(api):
    r = api.post("/v1/decide", json=req(model_tier="L9"))
    assert r.status_code == 400


def test_decide_disabled_tier_returns_422_with_hardware(api, monkeypatch):
    """验收：vLLM 模式下引擎不可达时返回 422 并附硬件快照（本机有 NVIDIA GPU 时
    走 503，两者都是「不崩溃、给出明确错误」）。

    Points vLLM at a port where nothing listens, so the test does not depend on
    whether a real vLLM server happens to be running on the developer's machine.
    """

    import core.config as config_module

    cfg = config_module.get_config()
    engines = cfg.section("engines")
    monkeypatch.setitem(engines["vllm"], "base_url", "http://127.0.0.1:9")
    monkeypatch.setitem(engines["vllm"], "openai_base_url", "http://127.0.0.1:9/v1")

    r = api.put("/v1/models/L1/deploy-mode", json={"mode": "vllm"})
    assert r.status_code in (422, 503)
    error = r.json()["error"]
    assert error["code"] in ("MODEL_UNSUPPORTED", "ENGINE_UNAVAILABLE")
    if error["code"] == "MODEL_UNSUPPORTED":
        assert "hardware" in error.get("details", {}), "422 必须附硬件快照"


def test_decide_undownloaded_builtin_returns_409(api):
    r = api.post("/v1/decide", json=req(model_tier="L1"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "MODEL_NOT_READY"


def test_deploy_mode_switch_builtin(api):
    r = api.put("/v1/models/L1/deploy-mode", json={"mode": "builtin"})
    assert r.status_code == 200
    assert r.json()["deploy_mode"] == "builtin"


def test_deploy_mode_switch_to_down_engine_is_503(api, monkeypatch):
    """切换到一个确定不可达的引擎必须返回 503，而不是 500 或崩溃。

    Points Ollama at a port where nothing listens so the test does not depend
    on whether a real Ollama happens to be running on the developer's machine.
    """

    import core.config as config_module
    from server import deps

    cfg = config_module.get_config()
    ollama = cfg.section("engines")["ollama"]
    monkeypatch.setitem(ollama, "base_url", "http://127.0.0.1:9")
    monkeypatch.setitem(ollama, "openai_base_url", "http://127.0.0.1:9/v1")
    monkeypatch.setitem(ollama, "timeout_seconds", 1)

    deps.reset_ctx()
    deps.get_ctx().refresh_engines()
    try:
        r = api.put("/v1/models/L1/deploy-mode", json={"mode": "ollama"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "ENGINE_UNAVAILABLE"
    finally:
        deps.reset_ctx()


def test_why_unavailable_explainer(api):
    r = api.get("/v1/models/L1/why-unavailable?mode=vllm")
    assert r.status_code == 200
    body = r.json()
    assert body["deploy_mode"] == "vllm"
    assert "needs" in body and "current" in body and "how_to_fix" in body


def test_error_envelope_shape(api):
    r = api.post("/v1/decide", json=req(model_tier="L1"))
    error = r.json()["error"]
    assert set(error) >= {"code", "message", "type", "param"}


def test_engines_test_endpoint(api):
    r = api.post("/v1/engines/ollama/test")
    assert r.status_code == 200
    assert "state" in r.json()


def test_stream_endpoint_events(api_stub):
    """验收：SSE 返回 meta → token* → result → done，且不会中途崩掉。"""

    with api_stub.stream("POST", "/v1/decide/stream", json=req(model_tier="L1")) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = [line for line in resp.iter_lines() if line.startswith("event:")]
        names = {line.split(":", 1)[1].strip() for line in events}
        assert "meta" in names
        assert "result" in names
        assert "done" in names


def test_stream_endpoint_reports_error_before_streaming(api):
    """模型未下载时必须返回正常 HTTP 错误，而不是断掉的流。"""

    r = api.post("/v1/decide/stream", json=req(model_tier="L1"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "MODEL_NOT_READY"


def test_cors_allows_configured_origin(api):
    """验收：配置里列出的源必须拿到 access-control-allow-origin 头。

    这是 PWA / 手机浏览器能访问 API 的前提 —— 漏配时浏览器直接拦掉请求，
    症状是界面一直「尚未决策」而服务端日志里根本没有请求。
    *The PWA and mobile browsers need this header; without it the browser blocks
    the request and the UI just shows "not yet decided" with no server log entry.*
    """

    import core.config as config_module

    cfg = config_module.get_config()
    origins = list(cfg.get("api.cors_origins", []) or [])
    assert origins, "config 里必须至少列出一个 cors_origins，否则 PWA 无法访问 API"

    r = api.options(
        "/v1/decide",
        headers={
            "Origin": origins[0],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origins[0]


def test_cors_blocks_unknown_origin(api):
    """未列出的源必须拿不到 allow-origin 头，而不是默认放行。"""

    import core.config as config_module

    cfg = config_module.get_config()
    known = list(cfg.get("api.cors_origins", []) or [])
    stranger = "http://127.0.0.1:59999"
    if stranger in known:
        pytest.skip("test origin happens to be configured")

    r = api.options(
        "/v1/decide",
        headers={
            "Origin": stranger,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") is None


def test_hf_login_network_failure_is_not_500(api, monkeypatch):
    """验收：网络层失败（DNS/TLS/拒绝连接）必须映射成 503 NETWORK_ERROR，不能是 500。

    On this machine httpx cannot verify the certificate of the local proxy, so the
    raw exception would otherwise escape as a generic 500.
    """

    import httpx
    import core.hf as hf_module

    def _boom(*a, **k):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] simulated")

    monkeypatch.setattr(hf_module.httpx, "get", _boom)

    r = api.post("/v1/hf/auth/login", json={"token": "hf_whatever"})
    assert r.status_code == 503, f"网络失败应是 503，实际 {r.status_code}"
    assert r.json()["error"]["code"] == "NETWORK_ERROR"


def test_hf_login_invalid_token_is_401(api, monkeypatch):
    """401/403 from HF must surface as 401 HF_UNAUTHORIZED, not a 500."""

    import httpx
    import core.hf as hf_module

    class _Resp:
        status_code = 401

        def raise_for_status(self):
            raise httpx.HTTPStatusError("401", request=None, response=None)

    monkeypatch.setattr(hf_module.httpx, "get", lambda *a, **k: _Resp())

    r = api.post("/v1/hf/auth/login", json={"token": "hf_bad"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "HF_UNAUTHORIZED"
