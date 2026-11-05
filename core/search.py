"""Search providers with a key-less fallback.

Order of preference: SerpAPI -> Bing -> Brave -> DuckDuckGo HTML.

The first three need an API key (stored in the system keyring, referenced by name
in ``config.yaml``); if none is configured the app silently falls back to
DuckDuckGo's HTML endpoint, which needs no key at all.  A provider that errors is
skipped, so one broken key never blocks the whole online path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .config import Config, get_config
from .errors import AppError, ErrorCode


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    provider: str = ""


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def _serpapi(query: str, key: str, limit: int, timeout: float) -> List[SearchResult]:
    r = httpx.get(
        "https://serpapi.com/search.json",
        params={"q": query, "engine": "google", "num": limit, "key": key},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise AppError(ErrorCode.HF_UPSTREAM_ERROR, f"SerpAPI 错误：{data['error']}")
    out: List[SearchResult] = []
    for item in (data.get("organic_results") or [])[:limit]:
        url = item.get("link", "")
        out.append(SearchResult(
            title=item.get("title", ""), url=url,
            snippet=item.get("snippet", ""), domain=domain_of(url), provider="serpapi",
        ))
    return out


def _bing(query: str, key: str, limit: int, timeout: float) -> List[SearchResult]:
    r = httpx.get(
        "https://api.bing.microsoft.com/v7.0/search",
        params={"q": query, "count": limit},
        headers={"Ocp-Apim-Subscription-Key": key},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    out: List[SearchResult] = []
    for item in (data.get("webPages", {}).get("value") or [])[:limit]:
        url = item.get("url", "")
        out.append(SearchResult(
            title=item.get("name", ""), url=url,
            snippet=item.get("snippet", ""), domain=domain_of(url), provider="bing",
        ))
    return out


def _mojeek(query: str, limit: int, timeout: float) -> List[SearchResult]:
    """Mojeek HTML — no key required (may rate-limit bots)."""

    r = httpx.get(
        "https://www.mojeek.com/search",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; paimind/1.0)"},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    out: List[SearchResult] = []
    for href, title in re.findall(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*class="ob"[^>]*>(.*?)</a>', r.text, re.DOTALL
    )[:limit]:
        out.append(SearchResult(
            title=_TAG_RE.sub("", title).strip(), url=href,
            snippet="", domain=domain_of(href), provider="mojeek",
        ))
    return out


_BING_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _bing_web(query: str, limit: int, timeout: float) -> List[SearchResult]:
    """Bing HTML scraping — no API key required.

    DuckDuckGo's HTML endpoint is blocked in some networks (notably mainland
    China), so a second key-less provider keeps the online path usable there.
    Results come from ``<li class="b_algo">`` blocks.
    """

    r = httpx.get(
        "https://cn.bing.com/search",
        params={"q": query, "count": limit, "setlang": "zh-cn"},
        headers={"User-Agent": _BING_WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()

    out: List[SearchResult] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', r.text, re.DOTALL)[:limit]:
        clean = re.sub(r"<link[^>]*>", "", block)
        match = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', clean, re.DOTALL)
        if not match:
            continue
        url = match.group(1).strip()
        if url.startswith("/"):
            continue
        title = _TAG_RE.sub("", match.group(2)).strip()
        para = re.search(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', clean, re.DOTALL) \
            or re.search(r"<p[^>]*>(.*?)</p>", clean, re.DOTALL)
        snippet = _TAG_RE.sub("", para.group(1)).strip() if para else ""
        out.append(SearchResult(
            title=title, url=url, snippet=snippet,
            domain=domain_of(url), provider="bing-web",
        ))
    return out


def _brave(query: str, key: str, limit: int, timeout: float) -> List[SearchResult]:
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    out: List[SearchResult] = []
    for item in (data.get("web", {}).get("results") or [])[:limit]:
        url = item.get("url", "")
        out.append(SearchResult(
            title=item.get("title", ""), url=url,
            snippet=item.get("description", ""), domain=domain_of(url), provider="brave",
        ))
    return out


_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL
)
_DDG_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_DDG_UDDg = re.compile(r"\buddg=([^&]+)")


def _duckduckgo(query: str, endpoint: str, limit: int, timeout: float) -> List[SearchResult]:
    """DuckDuckGo HTML endpoint — no key required.

    Result links are redirect wrappers (``//duckduckgo.com/l/?uddg=<encoded>``), so
    the real target is recovered from the ``uddg`` parameter.
    """

    r = httpx.post(
        endpoint,
        data={"q": query, "b": "", "kl": "wt-wt"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; paimind/1.0)"},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    html = r.text

    links = _DDG_RESULT_RE.findall(html)
    snippets = _DDG_SNIPPET_RE.findall(html)
    out: List[SearchResult] = []
    for i, (href, title) in enumerate(links[:limit]):
        url = href
        if href.startswith("//"):
            url = "https:" + href
        m = _DDG_UDDg.search(href)
        if m:
            from urllib.parse import unquote
            url = unquote(m.group(1))
        title_text = _TAG_RE.sub("", title).strip()
        snippet = _TAG_RE.sub("", snippets[i]).strip() if i < len(snippets) else ""
        out.append(SearchResult(
            title=title_text, url=url, snippet=snippet,
            domain=domain_of(url), provider="duckduckgo",
        ))
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _token_resolver(cfg: Config) -> Callable[[str], Optional[str]]:
    """Return a callable that resolves a keyring *reference* to a secret."""

    from .keys import get_secret

    def resolve(ref: Optional[str]) -> Optional[str]:
        if not ref:
            return None
        return get_secret(ref)

    return resolve


def available_providers(cfg: Optional[Config] = None) -> List[str]:
    """Which providers can actually be used right now (key present)."""

    cfg = cfg or get_config()
    resolve = _token_resolver(cfg)
    search = cfg.section("online").get("search", {}) or {}
    out: List[str] = []
    for name, ref in (("serpapi", "serpapi_key_ref"), ("bing", "bing_key_ref"),
                      ("brave", "brave_key_ref")):
        if resolve(search.get(ref)):
            out.append(name)
    # Key-less HTML providers are always available (subject to reachability).
    out.extend(["bing-web", "mojeek", "duckduckgo"])
    return out


def search(
    query: str,
    cfg: Optional[Config] = None,
    provider: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
) -> List[SearchResult]:
    """Run a search, falling back through the provider list.

    Never raises for a *total* failure — returns an empty list so the caller can
    silently degrade to a purely local decision (per the spec: no error dialog).
    """

    cfg = cfg or get_config()
    online = cfg.section("online")
    limit = limit or int(online.get("max_results", 3))
    timeout = timeout or float(online.get("timeout_seconds", 5))
    search_cfg = online.get("search", {}) or {}
    resolve = _token_resolver(cfg)

    order = _provider_order(cfg, provider)
    errors: List[str] = []

    for name in order:
        try:
            if name == "serpapi":
                key = resolve(search_cfg.get("serpapi_key_ref"))
                if not key:
                    continue
                return _serpapi(query, key, limit, timeout)
            if name == "bing":
                key = resolve(search_cfg.get("bing_key_ref"))
                if not key:
                    continue
                return _bing(query, key, limit, timeout)
            if name == "brave":
                key = resolve(search_cfg.get("brave_key_ref"))
                if not key:
                    continue
                return _brave(query, key, limit, timeout)
            if name == "bing-web":
                return _bing_web(query, limit, timeout)
            if name == "mojeek":
                return _mojeek(query, limit, timeout)
            if name == "duckduckgo":
                endpoint = search_cfg.get("duckduckgo_url") or "https://html.duckduckgo.com/html/"
                return _duckduckgo(query, str(endpoint), limit, timeout)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
            continue

    if errors:
        raise AppError(ErrorCode.ALL_MIRRORS_FAILED, "所有搜索源均失败", {"attempts": errors})
    return []


def _provider_order(cfg: Config, requested: Optional[str]) -> List[str]:
    search_cfg = cfg.section("online").get("search", {}) or {}
    preferred = str(search_cfg.get("provider", "auto")).lower()
    if requested:
        preferred = requested.lower()

    if preferred in ("serpapi", "bing", "brave", "bing-web", "mojeek", "duckduckgo"):
        return [preferred, "bing-web", "duckduckgo"]

    order = ["serpapi", "bing", "brave", "bing-web", "mojeek", "duckduckgo"]
    if preferred == "none":
        return ["duckduckgo"]
    return order


def rewrite_query(
    question: str,
    backend: Any,
    cfg: Optional[Config] = None,
    language: str = "zh",
    timeout: float = 5.0,
) -> str:
    """Build a search query: keywordise the question, optionally refined by the model.

    Deterministic keywordisation of the original question comes first — it is
    instant and reliable.  The local model is then asked for a rewrite and its
    answer is adopted only when it yields *more* keywords, because small models
    frequently collapse the terms into one unbroken Chinese string that search
    engines score poorly.

    Falls back to the raw question if neither produces anything usable.
    """

    from .prompt import build_query_prompt
    from .inference.base import ChatMessage
    from .schemas import Language

    direct = _keywordise(question)
    model_keywords = ""

    try:
        cfg = cfg or get_config()
        lang = Language.EN if language.lower().startswith("en") else Language.ZH
        system, user = build_query_prompt(question, lang)
        completion = backend.complete(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            max_tokens=64,
            temperature=0.1,
        )
        text = (completion.text or "").strip().strip('"').strip("'")
        text = text.splitlines()[0].strip() if text else ""
        if text and len(text) <= 200:
            model_keywords = _keywordise(text)
    except Exception:
        model_keywords = ""

    if model_keywords and len(model_keywords.split()) > len(direct.split()):
        return model_keywords
    return direct or question.strip()


_FILLER = frozenset(
    "请 帮我 问一下 告诉 我想 知道 多少 怎么样 如何 为什么 是否 可以 能不能 请问 "
    "一下 看看 查查 查询 的呢 了吗 呢 吗 吧 啊 呀 的 了 是 在 有 和 与 及 或 "
    "个 些 种 是多少 有几 多久 哪里 哪个 哪些 什么 "
    # Judgement phrases: they drag the search off-topic ("上海天气 适合出游" returns
    # almanac pages instead of weather).  Only the factual terms belong in a query.
    "适合出游 适合旅行 适合 出游 值得去 值得吗 该不该 要不要 可以吗 好吗 行不行 "
    "划算吗 好不好 怎么样 好不好 推荐 比较好 最好 最佳 应该怎么 该选择".split()
)


def _keywordise(text: str, max_terms: int = 8) -> str:
    """Turn a Chinese sentence into space-separated search keywords.

    Chinese has no spaces, so punctuation splitting alone leaves one long chunk.
    We strip filler words *inside* each chunk too, which is what makes
    ``今天上海天气怎么样？适合出游吗`` become ``今天上海天气 适合出游``.
    """

    cleaned = re.sub(r"[，。？！、；：,.?!;:\"'（）()\[\]【】]", " ", text)
    for filler in sorted(_FILLER, key=len, reverse=True):
        cleaned = cleaned.replace(filler, " ")
    terms: List[str] = []
    for term in cleaned.split():
        term = term.strip()
        if len(term) > 1 and term not in terms:
            terms.append(term)
    return " ".join(terms[:max_terms])
