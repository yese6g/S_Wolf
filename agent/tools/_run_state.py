"""
agent/tools/_run_state.py - 一次 Agent 运行内的工具状态（内部模块，不注册成工具）

【为什么需要它】
    模型在一轮对话里调工具时**没有会话内记忆**，实测会出现：
      1. 同一句话搜三遍（"AI 大模型 发布 价格 最新消息" / "AI模型 价格 降价 最新消息"），
         每次结果一样，但每次都真发网络请求、都往上下文里塞一遍；
      2. 一口气搜十次、抓八个网页 —— 上下文被撑爆，prompt token 从 16K 涨到 51K；
      3. 已经读过的网页再读一遍。
    这些都是"没有本轮记忆"造成的浪费，所以给每轮运行配一个状态盒子，
    让工具能记账（搜了几次、抓过哪些 URL、哪个查询已经搜过）。

【怎么用】
    - `agent/runner.py`：每轮开始 `ctx = new_run()`；每次工具执行用 `scoped(ctx)` 重绑并恢复；
    - 工具（web_search / fetch_page）：用 `state()` 读计数与缓存，用里面的字典记账。

【为什么用 contextvars 而不是给工具加参数】
    工具的签名是 OpenAI function calling 的约定（只接收模型给的 args），
    为了内部状态去改签名会污染协议；而全局变量又会串台（两个会话同时聊天）。
    contextvars 正好是"当前执行上下文内的局部变量"，每个请求互不干扰。

【拿不到状态怎么办】
    `state()` 在不在 Agent 运行时返回 None，工具会退化成"不记账"的单次调用，
    这样「设置 → 测试搜索」之类的独立调用依然可用。

【⚠️ 为什么不是"设一次、全程有效"】
    FastAPI 的流式响应会把**同步生成器丢进线程池**逐步迭代（Starlette 的
    `iterate_in_threadpool`）。每次 `next()` 都等价于在新的线程里跑一遍，
    而线程池调用只**复制**当前上下文 —— 在线程里 `ContextVar.set()` 的结果
    不会回传到调用方。如果只在开头 set 一次，第二步就读不到了（缓存和计数全会失效）。
    所以：状态本身是**一个普通 dict，存在生成器帧里**（帧状态不会丢），
    每次执行工具时再用 scoped 绑定，返回前恢复；绑定期间没有流式 yield，
    不会把必须同上下文 reset 的 token 带到另一个线程。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Dict, Optional

# 单轮对话内的硬上限：防止模型"无止境地搜"。
# 依据：实测一轮里出现过 10 次搜索 + 8 次抓页，prompt token 直接翻 3 倍；
# 正常查资料 2-3 次搜索足够，留一倍余量给"换个说法再搜一次"。
MAX_SEARCHES_PER_RUN = 6
MAX_FETCHES_PER_RUN = 6

_RUN_STATE: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "sw_tool_run_state", default=None
)


def new_run() -> Dict[str, Any]:
    """开一轮 Agent 运行，返回状态盒子（普通 dict，由调用方持有）。"""
    return {
        "searches": 0,                              # 已发出去的搜索次数（失败也算，防连败绕过上限）
        "fetches": 0,                               # 已发出去的抓取次数（同上）
        "search_cache": {},                         # 归一化查询词 → 上次的搜索结果
        "fetched_urls": {},                         # URL → 上次抓取的结果
        "max_searches": MAX_SEARCHES_PER_RUN,
        "max_fetches": MAX_FETCHES_PER_RUN,
        "plan": [],                                 # 本轮任务规划清单（update_plan 工具写）
    }


@contextmanager
def scoped(ctx: Dict[str, Any]):
    """仅在一次同步工具执行内绑定并恢复状态；参数为生成器帧持有的状态盒子。

    不把 ContextVar token 跨 yield 保存：线程池的下次 next() 可能属于另一个上下文，
    在那里 reset 会报 ValueError。工具前重绑、返回前恢复，兼顾缓存复用和并发隔离。
    """
    token = _RUN_STATE.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_STATE.reset(token)


def state() -> Optional[Dict[str, Any]]:
    """当前运行的状态盒子；不在 Agent 运行时返回 None。"""
    return _RUN_STATE.get()


def normalize_query(text: str) -> str:
    """
    把查询词归一化后再做去重判断。

    只做"看起来一样"的收敛：去空格、去标点、转小写、统一全角空格。
    不做同义词/分词这种重活 —— 太激进会把"应该重新搜"的情况也拦掉。
    """
    text = (text or "").strip().lower()
    for ch in " \t\u3000,，。.、;；:：!！?？\"'“”‘’()（）[]【】<>《》-_/\\|+*#~`":
        text = text.replace(ch, "")
    return text
