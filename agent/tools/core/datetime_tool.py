"""
agent/tools/datetime_tool.py - 当前时间工具

【为什么需要它】
    语言模型的知识和"现在"是脱节的——它不知道今天几号，
    所以在回答"今天有什么新闻""本周发生了什么"时会含糊其辞或猜错年份。
    这个工具让模型先问时间，再据此筛选搜索结果。

【谁在调用】模型 → 注册表 → 本文件底部的 TOOLS["get_current_time"]。
"""

from __future__ import annotations

import time
from typing import Any, Dict

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def get_current_time() -> Dict[str, Any]:
    """返回本机当前时间信息（本地时间/星期/时区/时间戳/UTC）。"""
    now = time.time()
    local = time.localtime(now)
    weekday = _WEEKDAYS[local.tm_wday]
    return {
        "ok": True,
        "text": (
            f"当前本地时间：{time.strftime('%Y-%m-%d %H:%M:%S', local)}（{weekday}）\n"
            f"时区：{time.strftime('%Z', local) or '本地时区'}，Unix 时间戳：{int(now)}\n"
            f"UTC 时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))}"
        ),
        "local_time": time.strftime("%Y-%m-%d %H:%M:%S", local),
        "weekday": weekday,
        "timestamp": int(now),
    }


def _tool_get_current_time(args: Dict[str, Any]):
    """工具入口：无参数，返回 (给模型看的文本, 前端 meta)。"""
    info = get_current_time()
    return info["text"], {
        "ok": True,
        "local_time": info["local_time"],
        "weekday": info["weekday"],
        "timestamp": info["timestamp"],
    }


TOOLS = {
    "get_current_time": {
        "description": "获取当前的日期与时间（含星期与时区）。当用户问“今天/现在/最近/最新”，"
                       "或需要按日期筛选信息时，先调用它拿到真实时间，不要凭记忆猜日期。",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_get_current_time,
    },
}
