"""
token_usage.py - Token 消耗统计

【谁在用】
    agent_runner.py 每轮模型调用后都会调用这里的工具统计输入/输出 token，
    最终通过 SSE 的 `usage` 事件发给前端，显示在回复气泡底部。

【原理】
    优先使用服务商返回的 usage（OpenAI 兼容接口在流式模式下需显式请求
    stream_options={"include_usage": True}，部分服务商/Ollama 不支持）。
    拿不到真实 usage 时退回估算：
      - 中日韩字符：约 1 字 ≈ 1 token
      - 其余字符（英文/符号）：约 4 字符 ≈ 1 token
    估算不是精确账单，但足以让用户对比「这次对话大概烧了多少」。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 中日韩统一表意文字 + 日文假名 + 韩文音节
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """按字符构成估算 token 数（无真实 usage 时的兜底）。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    others = max(len(text) - cjk, 0)
    return max(int(cjk + others / 4), 1)


def estimate_messages_tokens(messages: list) -> int:
    """估算一轮 messages 的输入 token（含每条约 4 token 的结构开销）。"""
    total = 0
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else ""
        total += estimate_tokens(content if isinstance(content, str) else "") + 4
        # 工具调用的参数同样计入输入
        if isinstance(m, dict) and m.get("tool_calls"):
            for call in m["tool_calls"]:
                fn = (call or {}).get("function", {})
                total += estimate_tokens(str(fn.get("arguments", ""))) + 4
    return total


class UsageTracker:
    """
    多轮 Agent 循环的用量累加器。

    用法：
        tracker = UsageTracker()
        tracker.add_provider_usage(usage_obj)      # 有真实 usage 时
        tracker.add_estimate(prompt_text, out_text)  # 没有时
        tracker.summary()  # -> {"prompt_tokens":..,"completion_tokens":..,"total_tokens":..,"source":..}
    """

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.has_provider_usage = False
        self.steps = 0

    # ── 写入 ──

    def add_provider_usage(self, usage: Any) -> bool:
        """
        记录服务商返回的真实 usage（OpenAI SDK 的 Usage 对象或同结构 dict）。
        返回是否成功读到数值。
        """
        if usage is None:
            return False
        def _get(name: str) -> Optional[int]:
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)
        prompt = _get("prompt_tokens")
        completion = _get("completion_tokens")
        if prompt is None and completion is None:
            return False
        self.prompt_tokens += int(prompt or 0)
        self.completion_tokens += int(completion or 0)
        self.has_provider_usage = True
        return True

    def add_estimate(self, prompt_text: str, completion_text: str) -> None:
        """没有真实 usage 时按文本估算累加。"""
        self.prompt_tokens += estimate_tokens(prompt_text)
        self.completion_tokens += estimate_tokens(completion_text)

    def next_step(self) -> None:
        """进入 Agent 循环的下一轮（工具调用会带来多轮 token 消耗）。"""
        self.steps += 1

    # ── 读取 ──

    def summary(self) -> Dict[str, Any]:
        """返回给前端展示的汇总结构。"""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "source": "provider" if self.has_provider_usage else "estimate",
            "steps": self.steps,
        }
