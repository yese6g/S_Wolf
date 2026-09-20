"""
agent_runner.py - Agent 主循环（ReAct：思考 → 调工具 → 再思考）

【谁在用】
    SilverWolf.py 的 `/api/chat/stream`（流式）与 `/api/chat`（非流式）在「Agent 模式」下调用本模块。

【原理】
    1. 把 agent_tools.openai_tool_schemas() 生成的工具表交给模型（OpenAI 兼容 function calling）；
    2. 模型返回 tool_calls 时，本模块执行工具，把结果作为 role="tool" 的消息塞回对话，再调一次模型；
    3. 模型直接返回文本时结束（最多 max_steps 轮，防止无限循环）；
    4. 全程统计 token，并把「用了哪些工具 / 搜了哪些网页 / 命中哪些 RAG」汇总成 meta 给前端。

【兼容性处理】
    - 有些模型/服务商不支持 function calling，会直接报错：这里会捕获并降级为普通流式聊天，
      同时在 meta 里标记 tools_unsupported，让用户知道「这次没走 Agent 工具链」。
    - 流式 usage：多数服务商需要 stream_options={"include_usage": True}；
      不支持时自动去掉该参数重试，拿不到真实 usage 就用 token_usage 估算。

【事件流】（生成器 yield 的 dict，由 SilverWolf.py 转成 SSE 发给前端）
    {"type": "content",     "text": "..."}                 模型正文增量
    {"type": "reasoning",   "text": "..."}                 思考过程增量（推理模型）
    {"type": "tool_call",   "name": "...", "args": {...}}  开始调用工具
    {"type": "tool_result", "name": "...", "ok": bool, "summary": "...", "meta": {...}}
    {"type": "usage",       "usage": {...}}                token 统计（结束时发一次）
    {"type": "done",        "meta": {...}}                 结束（包含汇总信息）
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from openai import APIError, BadRequestError

import provider_config
from agent import tools as agent_tools          # 工具仓库（注册表 + 执行）
from agent.tools import _run_state as run_state  # 本轮工具状态（计数/缓存）
from middleware import context as context_manager  # 上下文裁剪/截断
from middleware.stream_utils import extract_stream_delta
from middleware.token_usage import UsageTracker

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 4
# 单次回复 token 上限默认值（None / 0 = 不限长度，此时连 max_tokens 参数都不发）
DEFAULT_REPLY_MAX_TOKENS = 8192
# 工具结果进入上下文的字符上限（工具输出往往是长文本，截断能显著省 token）
DEFAULT_TOOL_OUTPUT_LIMIT = 3000
# 单次对话累计 token 硬闸：超过就停止继续调工具（防止一次任务烧穿额度）。
# 每次模型回复与工具结果都会重发上下文，多轮下来累加得很快，光靠 max_steps 不够。
DEFAULT_MAX_TOTAL_TOKENS = 200_000

# ── 高危工具审批（请求-响应）──
# 工具定义里带 approval_required=True 的（写文件/删文件/改自定义工具…）在执行前
# 会先发 tool_approval 事件给前端弹窗，等用户「允许/拒绝」。
# 前端收到后把 request_id + approve 回调 POST /api/agent/approval（见 SilverWolf.py），
# 这里用 threading.Event 做一次等待；超时未响应按「拒绝」处理，流程不会卡死。
APPROVAL_REGISTRY: Dict[str, Dict[str, Any]] = {}
APPROVAL_TIMEOUT = 60.0  # 秒；超时自动拒绝


def _parse_tool_args(raw: str) -> Dict[str, Any]:
    """解析模型给出的工具参数 JSON；解析失败时返回空参并记录（不让流程崩掉）。"""
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        logger.warning("工具参数不是合法 JSON: %s", raw[:200])
        return {}


def _merge_tool_call_fragments(buffer: Dict[int, Dict[str, Any]], deltas: List[Any]) -> None:
    """
    合并流式返回的 tool_calls 片段。

    流式模式下，函数的 name/arguments 是分片到达的（例如 arguments: '{"ci' + 'ty":"北京"}'），
    因此用 index 作为 key 累积，等这一轮结束后再整体解析。
    """
    for d in deltas or []:
        idx = getattr(d, "index", 0) or 0
        slot = buffer.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if getattr(d, "id", None):
            slot["id"] = d.id
        fn = getattr(d, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            if getattr(fn, "arguments", None):
                slot["arguments"] += fn.arguments


def _build_meta(
    tracker: UsageTracker,
    provider: Dict[str, Any],
    tools_used: List[Dict[str, Any]],
    searches: List[Dict[str, Any]],
    rag_used: List[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装最终 meta（token 用量 + 工具/搜索/RAG 明细），前端用它渲染气泡底部的总结。"""
    meta: Dict[str, Any] = {
        "usage": tracker.summary(),
        "provider_id": provider.get("id"),
        "model": provider.get("model"),
        "tools_used": tools_used,
        "web_searches": searches,
        "rag_hits": rag_used,
    }
    if extra:
        meta.update(extra)
    return meta


