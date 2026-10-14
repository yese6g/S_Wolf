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
        self.has_estimate = False
        self.reasoning_tokens = 0
        self.reasoning_known_steps = 0
        self.reasoning_unknown_steps = 0
        self.steps = 0
        self.calls = []

    # ── 写入 ──

    def add_provider_usage(self, usage: Any, purpose='回答') -> bool:
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
        # 先完整校验再修改累计值，避免一半合法、一半无效时估算又把输入算一遍。
        prompt_count = int(prompt or 0)
        completion_count = int(completion or 0)
        if prompt_count < 0 or completion_count < 0:
            raise ValueError("usage token 数不能为负数")
        details = _get('completion_tokens_details')
        thought = details.get('reasoning_tokens') if isinstance(details,dict) else getattr(details,'reasoning_tokens',None)
        if thought is None:
            thought = _get('reasoning_tokens')
        if thought is not None:
            thought = int(thought)
            if thought < 0: raise ValueError('思考token不能为负')
        self.prompt_tokens += prompt_count
        self.completion_tokens += completion_count
        self.has_provider_usage = True
        self.calls.append({'purpose':purpose,'prompt_tokens':prompt_count,'completion_tokens':completion_count,'reasoning_tokens':thought,'source':'provider'})
        if thought is None:
            self.reasoning_unknown_steps += 1
        else:
            self.reasoning_known_steps += 1
            self.reasoning_tokens += thought
        return True

    def add_estimate(self, prompt_text: str, completion_text: str, purpose='回答') -> None:
        """没有真实 usage 时按文本估算累加。"""
        prompt=estimate_tokens(prompt_text);completion=estimate_tokens(completion_text)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls.append({'purpose':purpose,'prompt_tokens':prompt,'completion_tokens':completion,'reasoning_tokens':None,'source':'estimate'})
        self.has_estimate = True
        self.reasoning_unknown_steps += 1

    def merge(self, usage):
        """子任务汇总按一次归并，不把子推理token再次加进输出总数。"""
        self.prompt_tokens += max(0,int(usage.get('prompt_tokens',0)))
        self.completion_tokens += max(0,int(usage.get('completion_tokens',0)))
        self.steps += max(0,int(usage.get('steps',0)))
        self.has_provider_usage |= usage.get('source') in {'provider','mixed'}
        self.has_estimate |= usage.get('source') in {'estimate','mixed'}
        self.reasoning_tokens += max(0,int(usage.get('reasoning_known_tokens',usage.get('reasoning_tokens') or 0)))
        self.reasoning_known_steps += int(usage.get('reasoning_known_steps',int(usage.get('reasoning_tokens') is not None)))
        self.reasoning_unknown_steps += int(usage.get('reasoning_unknown_steps',int(usage.get('reasoning_tokens') is None)))
        self.calls.extend({**call,'purpose':'子任务 / '+call.get('purpose','回答')} for call in usage.get('calls',[]))

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
            "source": 'mixed' if self.has_provider_usage and self.has_estimate else "provider" if self.has_provider_usage else "estimate",
            'reasoning_tokens': self.reasoning_tokens if self.reasoning_known_steps and not self.reasoning_unknown_steps else None,
            'reasoning_known_tokens':self.reasoning_tokens,
            'reasoning_known_steps':self.reasoning_known_steps,
            'reasoning_unknown_steps':self.reasoning_unknown_steps,
            "steps": self.steps,
            'calls':list(self.calls[-100:]),
        }
