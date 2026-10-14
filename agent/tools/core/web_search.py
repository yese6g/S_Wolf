"""
agent/tools/web_search.py - 联网搜索工具（Agent 的「眼睛」）

【这个文件干什么】
    两种搜索后端 + 一个给模型用的工具定义（TOOLS）。
      1. Tavily（需要 Key，专为 LLM 设计，质量最好）；
      2. 自研爬虫：直接抓 Bing 结果页 HTML 并解析（免 Key）。
        （百度对无 Cookie 请求会返回"安全验证"页，实测拿不到结果，已移除。）

【谁在调用】
    - 模型 → 通过 agent/tools/__init__.py 的注册表调用 `web_search`（本文件底部 TOOLS）；
    - SilverWolf.py 的「测试搜索」接口 → 直接调用 `search()` 验证某一层是否可用；
    - 历史记录/自我维护链路不受影响。

【Tavily 开关】
    Tavily 消耗额度，所以支持在「设置 → Agent 能力」里关掉（ui_state.tavily_enabled）。
    engine="auto" 时：开关打开且配了 Key 才用 Tavily，否则直接走内置爬虫。
    也允许显式 engine="tavily" 强制使用（例如刚配好 Key 想验证一次）。

【自研爬虫的注意事项（写在代码里免得踩）】
    - 搜索引擎有反爬：必须带常见浏览器 UA，并控制频率（限速逻辑在 _web.throttle）；
    - 解析依赖页面结构，站点改版就会解析失败 —— 所以失败原因要**分层报出来**
      （是没配 Key、被拦、还是没结果），方便一眼定位；
    - 请遵守目标站点的 robots.txt 与使用条款，不要用于批量采集。

【结果质量与次数控制（v2.1.2 加入）】
    搜索引擎（尤其免 Key 爬虫）会把"导航站 / 工具大全 / 百科 / 问答"混进前排：
    标题看着相关，点进去没有一手信息。实测用户搜"AI 模型和价格的新消息"时，
    前 7 条全是这类站点，模型只能回一句"搜索结果全是导航站，没用"。
    所以这里做了三件事：
      1. `_rank_results()`：丢导航农场站、官方/一手来源置顶、同域名最多留 2 条；
      2. 结果条数收敛（默认 3 条，最多 5 条）—— 条数越多，进上下文的 token 越多；
      3. 配合 `_run_state`：同一轮里重复查询不重复请求，并给搜索/抓页设次数上限。
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Dict, List

import httpx

from .._web import (
    BROWSER_UA,
    HTTP_TIMEOUT,
    parse_soup,
    read_setting,
    read_setting_bool,
    strip_html,
    throttle,
)
from .. import _run_state as run_state

logger = logging.getLogger(__name__)

# 默认/最大返回条数：3 条通常就够判断，5 条是上限（多了只是烧 token）
DEFAULT_MAX_RESULTS = 3
MAX_MAX_RESULTS = 5

# ── 结果质量：域名分级 ──
# 第一档（丢弃）：AI 工具导航站、工具大全、教程农场 —— 标题相关但没一手信息
_NAV_FARM_HINTS = (
    "ai-bot.cn", "aigc.cn", "askgo.ai", "chatgpt-chinese.com", "chooseai.net",
    "jimeng.jianying.com", "ai-tools", "toolnav",
)
# 第二档（降权）：百科/问答页 —— 有信息但不是新闻与官方口径
_LOW_VALUE_HINTS = ("baike.baidu.com", "zhidao.baidu.com", "zhihu.com/question", "wikipedia.org")
# 第三档（置顶）：厂商官方域名 —— 模型应该优先引用一手来源
_OFFICIAL_HINTS = (
    "openai.com", "anthropic.com", "blog.google", "ai.google.dev", "deepmind.google",
    "deepseek.com", "moonshot.cn", "bigmodel.cn", "aliyuncs.com", "aliyun.com",
    "volcengine.com", "mistral.ai", "x.ai", "meta.com", "nvidia.com",
    "microsoft.com", "github.com", "qwen.ai", "tencent.com",
)
# 第四档（小幅加权）：科技/财经媒体 —— 比内容农场可靠，但仍是二手信息
_NEWS_HINTS = (
    "reuters.com", "bloomberg.com", "theverge.com", "techcrunch.com", "36kr.com",
    "21jingji.com", "yicai.com", "stcn.com", "people.com.cn", "xinhuanet.com",
    "caixin.com", "jiemian.com", "tmtpost.com", "ithome.com", "infoq.cn",
)


def _domain_score(url: str) -> int:
    """给一个结果打分：分数越高越靠前；-100 表示"直接丢弃"。"""
    u = (url or "").lower()
    if any(h in u for h in _NAV_FARM_HINTS):
        return -100
    if any(h in u for h in _LOW_VALUE_HINTS):
        return -5
    if any(h in u for h in _OFFICIAL_HINTS):
        return 5
    if any(h in u for h in _NEWS_HINTS):
        return 3
    return 0


def _rank_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    去重 + 过滤 + 排序。

    规则（顺序即优先级）：
      1. 同一个 URL 只留一条；
      2. 同一域名最多 2 条（避免整页都是同一家）；
      3. 丢掉导航农场站 —— 但如果丢完一条不剩，就保留原样
         （宁可给带噪音的结果，也不能让模型"什么都搜不到"）；
      4. 官方来源 > 科技媒体 > 其它（`sorted` 稳定，同分保持原顺序）。
    """
    seen_urls: set = set()
    per_domain: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for item in results:
        url = str(item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        domain = urllib.parse.urlsplit(url).netloc.lower()
        if per_domain.get(domain, 0) >= 2:
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        kept.append(item)
    filtered = [r for r in kept if _domain_score(r.get("url", "")) > -100]
    if filtered:
        kept = filtered
    return sorted(kept, key=lambda r: -_domain_score(r.get("url", "")))


def _search_tavily(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Tavily 搜索后端（需要在设置里填 tavily_api_key）。"""
    key = read_setting("tavily_api_key")
    if not key:
        raise RuntimeError("未配置 TAVILY_API_KEY")
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": max_results, "search_depth": "basic"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": strip_html(item.get("content", ""))[:400],
        }
        for item in data.get("results", [])[:max_results]
    ]


def _search_bing_html(query: str, max_results: int) -> List[Dict[str, Any]]:
    """
    Bing 结果页解析（免 Key）。

    结构：结果条目是 <li class="b_algo">，标题在 <h2><a href="...">，
    摘要在该条目内的 <p>（或 .b_caption p）。Bing 的结构多年比较稳定，作为爬虫首选。
    """
    throttle()
    resp = httpx.get(
        "https://www.bing.com/search",
        params={"q": query, "setlang": "zh-CN", "cc": "CN", "count": max_results * 2},
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        follow_redirects=True,
    )
    resp.raise_for_status()
    soup = parse_soup(resp.text)
    if soup is None:
        raise RuntimeError("BeautifulSoup 不可用")

    results: List[Dict[str, Any]] = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a[href]")
        if not link:
            continue
        url = link.get("href", "")
        if not url.startswith("http"):
            continue
        snippet_node = item.select_one(".b_caption p") or item.select_one("p")
        results.append({
            "title": strip_html(link.get_text(" ", strip=True))[:200],
            "url": url,
            "snippet": strip_html(snippet_node.get_text(" ", strip=True))[:400] if snippet_node else "",
        })
        if len(results) >= max_results:
            break
    if not results:
        # 明确区分「被反爬拦了」和「真的没结果」，方便排查
        raise RuntimeError("Bing 未解析到结果（可能被反爬拦截或页面结构变化）")
    return results


def search(query: str, max_results: int = DEFAULT_MAX_RESULTS, engine: str = "auto") -> Dict[str, Any]:
    """
    执行联网搜索，返回统一结构。

    返回值：{"ok": bool, "engine": str, "results": [...], "error": str, "fetched_at": str}
    - engine 记录实际使用的后端（前端展示 & 排查问题用）
    - 默认 auto：
        * 用户开启 Tavily（ui_state.tavily_enabled，默认开启）且配了 Key → tavily → bing-html 依次尝试；
        * 用户关闭 Tavily 或没配 Key → 直接走 bing-html（不消耗任何额度）；
    - 也可以显式指定 engine="tavily" / "bing" 强制走某个后端
      （例如刚配好 Key 想单独验证一次，或想对比爬虫结果）；
    - 全部失败时 ok=False 并带上原因，
      模型可以据此告诉用户「这次没连上搜索」而不是编造内容。
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "engine": "", "results": [], "error": "查询词为空"}
    # 条数收敛：默认 3 条、最多 5 条（结果会进上下文，条数越多越烧 token）
    max_results = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_MAX_RESULTS))

    tavily_backend = ("tavily", _search_tavily)
    bing_backend = ("bing-html", _search_bing_html)

    engine_key = (engine or "auto").strip().lower()
    if engine_key in ("bing", "bing-html"):
        backends = (bing_backend,)
    elif engine_key == "tavily":
        backends = (tavily_backend,)
    else:
        # auto：是否使用 Tavily 由设置开关决定（关掉就不碰额度）
        tavily_enabled = read_setting_bool("tavily_enabled", True) and bool(read_setting("tavily_api_key"))
        backends = (tavily_backend, bing_backend) if tavily_enabled else (bing_backend,)

    errors: List[str] = []
    for name, fn in backends:
        try:
            results = fn(query, max_results)
            if results:
                # 质量排序放在这里：无论哪条后端（Tavily / 爬虫）都走同一套过滤
                results = _rank_results(results)[:max_results]
                logger.info("联网搜索成功: %s（%d 条）", name, len(results))
                return {
                    "ok": True,
                    "engine": name,
                    "results": results,
                    "error": "",
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            errors.append(f"{name}: 无结果")
        except Exception as e:  # 网络不通 / 缺少配置 / 页面结构变化
            errors.append(f"{name}: {str(e)[:120]}")
            logger.warning("搜索后端 %s 失败: %s", name, e)
    return {"ok": False, "engine": "", "results": [], "error": "; ".join(errors)[:400]}


# ── 给模型用的工具 ──

def _tool_web_search(args: Dict[str, Any]):
    """工具入口：把搜索结果整理成模型好读的文本 + 前端要展示的来源列表。"""
    query = str(args.get("query", "")).strip()
    max_results = int(args.get("max_results") or DEFAULT_MAX_RESULTS)
    # engine 可选：auto（默认，按设置决定用不用 Tavily，否则走 Bing 爬虫）/ tavily / bing
    engine = str(args.get("engine") or "auto")

    # ── 本轮记账：重复查询直接复用、超过次数上限就劝模型收手 ──
    # 子代理经 run["tree"] 共享整棵委托树的缓存与额度，不各拿一份新配额
    run = run_state.state()
    cache_key = run_state.normalize_query(query)
    box = run.get("tree") if run is not None else None
    if box is None:
        box = run
    if run is not None:
        cached = box["search_cache"].get(cache_key)
        if cached:
            logger.info("同一轮重复搜索，复用缓存: %s", query)
            return (
                "（这个查询你刚才已经搜过，下面是同一批结果，不要再重复搜同一个问题）\n" + cached["text"],
                cached["meta"],
            )
        if box["searches"] >= run["max_searches"]:
            logger.info("本轮搜索次数已达上限（%d），拒绝继续搜索", run["max_searches"])
            return (
                f"本轮搜索次数已达上限（{run['max_searches']} 次）。"
                "别再调用搜索了 —— 用已经拿到的资料直接把结论写出来；"
                "确实查不到的部分，就明确说这部分没查到，并给出你建议的下一步。",
                {"ok": True, "query": query, "sources": [], "engine": "limit-reached"},
            )

    result = search(query, max_results, engine=engine)
    # 失败也占名额：失败调用照样烧满网络超时，只记成功的话连续失败能无限重试绕过上限
    if run is not None:
        box["searches"] += 1
    if not result["ok"]:
        return (f"联网搜索失败：{result['error']}",
                {"ok": False, "query": query, "sources": [], "engine": result.get("engine", "")})
    lines = [
        f"搜索「{query}」命中 {len(result['results'])} 条"
        f"（引擎：{result['engine']}，抓取时间：{result.get('fetched_at', '未知')}）："
    ]
    for i, item in enumerate(result["results"], 1):
        lines.append(f"{i}. {item['title']}\n   URL: {item['url']}\n   摘要: {item['snippet']}")
    text = "\n".join(lines)
    meta = {
        "ok": True, "query": query, "engine": result["engine"], "sources": result["results"],
    }
    if run is not None:
        # 缓存整段结果：同一轮里再问同一个问题就直接复用，不重复发请求
        box["search_cache"][cache_key] = {"text": text, "meta": meta}
    return text, meta


TOOLS = {
    "web_search": {
        "description": "联网搜索最新信息（新闻/文档/资料），拿到搜索结果摘要。"
                       "用法纪律：一次搜准比搜多次重要 —— 先用最具体的查询词搜一次，"
                       "结果不够再换个说法补一次，同一轮最多搜 6 次；"
                       "已经搜过的查询会直接复用结果，不要重复搜。"
                       "搜索结果只是摘要，需要细节再对具体 URL 调 fetch_page。"
                       "拿到资料后要直接给结论，不要把'我在搜索'当成回答。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词。要具体：带主体 + 时间 + 想要的维度"
                                   "（例：'DeepSeek V4 API 价格 调整 2026' 优于 'AI 最新消息'）。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回条数，默认 3、最多 5。条数越多越占用上下文，够用即可。",
                },
                "engine": {
                    "type": "string",
                    "description": "可选：auto（默认）/ tavily / bing。Tavily 额度紧张或未配置时可指定 bing 走免 Key 爬虫。",
                },
            },
            "required": ["query"],
        },
        "handler": _tool_web_search,
    },
}
