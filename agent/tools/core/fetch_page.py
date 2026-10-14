"""
agent/tools/fetch_page.py - 打开网页读正文的工具

【这个文件干什么】
    搜索引擎只给摘要，摘要往往不够用；这个工具负责「点进搜索结果把正文读回来」。
    抽取策略（按优先级）：
      1. Firecrawl（配置了 Key 时）：服务端渲染 + 正文提取，适合 JS 站点，质量最好；
      2. 本地抓取 + BeautifulSoup 抽正文（优先 article/main，剔除导航/脚本/页脚）；
      3. 正则去标签（bs4 不可用时的兜底）。

【谁在调用】
    模型 → 注册表 → 本文件底部的 TOOLS["fetch_page"]；
    正文抓取函数 `fetch_page()` 也可以被其它模块直接调用（它是公开函数）。

【次数控制（v2.1.2）】
    抓正文比搜索更贵（要下载整页），所以配合 `_run_state` 做了两层限制：
    同一个 URL 只抓一次（第二次直接给缓存）、一轮最多抓 6 个页面。
    实测一轮里出现过连抓 8 个网页的情况 —— 上下文和 token 都会被拖垮。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

import httpx
from urllib.parse import urlsplit
from middleware.outbound import public_addresses, request_public

from .. import _run_state as run_state
from .._web import (
    BROWSER_UA,
    extract_main_text,
    read_setting,
    strip_html,
)

logger = logging.getLogger(__name__)


def fetch_page(url: str, max_chars: int = 4000) -> Dict[str, Any]:
    """
    抓取网页并转成纯文本，供模型阅读。

    优先级与文件头一致：配了 Firecrawl Key 先交给渲染服务（能绕开直连抓不动的
    403/JS 站点/重定向），失败或没配 Key 再走本地直连。本地直连只经 request_public：
    目标必须是公网地址、有界读取、不跟随重定向。

    返回：{"ok": bool, "url": str, "title": str, "text": str, "error": str, "engine": str}
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "url": url, "title": "", "text": "", "error": "URL 必须以 http(s):// 开头"}
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ValueError('网页地址不允许包含凭据')
        # 交给谁抓都一样：先做公网地址校验，内网/特殊地址一律拒绝
        public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except (ValueError, OSError) as exc:
        return {'ok':False, 'url':url, 'title':'', 'text':'', 'error':str(exc), 'engine':'blocked'}

    # ── 1) Firecrawl（可选，需要 Key）：服务端渲染，直连抓不动的页面靠它兜底 ──
    firecrawl_key = read_setting("firecrawl_api_key")
    if firecrawl_key:
        try:
            resp = httpx.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {firecrawl_key}"},
                json={"url": url, "formats": ["markdown"]},
                timeout=httpx.Timeout(45.0, connect=10.0),
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            text = (data.get("markdown") or "").strip()[:max_chars]
            if text:
                title = (data.get("metadata", {}) or {}).get("title") or url
                return {"ok": True, "url": url, "title": title[:200], "text": text,
                        "error": "", "engine": "firecrawl"}
        except Exception as e:
            logger.warning("Firecrawl 抓取失败，改用本地解析: %s", str(e)[:150])

    # ── 2) 本地抓取 + 正文抽取 ──
    try:
        resp = request_public(url, 'GET', {'User-Agent':BROWSER_UA}, {}, [parsed.hostname],
                              max_bytes=2*1024*1024, max_chars=None, response_info=True)
    except (ValueError, OSError) as exc:
        return {'ok':False, 'url':url, 'title':'', 'text':'', 'error':str(exc), 'engine':'blocked'}
    try:
        content_type = resp['content_type']
        if "html" not in content_type and "text" not in content_type:
            return {"ok": False, "url": url, "title": "", "text": "",
                    "error": f"不支持的内容类型: {content_type}"}
        raw = resp['text']
        title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        title = strip_html(title_match.group(1)) if title_match else url
        text = extract_main_text(raw)[:max_chars]
        if not text:
            text = strip_html(raw)[:max_chars]  # 极端情况（页面全是脚本）退回纯文本
        return {"ok": True, "url": url, "title": title[:200], "text": text,
                "error": "", "engine": "bs4"}
    except Exception as e:
        logger.warning("抓取网页失败 %s: %s", url, e)
        return {"ok": False, "url": url, "title": "", "text": "", "error": str(e)[:200]}


def _tool_fetch_page(args: Dict[str, Any]):
    """工具入口：读取 URL 参数并返回正文（失败时把原因原样告诉模型）。"""
    url = str(args.get("url", "")).strip()

    # 本轮记账：同一 URL 不重复抓、超过上限就劝模型收手；
    # 子代理经 run["tree"] 共享整棵委托树的缓存与额度，不各拿一份新配额
    run = run_state.state()
    box = run.get("tree") if run is not None else None
    if box is None:
        box = run
    if run is not None:
        cached = box["fetched_urls"].get(url)
        if cached:
            logger.info("同一轮重复抓取，复用缓存: %s", url)
            return ("（这个网页你刚才已经抓过，下面是同一份正文，别再重复抓）\n" + cached["text"],
                    cached["meta"])
        if box["fetches"] >= run["max_fetches"]:
            logger.info("本轮抓取次数已达上限（%d），拒绝继续抓取", run["max_fetches"])
            return (f"本轮抓取网页次数已达上限（{run['max_fetches']} 次）。"
                    "别再抓新页面了 —— 用已经读到的东西给结论。",
                    {"ok": True, "url": url, "sources": [], "engine": "limit-reached"})

    result = fetch_page(url)
    # 失败也占名额：失败抓取同样烧满网络超时，只记成功的话连续失败能无限重试绕过上限
    if run is not None:
        box["fetches"] += 1
    if not result["ok"]:
        return f"打开网页失败：{result['error']}", {"ok": False, "url": result.get("url", "")}
    text = f"网页《{result['title']}》正文（已截断）：\n{result['text']}"
    meta = {"ok": True, "url": result["url"], "title": result["title"]}
    if run is not None:
        box["fetched_urls"][url] = {"text": text, "meta": meta}
    return text, meta


TOOLS = {
    "fetch_page": {
        "description": "打开一个网页并读取正文纯文本，用于深入阅读搜索结果。"
                       "只对确实需要细节的 1-3 个链接使用；同一个 URL 不要重复抓，"
                       "一轮最多抓 6 个页面。拿到正文后直接给结论。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "完整 http(s) 链接"}},
            "required": ["url"],
        },
        "handler": _tool_fetch_page,
    },
}
