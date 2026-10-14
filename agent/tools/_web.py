"""
agent/tools/_web.py - 联网类工具共用的底层工具函数（内部模块，不注册成工具）

【为什么不注册】
    本文件名以 `_` 开头，`agent/tools/__init__.py` 的自动发现会跳过它。
    它只是被 web_search.py 与 fetch_page.py 共用的"零件"，模型看不到、也调不到。

【里面有什么】
    - read_setting / read_setting_bool：读配置（data/ui_state.json 优先，其次环境变量）
    - BROWSER_UA / HTTP_TIMEOUT：统一浏览器 UA 与超时（有些站点对 python UA 直接返回空页）
    - strip_html：去标签、反转义 HTML 实体
    - parse_soup：构造 BeautifulSoup（未安装时返回 None，让上层优雅降级）
    - throttle：爬虫限速（最小间隔 + 随机抖动），避免请求过快被封 IP
    - extract_main_text：从网页 HTML 里抽正文（优先 article/main，剔除导航/脚本等噪声）

【调用方】
    web_search.py（搜索后端）与 fetch_page.py（正文抓取）都从这里 import。
    拆出来的原因：以前这两个功能写在同一个文件里互相借用私有函数，
    一旦只想改搜索、不想动抓取，也要在同一文件里翻来翻去。
"""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from middleware.logging_system import register_secret

import httpx

from config_data import BASE_DIR

logger = logging.getLogger(__name__)

_UI_STATE_FILE = Path(BASE_DIR) / "data" / "ui_state.json"

# 统一的浏览器 UA：部分站点（尤其搜索结果页）对默认 python UA 会返回空页面
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT = httpx.Timeout(12.0, connect=6.0)


def read_setting(key: str) -> str:
    """
    读取一个字符串配置，优先级：界面设置（data/ui_state.json）> 环境变量。

    这样用户可以在「设置 → Agent 能力」里直接填 Tavily Key 等，不必去改 .env。
    """
    env_name = {
        "tavily_api_key": "TAVILY_API_KEY",
        "firecrawl_api_key": "FIRECRAWL_API_KEY",
    }.get(key, key.upper())
    try:
        if _UI_STATE_FILE.exists():
            data = json.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
            value = str(data.get(key, "") or "").strip()
            if value:
                register_secret(value)
                return value
    except Exception:
        pass
    value=os.getenv(env_name, "").strip()
    register_secret(value)
    return value


def read_setting_bool(key: str, default: bool = True) -> bool:
    """读取一个开关配置（界面设置优先，其次环境变量的 1/true/yes/on，最后默认值）。"""
    try:
        if _UI_STATE_FILE.exists():
            data = json.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get(key), bool):
                return data[key]
    except Exception:
        pass
    env = os.getenv(key.upper(), "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return default


def strip_html(text: str) -> str:
    """去掉 HTML 标签与多余空白，并解码 HTML 实体（用于把网页片段变成纯文本）。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_soup(html_text: str):
    """构造 BeautifulSoup 对象；bs4 未安装时返回 None（上层据此降级）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("未安装 beautifulsoup4，无法解析网页（pip install beautifulsoup4）")
        return None
    return BeautifulSoup(html_text, "html.parser")


# ── 爬虫限速 ──
# 搜索引擎有反爬：请求太密会被封 IP。这里强制两次抓取之间至少间隔 1.5 秒，
# 再叠加 0.3-0.8 秒随机抖动，让请求节奏看起来不像脚本。
_SCRAPE_MIN_INTERVAL = 1.5
_LAST_SCRAPE_TS = 0.0


def throttle() -> None:
    """限制爬虫请求频率：距上次不足最小间隔就等待，另加随机抖动。"""
    global _LAST_SCRAPE_TS
    now = time.time()
    wait = _SCRAPE_MIN_INTERVAL - (now - _LAST_SCRAPE_TS)
    if wait > 0:
        time.sleep(wait)
    time.sleep(random.uniform(0.3, 0.8))
    _LAST_SCRAPE_TS = time.time()


def extract_main_text(html_text: str) -> str:
    """
    从 HTML 中抽取「正文」，尽量去掉导航/广告/脚注等噪声。

    原理：网页真正的内容通常在 <article> / <main>（或 role="main"）里；
    先把明显的噪声标签整块删掉，再优先取上述容器，取不到就退回 <body>。
    BeautifulSoup 不可用时退回正则去标签，保证这个函数永远有输出。
    """
    soup = parse_soup(html_text)
    if soup is None:
        return strip_html(html_text)
    try:
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                         "aside", "form", "iframe", "svg", "button"]):
            tag.decompose()
        container = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.body
            or soup
        )
        text = container.get_text("\n")
        # 压缩空白：去掉空行与行首行尾空格，避免正文里塞满空行
        lines = [ln.strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)
    except Exception as e:
        logger.warning("正文抽取失败，退回正则去标签: %s", str(e)[:120])
        return strip_html(html_text)
