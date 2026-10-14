"""
context_manager.py - 对话上下文管理（控制 token 消耗）

【为什么需要这个模块】
    Agent 的每一步（模型 → 工具 → 模型）都会把**完整上下文**重新发一遍：
    4 步就是 4 份 prompt。上下文越大，倍数放大越明显。
    实测：一次 4 步的联网问答，prompt 就有 1.3-1.4 万 token。
    所以除了"少调用工具"，最有效的省钱手段就是**把上下文压小**。

【本模块做两件事】
    1. 裁剪（trim）：给**整个上下文窗口**设一个 token 预算（system + RAG + 历史 + 当前提问），
       超出就从最旧的开始丢；system 提示、助手开场、当前用户消息必须保留（它们是本轮的必要输入），
       另外有「保底最近轮数」，最近的几轮对话无论预算多紧都不丢；
    2. 压缩（compress，可选）：被裁掉的旧消息交给模型压成一段短摘要，
       存进会话（sessions.summary），下次对话作为一条 system 消息带回，
       既不丢关键信息，又比原文省几十倍 token。

【谁在用】
    SilverWolf.py 的 `_apply_context_management()`（每轮对话开始前）
    （工具结果的限长截断在 agent/artifacts.py 的 preserve() 里做，不在本模块）

【设计取舍】
    - 裁剪永远开启（纯计算，零成本）；
    - 预算是「窗口总量」而不是「历史部分」：用户看到的是"上下文窗口 128K"这种数字，
      语义要跟它一致，所以 system/RAG/当前提问都算在里面；
    - 「保底最近轮数」是为了避免预算设太小导致模型彻底断片：
      最近 N 轮一定保留，宁可略微超出预算；
    - 预算上限给到 1,000,000（对齐主流长上下文模型），但不建议无脑拉满：
      上下文越大，每一轮工具循环都要重发一遍，成本是成倍涨的（见 changecode 深度案例 11）。
    - 压缩是可选项（需要额外一次模型调用），只在"确实裁掉了消息"时才触发，
      且限制输出长度，避免"为了省 token 又烧 token"；
    - 压缩失败不影响对话：静默跳过，只丢掉被裁的历史。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .token_usage import estimate_tokens

logger = logging.getLogger(__name__)

# 上下文窗口预算（整个请求：system + RAG + 历史 + 当前提问）
MIN_CONTEXT_BUDGET = 4_000          # 再小就没法正常对话了
MAX_CONTEXT_BUDGET = 1_000_000      # 对齐主流长上下文模型上限
DEFAULT_CONTEXT_BUDGET = 200_000    # 默认值：对齐 200K 级模型
# 保底保留的最近轮数（一轮 = 用户问 + 助手答）
DEFAULT_MIN_KEEP_TURNS = 10
MAX_MIN_KEEP_TURNS = 20
# 压缩摘要的长度上限（字符），避免摘要本身又变成大块上下文
SUMMARY_MAX_CHARS = 600


def trim_messages(
    messages: List[Dict[str, Any]],
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET,
    keep_head: int = 2,
    min_keep_turns: int = DEFAULT_MIN_KEEP_TURNS,
    unlimited: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    按 token 预算裁剪对话上下文。

    Args:
        messages: 完整消息列表（system / assistant 开场 / 历史 / 当前用户）
        budget_tokens: **整个上下文**的 token 预算（含锚点：system、开场、当前提问）
        keep_head: 开头必须保留的消息条数（system 提示 + 助手开场，可能含 RAG）
        min_keep_turns: 保底保留的最近轮数（一轮 = 一问一答，所以是 2×条消息）
        unlimited: 无限轮数模式 —— 一条历史都不丢（预算不再裁剪，只当作展示参考）

    Returns:
        (保留后的消息列表, 被裁掉的消息列表)
        —— 被裁掉的列表会交给 compress_history 做摘要。

    说明：实现是"从最新的往旧的累加"，一旦超过预算就停止 —— 所以丢掉的永远是更早的内容。
    两个例外（宁可超预算也要留）：
      1. 锚点：system 提示、助手开场、当前提问；
      2. 保底轮数：最近 min_keep_turns 轮对话。
    """
    if not messages:
        return [], []
    if unlimited:
        # 用户明确选了"无限轮数"：全部保留，谁也别想被裁掉。
        # 代价是每轮都要把完整历史重发一遍，token 账单自己扛（界面上有提示）。
        return list(messages), []
    keep_head = max(0, min(keep_head, len(messages) - 1))
    head = messages[:keep_head]
    tail = messages[keep_head:]
    if not tail:
        return list(messages), []

    last = tail[-1]                 # 当前用户消息，必须保留
    history = tail[:-1]             # 中间的历史
    # 框架注入的 system 消息是前提，不是普通历史；位置变了也必须保住。
    pinned = [m for m in history if m.get("role") == "system"]
    history = [m for m in history if m.get("role") != "system"]

    # 锚点已经占掉一部分预算，剩下的才是历史能用的额度
    anchors_cost = _messages_cost(head) + _messages_cost(pinned) + _messages_cost([last])
    min_keep_msgs = max(0, int(min_keep_turns) * 2)

    kept_history: List[Dict[str, Any]] = []
    used = 0
    for msg in reversed(history):
        cost = _messages_cost([msg])
        # 超出预算就停 —— 但先保证「保底最近轮数」已经被保住
        if anchors_cost + used + cost > budget_tokens and len(kept_history) >= min_keep_msgs:
            break
        kept_history.append(msg)
        used += cost
    kept_history.reverse()

    dropped = history[: len(history) - len(kept_history)]
    return head + pinned + kept_history + [last], dropped


