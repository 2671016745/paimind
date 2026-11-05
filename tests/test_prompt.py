"""Prompt building and model-output parsing."""

from __future__ import annotations

import pytest

from core.prompt import build_decision_prompt, build_query_prompt, parse_decision
from core.schemas import Language, Option

OPTIONS = [Option(id="a", text="近郊露营"), Option(id="b", text="周边古镇一日游")]


def test_decision_prompt_asks_for_json():
    system, user = build_decision_prompt("周末去哪", OPTIONS, language=Language.ZH)
    assert "JSON" in user
    assert "[a] 近郊露营" in user
    assert "reason" in user


def test_decision_prompt_injects_sources_for_rag_pass():
    sources = [{"title": "T", "url": "https://x.com", "snippet": "S", "domain": "x.com"}]
    _, user = build_decision_prompt("今天天气", OPTIONS, language=Language.ZH, sources=sources)
    assert "参考资料" in user
    assert "x.com" in user


def test_query_prompt_excludes_options():
    """选项绝不进检索 query；且提示词要求「关键词式」输出。"""

    system, user = build_query_prompt("今天上海天气")
    assert "选项" not in user
    assert "选项" not in system
    assert "关键词" in system or "keywords" in system.lower()


@pytest.mark.parametrize("text,expected_id,expected_conf", [
    ('{"selected": "a", "reason": "更近", "confidence": 0.9}', "a", 0.9),
    ('```json\n{"selected":"b","reason":"便宜","confidence":0.7,"alternatives":[]}\n```', "b", 0.7),
    ('{"selected": "a", "reason": "x", "confidence": 1.4}', "a", 1.0),   # clamped
    ('{"selected": "zzz", "reason": "x", "confidence": 0.8, "alternatives": [{"id":"b","score":0.6}]}',
     "b", 0.6),                                                          # invalid id -> best alt
    ('模型说：选 a 吧', "a", 0.4),                                        # permissive scan
])
def test_parse_decision(text, expected_id, expected_conf):
    parsed = parse_decision(text, OPTIONS)
    assert parsed.selected_id == expected_id
    assert abs(parsed.confidence - expected_conf) < 1e-6


def test_reason_is_trimmed_to_limit():
    parsed = parse_decision('{"selected":"a","reason":"这是一句非常非常长的理由需要被截断","confidence":0.5}',
                            OPTIONS, max_reason_length=20)
    assert len(parsed.reason) <= 20


def test_alternatives_sorted_desc():
    parsed = parse_decision(
        '{"selected":"a","reason":"x","confidence":0.5,'
        '"alternatives":[{"id":"b","score":0.9},{"id":"a","score":0.4}]}',
        OPTIONS,
    )
    scores = [a.score for a in parsed.alternatives]
    assert scores == sorted(scores, reverse=True)


def test_empty_output_falls_back_without_crash():
    """空输出不应崩溃；退化为第一项 + 低置信度。"""

    parsed = parse_decision("", OPTIONS)
    assert parsed.selected_id == "a"
    assert parsed.confidence <= 0.3
