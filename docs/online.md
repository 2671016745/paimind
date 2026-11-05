# 联网求助 / Online fallback

## 触发条件 / Triggers

默认本地，联网为兜底。**任一条件满足即触发**：
> *Local by default, online as a fallback. Any one of these triggers it:*

| 条件 / Condition | 默认值 / Default |
|---|---|
| top1 置信度 < 阈值 / confidence below threshold | `0.6`（`decision.confidence_threshold`） |
| 问题含时效性关键词 / time-sensitive keyword | 今天 / 最新 / 现在 / 价格 / 新闻 / 汇率 / 版本 / 发布 … |
| 用户手动强制 / user forces it | 「强制联网」按钮 / the Force-online button |

`mode` 语义 / semantics：
- `auto`：本地优先，按需联网；
  *local first, online when needed;*
- `local`：**永不联网**；
  *never goes online;*
- `online`：**总是联网**（需先确认隐私）。
  *always online (requires the privacy ack first).*

---

## RAG 两级流程 / Two-level RAG

```
① 本地模型改写检索 query
        ↓
② 调搜索接口取 top3 标题 + 摘要
        ↓
③ 摘要作为参考资料交本地模型做「带依据的决策」
        ↓
④ 输出仍为 选项 + 一句话 + 来源域名，结果区标联网徽标，可展开看来源
```

**改写失败时退回原问题**（不阻塞）。
> *If the rewrite fails, the original question is used — nothing blocks.*

---

## 搜索 API / Search providers

优先可配置搜索 API，无 key 时降级 DuckDuckGo HTML。
> *A configured search API is preferred; without a key the app degrades to DuckDuckGo HTML.*

| provider | 需要 / Requires | 配置 / Configuration |
|---|---|---|
| SerpAPI | key | `online.search.serpapi_key_ref` |
| Bing（API） | key | `online.search.bing_key_ref` |
| Brave（API） | key | `online.search.brave_key_ref` |
| **Bing（网页抓取）** | **无 / none** | 内置 / built in |
| **Mojeek（网页抓取）** | **无 / none** | 内置 / built in |
| DuckDuckGo HTML | 无 / none | `online.search.duckduckgo_url` |

`provider: auto` 时按 `serpapi → bing → brave → bing-web → mojeek → duckduckgo` 顺序尝试。
> *With `provider: auto` the order is `serpapi → bing → brave → bing-web → mojeek → duckduckgo`.*

**为什么有 Bing 网页抓取这一路 / Why a Bing web-scraper exists**：DuckDuckGo 的 HTML 端点在国内网络常被阻断（连接超时），所以加了一条同样不需要 key 的备用通路，保证「无 key 也能联网」。
> *DuckDuckGo's HTML endpoint is frequently blocked on some networks (connect timeout), so a second key-less path keeps online search usable.*

**查询是关键词式的 / Queries are keyword-style**：搜索引擎对「空格分隔的关键词」打分远高于自然语言句子 —— `上海 今天 天气` 能返回天气网站，`今天上海天气怎么样` 只会返回黄历。所以检索前会把问题关键词化（去掉口语词与判断词）。
> *Search engines score space-separated keywords far better than sentences — `上海 今天 天气` returns weather sites, `今天上海天气怎么样` returns almanacs. The question is therefore keywordised first.*

---

## 降级 / Degradation

| 情况 / Situation | 行为 / Behaviour |
|---|---|
| 超时 5 秒 | 静默回退纯本地 / silent local fallback |
| 网络失败 / 全部源失败 | 静默回退，状态栏提示「联网不可用，已用本地结果」 |
| | silent fallback with a status-bar note, **no error dialog** |
| 某个 provider 报错 | 跳过它，试下一个 / skip it and try the next |
| 改写失败 | 用原问题 / use the original question |

**绝不因联网失败让决策失败。**
> *An online failure must never fail the decision.*

---

## 隐私 / Privacy

首次联网前弹确认：
> *A confirmation appears before the first online call:*

```
将把问题发送到搜索引擎，选项默认不上传。
将把模型文件…（不，是问题）…

[仅联网、不上传选项]  ← 开关，默认开
[取消]  [继续]
```

- 默认只发**问题**，**不发选项**；
  *by default only the* question *is sent, never the options;*
- 「仅联网、不上传选项」开关默认**开**；
  *the "online only, never upload options" switch is **on** by default;*
- 关掉开关才会把选项附在 query 后面（用户已接受这个取舍）；
  *turning it off appends the options — the user accepted that trade-off;*
- 确认状态存在 `data/privacy.json`，可随时撤销。
  *the ack lives in `data/privacy.json` and can be revoked.*

API 字段 `upload_options` 对应这个开关（默认 `true` = 保护选项）。
> *The API field `upload_options` maps to this switch (default `true` = protect options).*

---

## 缓存 / Cache

相同 query 缓存 **24 小时**，存在 `data/online_cache.json`。
> *The same query is cached for **24 hours** in `data/online_cache.json`.*

- 命中缓存不会重复请求搜索引擎；
  *a cache hit avoids repeating the search;*
- 缓存过期后自动失效并重新检索；
  *expired entries are refreshed automatically;*
- 缓存**只在本地**，不上传。
  *the cache stays on the device.*

---

## 输出 / Output

联网结果的响应里 `source` 为 `online` 或 `hybrid`，并带来源列表。
> *Online results carry `source` of `online` or `hybrid` plus a source list.*

```json
{
  "source": "hybrid",
  "selected": {"id": "a", "text": "近郊露营", "confidence": 0.88},
  "reason": "今日天气晴朗，适合户外",
  "sources": [
    {"title": "周末天气预报", "url": "https://weather.example.com/x", "domain": "weather.example.com"}
  ]
}
```

结果区标**联网徽标**（蓝色），可展开看来源。
> *The result card shows a blue online badge and can expand to reveal the sources.*

---

## 端点 / Endpoints

| 端点 / Endpoint | 说明 / Description |
|---|---|
| `GET /v1/decide/online-consent` | 查询确认状态与开关 / read consent + switch |
| `POST /v1/decide/online-consent` | `{"granted": true\|false}` 记录或撤销 / record or revoke |