def _messages_cost(messages: List[Dict[str, Any]]) -> int:
    """估算一批消息占用的 token（每条额外加 4，作为角色/结构开销的经验补偿）。"""
    total = 0
    for msg in messages:
        total += estimate_tokens(str(msg.get("content") or "")) + 4
    return total


def compress_history(
    dropped: List[Dict[str, Any]],
    provider: Optional[Dict[str, Any]] = None,
    previous_summary: str = "",
) -> str:
    """
    把被裁掉的历史压缩成一段摘要（可选功能，失败返回空串）。

    实现：用当前 Provider 发一次很小的请求（max_tokens 限制在 300 以内），
    要求模型只输出要点。previous_summary 会一起送进去，实现"滚动摘要"。
    """
    if not dropped:
        return previous_summary or ""
    if provider is None:
        return previous_summary or ""

    transcript_parts = []
    for m in dropped:
        role = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}.get(
            str(m.get("role")), str(m.get("role"))
        )
        content = str(m.get("content") or "")[:1200]   # 单条最多取 1200 字符
        if content.strip():
            transcript_parts.append(f"{role}：{content}")
    transcript = "\n".join(transcript_parts)[:6000]
    if not transcript.strip():
        return previous_summary or ""

    prompt = (
        "请把下面这段对话历史压缩成不超过 200 字的中文要点，只保留：用户的目标、"
        "已确认的事实/结论、未完成的事项。不要寒暄，不要复述原文。\n"
        + (f"\n【已有摘要，可合并】\n{previous_summary}\n" if previous_summary else "")
        + f"\n【待压缩的对话】\n{transcript}"
    )

    try:
        import provider_config
        client = provider_config.build_client(provider)
        resp = client.chat.completions.create(
            model=provider.get("model"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.2,
        )
        tracker = provider.get('_usage_tracker')
        if tracker:
            tracker.next_step()
            if not tracker.add_provider_usage(resp.usage,purpose='摘要压缩'):
                tracker.add_estimate(prompt,resp.choices[0].message.content or '',purpose='摘要压缩')
        summary = (resp.choices[0].message.content or "").strip()
        if summary:
            summary = summary[:SUMMARY_MAX_CHARS]
            logger.info("上下文压缩完成：%d 条历史 → %d 字摘要", len(dropped), len(summary))
            return summary
    except Exception as e:
        logger.warning("上下文压缩失败（不影响对话）: %s", str(e)[:150])
    return previous_summary or ""


def build_summary_message(summary: str) -> Optional[Dict[str, str]]:
    """把摘要包装成一条 system 消息（没有摘要时返回 None）。"""
    if not summary or not summary.strip():
        return None
    return {
        "role": "system",
        "content": f"【早前对话摘要（由系统压缩，供你保持上下文连贯）】\n{summary.strip()}",
    }
