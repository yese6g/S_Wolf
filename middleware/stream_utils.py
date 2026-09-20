"""
stream_utils.py - 流式响应解析工具（被 SilverWolf.py 与 agent_runner.py 共用）

【为什么单独抽出来】
    普通聊天（SilverWolf.py）和 Agent 循环（agent_runner.py）都要解析流式 chunk。
    如果把解析函数留在 SilverWolf.py，agent_runner 再导入它就会形成循环依赖，
    所以这里做一层共享工具。

【要点：不同厂商的 chunk 结构不一样，必须防御式解析】
    - choices 可能为空数组（阿里云百炼等会在结尾发这种帧）→ 直接取 [0] 会 IndexError；
    - delta 可能不存在；
    - content 可能是字符串，也可能是 [{type,text}] 这样的列表；
    - 推理模型把思考放在 reasoning_content（DeepSeek 等）。
"""

from __future__ import annotations

from typing import Optional, Tuple


def normalize_delta_text(value) -> Optional[str]:
    """把流式增量规范成字符串（兼容 str / list[dict] 两种格式）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        text = "".join(parts)
        return text or None
    return str(value) if value else None


def extract_stream_delta(chunk) -> Tuple[Optional[str], Optional[str]]:
    """从流式 chunk 提取 (content, reasoning_content)，异常结构一律安全返回 (None, None)。"""
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None, None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None, None
    content = normalize_delta_text(getattr(delta, "content", None))
    reasoning = normalize_delta_text(getattr(delta, "reasoning_content", None))
    return content, reasoning