def _collect_tool_meta(name: str, meta: Dict[str, Any], searches: List[Dict[str, Any]], rag_used: List[Dict[str, Any]]) -> None:
    """把工具返回的 meta 归类到「搜索来源 / RAG 命中」列表，供前端展示。"""
    if name == "web_search" and meta.get("sources"):
        searches.append({
            "query": meta.get("query", ""),
            "engine": meta.get("engine", ""),
            "sources": meta["sources"],
        })
    elif name == "rag_search" and meta.get("sources"):
        rag_used.append({"kb_slug": meta.get("kb_slug", ""), "sources": meta["sources"]})


def run_agent_stream(
    provider: Dict[str, Any],
    messages: List[Dict[str, Any]],
    enabled_tools: Optional[List[str]] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    reply_max_tokens: Optional[int] = DEFAULT_REPLY_MAX_TOKENS,
    tool_output_limit: int = DEFAULT_TOOL_OUTPUT_LIMIT,
    max_total_tokens: Optional[int] = DEFAULT_MAX_TOTAL_TOKENS,
) -> Iterator[Dict[str, Any]]:
    """
    流式执行 Agent 循环。调用方（SilverWolf.py）负责把事件转成 SSE。

    Args:
        provider: Provider 配置字典（含 base_url/api_key/model），由 provider_config 提供
        messages: 已经拼好的对话消息（system + 历史 + 当前用户）
        enabled_tools: 允许使用的工具名列表；None 表示全部启用（联网开关会过滤 web_search）
        max_steps: 最大「模型→工具」轮数
        reply_max_tokens: 每次模型回复的 token 上限（None 表示不限制）
        tool_output_limit: 工具结果写入上下文前的字符上限（超长会截断，省 token）

    Yields:
        事件字典（见文件头注释）
    """
    client = provider_config.build_client(provider)
    model = provider.get("model")
    tools_schema = agent_tools.openai_tool_schemas(enabled_tools)
    tracker = UsageTracker()
    tools_used: List[Dict[str, Any]] = []
    searches: List[Dict[str, Any]] = []
    rag_used: List[Dict[str, Any]] = []
    tools_unsupported = False
    # 本轮工具状态（搜索/抓页计数与缓存）。存在生成器帧里，每个 step 重新 bind 一次，
    # 原因见 agent/tools/_run_state.py 的说明：流式生成器是在线程池里被逐步迭代的。
    run_ctx = run_state.new_run()
    # 不限长度（reply_max_tokens=None/0）时**不传** max_tokens 参数：
    # 传 None 有些服务商会当成 "max_tokens: null" 而报错，直接不发最稳。
    token_kwargs = {"max_tokens": reply_max_tokens} if reply_max_tokens else {}
    answered = False
    steps_total = max(1, int(max_steps or DEFAULT_MAX_STEPS))

    for step in range(steps_total):
        run_state.bind(run_ctx)
        tracker.next_step()
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_buffer: Dict[int, Dict[str, Any]] = {}
        usage_obj = None

        # ── 1. 调用模型（带工具） ──
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_schema or None,
                stream=True,
                stream_options={"include_usage": True},
                **token_kwargs,
            )
        except (BadRequestError, APIError) as e:
            # 服务商不支持 tools / stream_options：先尝试只去掉 stream_options，再不行就降级无工具。
            # 另外有一种情况：用户把「单次回复上限」设得比模型允许的输出上限还大，
            # 服务端会直接报错——这时丢掉 max_tokens 重试，别让一个设置项把对话搞挂。
            msg = str(e).lower()
            if "max_tokens" in msg or "max output" in msg or "maximum" in msg and "token" in msg:
                logger.warning("模型不接受 max_tokens=%s，去掉该参数重试", reply_max_tokens)
                stream = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools_schema or None,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            elif "tool" in msg or "function" in msg:
                tools_unsupported = True
                logger.warning("该模型不支持工具调用，降级为普通对话: %s", str(e)[:160])
                yield {"type": "warning", "text": "当前模型不支持工具调用，本次以普通对话模式回答"}
                stream = client.chat.completions.create(model=model, messages=messages, stream=True)
            else:
                stream = client.chat.completions.create(
                    model=model, messages=messages, tools=tools_schema or None, stream=True,
                    **token_kwargs,
                )

        # ── 2. 消费流式增量 ──
        for chunk in stream:
            content, reasoning = extract_stream_delta(chunk)
            if reasoning:
                reasoning_parts.append(reasoning)
                yield {"type": "reasoning", "text": reasoning}
            if content:
                content_parts.append(content)
                yield {"type": "content", "text": content}
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = getattr(choices[0], "delta", None)
                calls = getattr(delta, "tool_calls", None) if delta is not None else None
                if calls:
                    _merge_tool_call_fragments(tool_buffer, calls)
            if getattr(chunk, "usage", None):
                usage_obj = chunk.usage

        text = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)

        # ── 3. token 统计：优先真实 usage，其次按文本估算 ──
        if not tracker.add_provider_usage(usage_obj):
            prompt_text = "\n".join(
                str(m.get("content") or "") for m in messages if isinstance(m, dict)
            )
            tracker.add_estimate(prompt_text, text + reasoning_text)

        # ── 4. 没有工具调用 → 本轮就是最终回答，结束循环 ──
        if not tool_buffer:
            answered = True
            break

        # ── 4.5 单轮累计 token 硬闸：还要调工具但已经超预算 → 停止调用，走收尾作答 ──
        total_now = tracker.summary().get("total_tokens", 0)
        if max_total_tokens and total_now > max_total_tokens:
            logger.info("token 硬闸触发：累计 %d > %d，停止本轮工具调用",
                        total_now, max_total_tokens)
            yield {"type": "warning",
                   "text": f"累计 token 已达上限（>{max_total_tokens}），"
                           "不再调用新工具，把已拿到的资料整理成结论"}
            answered = False
            break

        # ── 5. 有工具调用 ──
        # 5.0 关键一步：这一轮里模型的"碎碎念"（"搜索结果全是导航站，没用。换几个词试试"）
        #     已经被当成 content 流给前端了，但它**不是回答**。
        #     发一个 demote 事件，让前端把它从回答气泡里撤回、转进「思考过程」，
        #     这样最终落库/展示的内容才只有真正的答案。
        if text.strip():
            yield {"type": "demote", "text": text}

        # 5.1 先把这一轮的 assistant(tool_calls) 写入上下文（OpenAI 协议要求：
        #     assistant 消息在前，紧随其后的 role=tool 结果消息用 tool_call_id 对应）
        calls: List[Dict[str, Any]] = []
        for idx in sorted(tool_buffer.keys()):
            slot = tool_buffer[idx]
            calls.append({
                "index": idx,
                "id": slot.get("id") or f"call_{step}_{idx}",
                "name": slot.get("name") or "",
                "args": _parse_tool_args(slot.get("arguments", "")),
                "raw_arguments": slot.get("arguments") or "{}",
            })
        messages.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["raw_arguments"]},
                }
                for c in calls
            ],
        })

        # 5.2 逐个执行工具，把结果作为 role=tool 消息塞回上下文
        for c in calls:
            # 5.2.0 高危工具需先过审批：发事件给前端弹窗，等待用户「允许/拒绝」。
            #       拒绝或超时 → 跳过执行，把「被拒」作为工具结果回给模型，让它换个方式。
            spec = agent_tools.BUILTIN_TOOLS.get(c["name"]) or {}
            if spec.get("approval_required"):
                request_id = f"apv_{uuid.uuid4().hex[:12]}"
                APPROVAL_REGISTRY[request_id] = {"approved": False, "event": threading.Event()}
                yield {"type": "tool_approval", "request_id": request_id, "name": c["name"], "args": c["args"]}
                waited = APPROVAL_REGISTRY[request_id]["event"].wait(APPROVAL_TIMEOUT)
                approved = APPROVAL_REGISTRY.get(request_id, {}).get("approved", False) if waited else False
                APPROVAL_REGISTRY.pop(request_id, None)
                if not approved:
                    reason = "用户拒绝了这次操作" if waited else f"等待批准超时（{int(APPROVAL_TIMEOUT)} 秒），已自动拒绝"
                    logger.info("工具 %s 未获批准：%s", c["name"], reason)
                    tools_used.append({"name": c["name"], "args": c["args"], "ok": False, "denied": True})
                    yield {
                        "type": "tool_result",
                        "name": c["name"],
                        "ok": False,
                        "summary": reason,
                        "meta": {"denied": True, "tool": c["name"]},
                    }
                    messages.append({
                        "role": "tool",
                        "tool_call_id": c["id"],
                        "content": f"工具 {c['name']} 被跳过：{reason}。"
                                   "向用户说明情况，改用其它方式或等用户同意后再试。",
                    })
                    continue

            yield {"type": "tool_call", "name": c["name"], "args": c["args"]}
            result = agent_tools.execute_tool(c["name"], c["args"])
            tools_used.append({"name": c["name"], "args": c["args"], "ok": result["ok"]})
            _collect_tool_meta(c["name"], result.get("meta", {}), searches, rag_used)
            yield {
                "type": "tool_result",
                "name": c["name"],
                "ok": result["ok"],
                "summary": result["content"][:300],
                "meta": result.get("meta", {}),
            }
            # update_plan / show_plan 更新计划 → 转发给前端渲染进度清单
            if result.get("meta", {}).get("plan") is not None:
                yield {"type": "plan", "steps": result["meta"]["plan"]}
            messages.append({
                "role": "tool",
                "tool_call_id": c["id"],
                # 工具结果截断：长输出会随每一轮上下文反复计费，截断是最直接的省 token 手段
                "content": context_manager.truncate_text(result["content"], tool_output_limit),
            })

    # ── 6. 轮数用尽却还在调工具 → 强制它用已有资料收尾作答 ──
    # 少了这一步，用户看到/落库的"回答"就会是最后那句过程碎碎念
    # （实测事故：整轮回答只有"搜索结果全是导航站，没用。换几个精确的关键词。"）。
    if not answered:
        logger.info("工具轮数用尽（max_steps=%d），强制收尾作答", steps_total)
        yield {"type": "warning", "text": "工具轮数用尽，先把已拿到的资料整理成结论"}
        tracker.next_step()
        messages.append({
            "role": "system",
            "content": (
                "（系统）工具调用次数已用尽，现在禁止再调用任何工具。"
                "请直接回答用户最初的问题：给出明确结论与关键数据，说明信息来自哪几个来源；"
                "确实没查到的部分就直接说没查到，别用「我再去搜一下」敷衍。"
            ),
        })
        run_state.bind(run_ctx)
        try:
            final_stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **token_kwargs,
            )
        except Exception as e:  # 小众服务商可能不认 stream_options，退一步再试
            logger.warning("收尾调用失败，去掉用量参数重试: %s", str(e)[:150])
            final_stream = client.chat.completions.create(model=model, messages=messages, stream=True)
        final_parts: List[str] = []
        final_usage = None
        for chunk in final_stream:
            content, reasoning = extract_stream_delta(chunk)
            if reasoning:
                yield {"type": "reasoning", "text": reasoning}
            if content:
                final_parts.append(content)
                yield {"type": "content", "text": content}
            if getattr(chunk, "usage", None):
                final_usage = chunk.usage
        if not tracker.add_provider_usage(final_usage):
            tracker.add_estimate(
                "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict)),
                "".join(final_parts),
            )

    usage = tracker.summary()
    yield {"type": "usage", "usage": usage}
    yield {
        "type": "done",
        "meta": _build_meta(
            tracker, provider, tools_used, searches, rag_used,
            {"tools_unsupported": tools_unsupported},
        ),
    }


