"""Online fallback: trigger rules, RAG two-level flow, caching, privacy.

Trigger (any one is enough):

* top-1 confidence below ``decision.confidence_threshold`` (default 0.6);
* the question contains a time-sensitive keyword (今天/最新/价格/新闻/汇率/...);
* the user forced online.

Flow
----
1. the local model rewrites the question into a search query;
2. the search provider returns the top N titles + snippets;
3. those summaries become "reference material" for a second local pass that
   decides *with evidence*;
4. the result is still "selected + one line + source domains", but flagged as
   ``online``/``hybrid``.

Degradation: if the query rewrite fails, the raw question is used.  If the search
fails or times out (5 s), the app silently returns the local result and shows
"联网不可用，已用本地结果" in the status bar — no error dialog.

Privacy: the question is sent to the search engine; **options are not**, unless
the user explicitly turns the "仅联网、不上传选项" switch off.  A one-time
confirmation is required before the first online call.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Config, get_config
from .schemas import DecisionMode, Language, Option
from .search import search

DEFAULT_KEYWORDS_FALLBACK = [
    "今天", "最新", "现在", "价格", "新闻", "汇率", "版本", "发布", "本周", "当前", "实时",
    "today", "latest", "now", "price", "news", "exchange rate", "version", "release",
]


# --------------------------------------------------------------------------
# Trigger
# --------------------------------------------------------------------------


def needs_online(
    question: str,
    confidence: float,
    mode: DecisionMode,
    force: bool = False,
    cfg: Optional[Config] = None,
) -> Tuple[bool, Optional[str]]:
    """Decide whether the local result is good enough, and why not if it isn't."""

    cfg = cfg or get_config()

    if force or mode == DecisionMode.ONLINE:
        return True, "用户强制联网"

    if mode == DecisionMode.LOCAL:
        return False, None

    threshold = float(cfg.get("decision.confidence_threshold", 0.6))
    if confidence and confidence < threshold:
        return True, f"本地置信度 {confidence:.2f} 低于阈值 {threshold:.2f}"

    keywords = cfg.get("decision.online_keywords") or DEFAULT_KEYWORDS_FALLBACK
    lowered = question.lower()
    for kw in keywords:
        if str(kw).lower() in lowered:
            return True, f"问题含时效性关键词「{kw}」"

    return False, None


def contains_time_sensitive_keywords(question: str, cfg: Optional[Config] = None) -> List[str]:
    cfg = cfg or get_config()
    keywords = cfg.get("decision.online_keywords") or DEFAULT_KEYWORDS_FALLBACK
    lowered = question.lower()
    return [str(kw) for kw in keywords if str(kw).lower() in lowered]


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _cache_path(cfg: Config) -> Path:
    env = os.environ.get("PAIMIND_ONLINE_CACHE")
    if env:
        return Path(env)
    root = Path(cfg.get("app.data_dir", "data")) or Path("data")
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root / "online_cache.json"


def _load_cache(cfg: Config) -> Dict[str, Any]:
    path = _cache_path(cfg)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cfg: Config, data: Dict[str, Any]) -> None:
    path = _cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:24]


def cached_search(
    query: str,
    cfg: Optional[Config] = None,
    provider: Optional[str] = None,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], bool]:
    """Return ``(results, from_cache)`` for *query*, honouring the 24 h TTL."""

    cfg = cfg or get_config()
    ttl_hours = float(cfg.get("online.cache_ttl_hours", 24))
    key = _cache_key(query)
    cache = _load_cache(cfg)

    entry = cache.get(key)
    if entry and (time.time() - float(entry.get("ts", 0))) < ttl_hours * 3600:
        return list(entry.get("results", [])), True

    results = search(query, cfg, provider=provider, limit=limit)
    serialisable = [
        {"title": r.title, "url": r.url, "snippet": r.snippet, "domain": r.domain}
        for r in results
    ]
    cache[key] = {"ts": time.time(), "query": query, "results": serialisable}
    cache = {k: v for k, v in cache.items()
             if (time.time() - float(v.get("ts", 0))) < ttl_hours * 3600}
    _save_cache(cfg, cache)
    return serialisable, False


def clear_cache(cfg: Optional[Config] = None) -> None:
    cfg = cfg or get_config()
    _save_cache(cfg, {})


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------


def privacy_path(cfg: Config) -> Path:
    root = Path(cfg.get("app.data_dir", "data")) or Path("data")
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root / "privacy.json"


def _privacy_state(cfg: Config) -> Dict[str, Any]:
    path = privacy_path(cfg)
    if not path.exists():
        return {"online_ack": False, "upload_options": True}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"online_ack": False, "upload_options": True}


def _save_privacy_state(cfg: Config, state: Dict[str, Any]) -> None:
    path = privacy_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def online_consent_given(cfg: Optional[Config] = None) -> bool:
    cfg = cfg or get_config()
    return bool(_privacy_state(cfg).get("online_ack", False))


def grant_online_consent(cfg: Optional[Config] = None) -> None:
    cfg = cfg or get_config()
    state = _privacy_state(cfg)
    state["online_ack"] = True
    _save_privacy_state(cfg, state)


def upload_options_enabled(cfg: Optional[Config] = None) -> bool:
    """``True`` (default) means the privacy switch is ON: options stay on device."""

    cfg = cfg or get_config()
    return bool(_privacy_state(cfg).get("upload_options", True))


def set_upload_options(enabled: bool, cfg: Optional[Config] = None) -> None:
    cfg = cfg or get_config()
    state = _privacy_state(cfg)
    state["upload_options"] = bool(enabled)
    _save_privacy_state(cfg, state)


def build_search_query(
    question: str,
    options: Sequence[Option],
    keep_options_private: bool,
) -> str:
    """The query actually sent to the search engine.

    ``keep_options_private`` is the 「仅联网、不上传选项」switch, **on by
    default**: only the question leaves the device.  Turning it off appends the
    option texts so the engine can disambiguate (the user accepted that trade-off
    in the UI).
    """

    q = question.strip()
    if keep_options_private:
        return q
    texts = [o.text.strip() for o in options if o.text.strip()]
    if not texts:
        return q
    return f"{q}（选项：{' / '.join(texts)}）"


# --------------------------------------------------------------------------
# RAG second pass
# --------------------------------------------------------------------------


def online_sources_payload(results: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("snippet", ""),
            "domain": r.get("domain", ""),
        }
        for r in results
    ]


def source_domains(results: Sequence[Dict[str, str]]) -> List[str]:
    seen: List[str] = []
    for r in results:
        d = (r.get("domain") or "").lower()
        if d and d not in seen:
            seen.append(d)
    return seen
