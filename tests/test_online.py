"""Online trigger rules, cache and privacy."""

from __future__ import annotations

import time

import pytest

from core.online import (
    cached_search,
    contains_time_sensitive_keywords,
    needs_online,
    set_upload_options,
    upload_options_enabled,
    build_search_query,
)
from core.schemas import DecisionMode, Option

OPTIONS = [Option(id="a", text="露营"), Option(id="b", text="古镇")]


def test_low_confidence_triggers_online(cfg):
    needed, reason = needs_online("随便选个", 0.3, DecisionMode.AUTO, cfg=cfg)
    assert needed and "置信度" in reason


def test_confident_answer_stays_local(cfg):
    assert needs_online("随便选个", 0.95, DecisionMode.AUTO, cfg=cfg) == (False, None)


@pytest.mark.parametrize("question", ["今天天气怎么样", "最新的汇率是多少", "这个软件的最新版本号"])
def test_time_sensitive_keywords_trigger_online(cfg, question):
    needed, reason = needs_online(question, 0.95, DecisionMode.AUTO, cfg=cfg)
    assert needed, f"{question} 应触发联网"


def test_local_mode_never_goes_online(cfg):
    assert needs_online("今天天气", 0.1, DecisionMode.LOCAL, cfg=cfg) == (False, None)


def test_online_mode_always_goes_online(cfg):
    needed, reason = needs_online("随便", 1.0, DecisionMode.ONLINE, cfg=cfg)
    assert needed and "强制" in reason


def test_force_flag_overrides_local_mode(cfg):
    needed, _ = needs_online("随便", 1.0, DecisionMode.LOCAL, force=True, cfg=cfg)
    assert needed


def test_keyword_list_returns_matches(cfg):
    hits = contains_time_sensitive_keywords("今天的价格是多少", cfg)
    assert "今天" in hits and "价格" in hits


def test_options_are_not_sent_by_default(cfg):
    set_upload_options(True, cfg)
    query = build_search_query("去哪玩", OPTIONS, upload_options_enabled(cfg))
    assert query == "去哪玩", "隐私开关开启时只发送问题"


def test_options_sent_when_switch_off(cfg):
    set_upload_options(False, cfg)
    query = build_search_query("去哪玩", OPTIONS, upload_options_enabled(cfg))
    assert "露营" in query and "古镇" in query


def test_cache_roundtrip(cfg, monkeypatch):
    """Same query within the TTL returns the cached payload."""

    import core.online as online

    calls = {"n": 0}

    def fake_search(query, cfg, provider=None, limit=None):
        calls["n"] += 1
        from core.search import SearchResult

        return [SearchResult(title="T", url="https://x.com", snippet="S", domain="x.com")]

    monkeypatch.setattr(online, "search", fake_search)
    first, cached1 = cached_search("缓存测试查询", cfg)
    second, cached2 = cached_search("缓存测试查询", cfg)
    assert calls["n"] == 1, "第二次应命中缓存"
    assert cached1 is False and cached2 is True
    assert first == second


def test_cache_expires_after_ttl(cfg, monkeypatch):
    import core.online as online

    monkeypatch.setattr(online, "_load_cache", lambda cfg: {
        online._cache_key("过期查询"): {"ts": time.time() - 48 * 3600, "results": [{"title": "old"}]}
    })
    monkeypatch.setattr(online, "_save_cache", lambda cfg, data: None)

    def fake_search(query, cfg, provider=None, limit=None):
        from core.search import SearchResult

        return [SearchResult(title="new", url="u", snippet="s", domain="d")]

    monkeypatch.setattr(online, "search", fake_search)
    results, from_cache = cached_search("过期查询", cfg)
    assert from_cache is False
    assert results[0]["title"] == "new"