def run_agent(
    provider: Dict[str, Any],
    messages: List[Dict[str, Any]],
    enabled_tools: Optional[List[str]] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    reply_max_tokens: Optional[int] = DEFAULT_REPLY_MAX_TOKENS,
    tool_output_limit: int = DEFAULT_TOOL_OUTPUT_LIMIT,
    max_total_tokens: Optional[int] = DEFAULT_MAX_TOTAL_TOKENS,
) -> Tuple[str, Dict[str, Any]]:
    """
    非流式版本：把 run_agent_stream 的事件流合并成 (最终文本, meta)。
    供 `/api/chat` 使用；逻辑与流式完全一致，只是不逐字返回。
    """
    text_parts: List[str] = []
    meta: Dict[str, Any] = {}
    plan_steps: List[Any] = []
    for event in run_agent_stream(
        provider, messages, enabled_tools, max_steps, reply_max_tokens, tool_output_limit, max_total_tokens
    ):
        if event["type"] == "content":
            text_parts.append(event["text"])
        elif event["type"] == "plan":
            # 计划清单：非流式也带进 meta，与 SSE 行为保持一致（三端一致原则）
            plan_steps = event["steps"]
        elif event["type"] == "demote":
            # 中间步骤的碎碎念被"撤回"：非流式这里同样不能算进最终回答
            # （与前端 stores.js 的处理保持一致，否则两边表现会不一样）
            joined = "".join(text_parts)
            if joined.endswith(event["text"]):
                text_parts = [joined[: -len(event["text"])]] if joined[: -len(event["text"])] else []
        elif event["type"] == "done":
            meta = event["meta"]
            if plan_steps:
                meta["plan"] = plan_steps
    return "".join(text_parts), meta
