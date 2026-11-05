"""Prompt construction and model-output parsing.

Two prompts exist:

* **decision** — "here is a question and N options, pick one, give a <=20 char
  reason and a confidence".  Used both locally and (with the search summaries
  injected) for the evidence-backed second pass.
* **query rewrite** — turns the question into a search query.  The options are
  *not* sent to the search engine by default (see the privacy switch).

The model is asked for a strict JSON object; :func:`parse_decision` then tolerates
markdown fences, trailing prose and missing fields, because small INT4 models are
not perfectly instruction-following.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .schemas import Alternative, Language, Option

MAX_REASON_LENGTH = 20

SYSTEM_ZH = (
    "你是一个决策助手。你会收到一个问题和若干选项，必须从中选出最合适的一个。"
    "只输出 JSON，不要输出任何其他文字。"
)
SYSTEM_EN = (
    "You are a decision assistant. You are given a question and several options; "
    "choose the single best one. Output JSON only, no other text."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class ParsedDecision:
    selected_id: Optional[str] = None
    reason: str = ""
    confidence: float = 0.0
    alternatives: List[Alternative] = field(default_factory=list)
    raw: str = ""


def _system_prompt(language: Language) -> str:
    return SYSTEM_EN if language == Language.EN else SYSTEM_ZH


def _format_options(options: Sequence[Option]) -> str:
    return "\n".join(f"{i + 1}. [{o.id}] {o.text}" for i, o in enumerate(options))


def build_decision_prompt(
    question: str,
    options: Sequence[Option],
    context: Optional[str] = None,
    language: Language = Language.ZH,
    sources: Optional[Sequence[Dict[str, str]]] = None,
    max_reason_length: int = MAX_REASON_LENGTH,
) -> Tuple[str, str]:
    """Return ``(system, user)`` for the decision step."""

    lines: List[str] = []
    if language == Language.ZH:
        lines.append(f"问题：{question}")
        if context:
            lines.append(f"背景：{context}")
        if sources:
            lines.append("参考资料（可能不完整，请自行判断可信度）：")
            for i, s in enumerate(sources, 1):
                title = s.get("title", "")
                snippet = s.get("snippet") or s.get("summary") or ""
                domain = s.get("domain", "")
                lines.append(f"  [{i}] {title}（{domain}）\n      {snippet}")
            lines.append("请结合参考资料做判断；若资料与问题无关，请忽略它们。")
        lines.append("选项：")
        lines.append(_format_options(options))
        lines.append(
            f"请只输出如下 JSON（reason 不超过 {max_reason_length} 个字，"
            "confidence 为 0 到 1 之间的小数）："
        )
        lines.append(
            "注意：reason 必须解释你选中的那个选项为什么合适，不要描述其他选项。"
        )
        lines.append(
            '{"selected": "<选项 id>", "reason": "<为什么选该选项，一句话>", '
            '"confidence": 0.85, "alternatives": [{"id": "<选项 id>", "score": 0.7}]}'
        )
    else:
        lines.append(f"Question: {question}")
        if context:
            lines.append(f"Context: {context}")
        if sources:
            lines.append("Reference material (may be incomplete; judge credibility yourself):")
            for i, s in enumerate(sources, 1):
                title = s.get("title", "")
                snippet = s.get("snippet") or s.get("summary") or ""
                domain = s.get("domain", "")
                lines.append(f"  [{i}] {title} ({domain})\n      {snippet}")
            lines.append(
                "Decide using the references; if they are irrelevant, ignore them."
            )
        lines.append("Options:")
        lines.append(_format_options(options))
        lines.append(
            f"Output only this JSON (reason <= {max_reason_length} chars, "
            "confidence between 0 and 1):"
        )
        lines.append(
            "Note: the reason must explain why the option you SELECTED is the best "
            "fit; do not describe other options."
        )
        lines.append(
            '{"selected": "<option id>", "reason": "<why THIS option, one short line>", '
            '"confidence": 0.85, "alternatives": [{"id": "<option id>", "score": 0.7}]}'
        )

    return _system_prompt(language), "\n".join(lines)


def build_query_prompt(
    question: str,
    language: Language = Language.ZH,
) -> Tuple[str, str]:
    """Rewrite the question into **keyword-style** search terms.

    Search engines (especially Bing's HTML endpoint used as the key-less
    fallback) return far better results for space-separated keywords than for a
    natural-language sentence: ``上海 今天 天气`` works, ``今天上海天气怎么样`` does
    not.  So the model is explicitly asked for keywords only.
    """

    if language == Language.ZH:
        system = (
            "你是一个搜索关键词提取器。把用户问题改写成 3-8 个空格分隔的搜索关键词，"
            "去掉「怎么/为什么/帮我/请问」等口语词和标点，只输出关键词本身。"
        )
        user = f"问题：{question}\n\n只输出关键词，用空格分隔，不要解释。"
    else:
        system = (
            "You extract search keywords. Rewrite the question into 3-8 "
            "space-separated search keywords, dropping filler words and "
            "punctuation. Output the keywords only."
        )
        user = f"Question: {question}\n\nOutput keywords only, space-separated."
    return system, user


def parse_decision(
    text: str,
    options: Sequence[Option],
    max_reason_length: int = MAX_REASON_LENGTH,
) -> ParsedDecision:
    """Parse the model's JSON, degrading gracefully when it misbehaves.

    Small quantised models frequently emit markdown fences, trailing commentary or
    a missing field.  Rather than failing the whole request we:

    1. extract the first ``{...}`` block (or the first JSON-looking line);
    2. ``json.loads`` it, falling back to a permissive key/value scan;
    3. validate the selected id against the option list — an unknown id is
       rejected and the best-scoring alternative is used instead;
    4. clamp confidence to [0, 1] and trim the reason.
    """

    raw = (text or "").strip()
    payload = _extract_json(raw, options)

    selected_id = _clean_str(payload.get("selected") or payload.get("choice") or payload.get("id")) if payload else None
    reason = _clean_str(payload.get("reason") or payload.get("explanation") or "") if payload else ""
    confidence = _clean_float(payload.get("confidence"), default=0.0) if payload else 0.0

    alternatives = _parse_alternatives(payload.get("alternatives"), options) if payload else []
    if not alternatives and selected_id:
        # No scores from the model: fall back to the declared confidence
        alternatives = [Alternative(id=selected_id, text=_option_text(options, selected_id),
                                    score=_clamp01(confidence))]
        alternatives += [
            Alternative(id=o.id, text=o.text, score=0.0)
            for o in options if o.id != selected_id
        ]

    valid_ids = {o.id for o in options}
    if selected_id not in valid_ids:
        best = max(alternatives, key=lambda a: a.score, default=None)
        if best is not None and best.id in valid_ids and best.score > 0:
            # The model picked an id we never offered — trust the scored alternative
            selected_id = best.id
            confidence = best.score
        else:
            selected_id = None

    if selected_id is None and options:
        # Total garbage output: fall back to the first option with low confidence
        selected_id = options[0].id
        confidence = min(confidence, 0.3)

    reason = _trim_reason(reason, max_reason_length)
    return ParsedDecision(
        selected_id=selected_id,
        reason=reason,
        confidence=max(0.0, min(1.0, confidence)),
        alternatives=sorted(alternatives, key=lambda a: a.score, reverse=True),
        raw=raw,
    )


def _extract_json(text: str, options: Sequence[Option]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    candidates: List[str] = []

    m = _JSON_RE.search(text)
    if m:
        candidates.append(m.group(0))

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    candidates.append(text)

    for cand in candidates:
        obj = _try_json(cand)
        if obj is not None:
            return obj

    return _permissive_scan(text, options)


def _try_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _permissive_scan(text: str, options: Sequence[Option]) -> Optional[Dict[str, Any]]:
    """Last resort: pull ``"key": value`` pairs out of malformed output.

    If no JSON keys can be found at all, look for a bare option id mentioned in
    free text (``模型说：选 a 吧``) — small models frequently skip the JSON.
    """

    payload: Dict[str, Any] = {}
    for key in ("selected", "choice", "id", "reason", "explanation", "confidence"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
        if m:
            payload[key] = m.group(1)
    m = re.search(r'"confidence"\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if m:
        payload["confidence"] = m.group(1)
    alts: List[Dict[str, Any]] = []
    for am in re.finditer(r'\{\s*"id"\s*:\s*"([^"]*)"\s*,\s*"score"\s*:\s*(-?\d+(?:\.\d+)?)\s*\}', text):
        alts.append({"id": am.group(1), "score": float(am.group(2))})
    if alts:
        payload["alternatives"] = alts

    if not payload and options:
        valid = {o.id for o in options}
        # Only accept a *standalone* token, so "a" inside a word is not a match.
        for o in options:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(o.id)}(?![A-Za-z0-9_])", text):
                payload["selected"] = o.id
                payload["reason"] = text.strip()
                payload["confidence"] = 0.4
                break

    return payload or None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_alternatives(raw: Any, options: Sequence[Option]) -> List[Alternative]:
    if not isinstance(raw, list):
        return []
    out: List[Alternative] = []
    valid_ids = {o.id for o in options}
    for item in raw:
        if isinstance(item, str):
            if item in valid_ids:
                out.append(Alternative(id=item, text=_option_text(options, item), score=0.0))
            continue
        if not isinstance(item, dict):
            continue
        oid = _clean_str(item.get("id") or item.get("option"))
        if oid not in valid_ids:
            continue
        out.append(Alternative(
            id=oid,
            text=_option_text(options, oid),
            score=_clean_float(item.get("score") or item.get("confidence"), default=0.0),
        ))
    return out


def _option_text(options: Sequence[Option], option_id: str) -> str:
    for o in options:
        if o.id == option_id:
            return o.text
    return ""


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    return text or None


def _clean_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, str):
            m = _NUMBER_RE.search(value)
            if not m:
                return default
            return float(m.group(0))
        return float(value)
    except (TypeError, ValueError):
        return default


def _trim_reason(reason: str, max_length: int) -> str:
    reason = reason.strip()
    if len(reason) <= max_length:
        return reason
    head = reason[:max_length]
    # 尽量在标点处断开，避免「预算500足」这种半截词
    cuts = [head.rfind(p) for p in "，。；、！？：,.!?;:"]
    cut = max(cuts)
    if cut >= max_length // 2:
        return head[:cut].rstrip("，。；、！？：,.!?;: ")
    return head
