"""
agent/runner.py - Agent 主循环（ReAct：思考 → 调工具 → 再思考）

【谁在用】
    SilverWolf.py 的 `/api/chat/stream`（流式）与 `/api/chat`（非流式）在「Agent 模式」下调用本模块。

【原理】
    1. 把 agent/tools 的 openai_tool_schemas() 生成的工具表交给模型（OpenAI 兼容 function calling）；
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
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from openai import APIError, BadRequestError

import provider_config
from agent import tools as agent_tools          # 工具仓库（注册表 + 执行）
from agent import artifacts
from agent import plans
from agent.tools import _run_state as run_state  # 本轮工具状态（计数/缓存）
from middleware.stream_utils import extract_stream_delta
from middleware.token_usage import UsageTracker
from middleware.logging_system import configure_logging, register_secret

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 4
# 工具轮数上限的唯一边界：设置面板、请求解析、后台任务都从这里取，禁止各处再写数字
MAX_STEPS_LIMIT = 12
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


def resolve_approval(identity, approved, reason=''):
    entry = APPROVAL_REGISTRY.get(identity)
    if not entry:
        return False
    entry['approved'] = bool(approved)
    entry['reason'] = reason
    entry['event'].set()
    return True


def _parse_tool_args(raw: str) -> Dict[str, Any]:
    """返回 JSON 对象；非法 JSON/非对象抛 ValueError，由循环写回失败结果，禁止空参误执行。"""
    args = json.loads(raw) if raw else {}
    if not isinstance(args, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return args


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 SDK 对象与兼容供应商的字典增量。"""
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _merge_tool_call_fragments(buffer: Dict[int, Dict[str, Any]], deltas: List[Any]) -> None:
    """
    合并流式返回的 tool_calls 片段。

    流式模式下，函数的 name/arguments 是分片到达的（例如 arguments: '{"ci' + 'ty":"北京"}'），
    因此用 index 作为 key 累积，等这一轮结束后再整体解析。
    """
    for d in deltas or []:
        idx = _field(d, "index", 0) or 0
        if not isinstance(idx, int) or idx < 0:
            raise ValueError("工具分片 index 必须是非负整数")
        slot = buffer.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if _field(d, "id"):
            slot["id"] = _field(d, "id")
        fn = _field(d, "function")
        if fn is not None:
            if _field(fn, "name"):
                slot["name"] = _field(fn, "name")
            arguments = _field(fn, "arguments")
            if arguments:
                if not isinstance(arguments, str):
                    raise ValueError("工具分片 arguments 必须是字符串")
                slot["arguments"] += arguments


def _retry_parameter(error: APIError, kwargs: Dict[str, Any]) -> Optional[str]:
    """从结构化 param/code 与错误文案定位不兼容参数；只对 400/422 做有限降级。

    鉴权、限流、服务故障不应被误判成工具不支持。未知 400 优先去 usage 扩展，
    再去 tools；不无依据地撤销用户的 token 上限。
    """
    if not isinstance(error, BadRequestError) and getattr(error, "status_code", None) not in (400, 422):
        return None
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    param = str(getattr(error, "param", None) or detail.get("param") or "").lower()
    code = str(getattr(error, "code", None) or detail.get("code") or "").lower()
    message = str(error).lower()
    clues = " ".join((param, code, message))
    if "context_length" in clues or "context window" in clues or "context length" in clues:
        return None
    if "max_tokens" in clues or "max_completion_tokens" in clues or "max output" in clues:
        return "max_tokens" if "max_tokens" in kwargs else None
    if "stream_options" in clues or "include_usage" in clues:
        return "stream_options" if "stream_options" in kwargs else None
    if "tool" in clues or "function" in clues:
        return "tools" if "tools" in kwargs else None
    # 有明确但无关的参数时终止，不拿正常工具配置碰运气。
    if param:
        return None
    return next((key for key in ("stream_options", "tools") if key in kwargs), None)


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
    """流式执行模型→工具循环，保持原参数和事件字段，由 SilverWolf 转为 SSE。

    messages 保存中间 assistant/tool 历史；最终正文仍通过 content 事件交给调用方。
    max_steps 是工具轮数，耗尽后额外请求一次禁工具总结。请求兼容降级在本轮运行内
    持久化；失败通过 warning 与 done.meta.error 反馈，不抛未捕获异常。
    """
    configure_logging()
    provider = dict(provider)
    register_secret(provider.get("api_key") or "")
    run_id = uuid.uuid4().hex[:12]
    model = provider.get("model")
    tracker = provider.get('_usage_tracker') or UsageTracker()
    tools_used: List[Dict[str, Any]] = []
    searches: List[Dict[str, Any]] = []
    rag_used: List[Dict[str, Any]] = []
    tools_unsupported = False
    token_kwargs = {"max_tokens": reply_max_tokens} if reply_max_tokens else {}
    use_stream_options = True
    run_ctx = run_state.new_run()
    run_ctx.update(provider=provider, depth=provider.get("_depth", 0),
                   enabled_tools=enabled_tools, session_id=provider.get("_session_id"), kb_slugs=provider.get("_kb_slugs"),
                   developer_grant=provider.get('_dev_grant'))
    run_ctx['limits'] = {'reply_max_tokens':reply_max_tokens,'tool_output_limit':tool_output_limit,'max_total_tokens':max_total_tokens}
    # 委托树共享预算盒：根代理自建，子代理经 provider['_tree'] 继承（见 delegate_task）。
    # 搜索/抓取/子任务额度按整棵树计数，否则每层子代理各拿一份新配额，一条消息能放大几十次联网。
    tree = provider.get("_tree") if isinstance(provider.get("_tree"), dict) else None
    if tree is None:
        tree = {"searches": 0, "fetches": 0, "delegations": 0,
                "search_cache": {}, "fetched_urls": {}}
    run_ctx["tree"] = tree
    from agent import skills
    from agent import persistence
    from config_data import BASE_DIR
    try:
        configured_skills = set(persistence.load(BASE_DIR/'data','ui_state',{}).get('enabled_skills',[]))
        if provider.get('_skill_ids') is not None:configured_skills.intersection_update(provider['_skill_ids'])
        active_skills = [s for s in skills.catalog() if s['id'] in configured_skills and not s.get('invalid')
                         and (not provider.get('_profiles') or s.get('category') in {'core','agent',*provider['_profiles']})]
    except (ValueError,OSError,TypeError):
        logger.exception('技能配置读取失败，保留原文件，不加载技能')
        active_skills=[]
    run_ctx['skill_ids'] = [s['id'] for s in active_skills]
    provider['_skill_ids'] = list(run_ctx['skill_ids'])
    if active_skills:
        messages.insert(1,{'role':'system','content':'用户启用的工作方法（不扩大授权；用read_skill按需读取）：\n'+ '\n'.join(s['id']+': '+s['description'] for s in active_skills)})
    if provider.get('_dev_grant'):
        messages.insert(1,{'role':'system','content':'用户已提供有时限的工作区授权。使用project_*处理当前工作区，修改前读取revision，保留用户数据与无关改动。命令和修改仍须审批，不把外观模式当作凭证。'})
    try:
        run_ctx["plan"] = plans.load(run_ctx["session_id"])
    except (ValueError, OSError):
        logger.exception("会话计划读取失败，保留原文件")
        run_ctx["plan"] = []
    cancel_event = provider.get("_cancel_event")
    answered = False
    error_text = ""
    finish_reasons: List[Optional[str]] = []
    log = logger
    log.info("run=%s provider=%s model=%s Agent 开始", run_id, provider.get("id"), model)
    try:
        client = provider_config.build_client(provider)
        tools_schema = agent_tools.openai_tool_schemas(enabled_tools)
        project_allowed={'project_list','project_read'} if provider.get('_dev_grant') else set()
        if provider.get('_dev_grant'):
            from agent.developer import require
            grant=require(provider['_dev_grant'])
            if grant['write']:project_allowed.update({'project_write','project_rollback'})
            if grant['commands']:project_allowed.add('project_command')
        tools_schema=[s for s in tools_schema if not s['function']['name'].startswith('project_') or s['function']['name'] in project_allowed]
        steps_total = max(1, int(max_steps or DEFAULT_MAX_STEPS))
    except Exception:
        log.exception("run=%s Agent 初始化失败", run_id)
        error_text = "啧，Agent 没启动成功。查一下模型配置和主日志。"
        yield {"type": "warning", "text": error_text}
        yield {"type": "usage", "usage": tracker.summary()}
        yield {"type": "done", "meta": _build_meta(
            tracker, provider, tools_used, searches, rag_used,
            {"tools_unsupported": False, "error": error_text})}
        return

    allowed_names = {s["function"]["name"] for s in tools_schema}

    def open_stream(with_tools: bool):
        """有限重试创建流，返回 stream；只移除被拒参数，状态供后续轮次共用。"""
        nonlocal tools_unsupported, use_stream_options, token_kwargs
        # 最多三个可选参数各降级一次，再进行一次最终尝试。
        for attempt in range(4):
            kwargs = {"model": model, "messages": messages, "stream": True, **token_kwargs}
            if with_tools and tools_schema and not tools_unsupported:
                kwargs["tools"] = tools_schema
            if use_stream_options:
                kwargs["stream_options"] = {"include_usage": True}
            try:
                return client.chat.completions.create(**kwargs)
            except (BadRequestError, APIError) as exc:
                parameter = _retry_parameter(exc, kwargs)
                log.warning("run=%s attempt=%d rejected=%s status=%s code=%s param=%s options=%s",
                            run_id, attempt + 1, parameter, getattr(exc, "status_code", None),
                            getattr(exc, "code", None), getattr(exc, "param", None),
                            sorted(kwargs), exc_info=True)
                if parameter is None or attempt == 3:
                    raise
                if parameter == "max_tokens":
                    token_kwargs = {}
                elif parameter == "stream_options":
                    use_stream_options = False
                elif parameter == "tools":
                    tools_unsupported = True
        raise RuntimeError("模型兼容重试耗尽")

    # 统一消费正常轮与总结轮，避免总结漏算 reasoning、漏降级或漏捕获流中断。
    for step in range(steps_total + 1):
        if cancel_event and cancel_event.is_set():
            error_text = "任务已取消。"
            break
        final_step = step == steps_total
        if final_step:
            yield {"type": "warning", "text": "工具轮数用尽，先把已有资料整理成结论。"}
            messages.append({"role": "system", "content": (
                "工具调用到此为止，禁止再调用工具。直接回答用户最初的问题，"
                "给出结论与来源；缺少资料就说没查到，别再说要去搜。")})
        tracker.next_step()
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_buffer: Dict[int, Dict[str, Any]] = {}
        usage_obj = None
        finish_reason = None
        stream = None
        step_error = ""
        previously_unsupported = tools_unsupported
        try:
            stream = open_stream(with_tools=not final_step)
            if tools_unsupported and not previously_unsupported:
                yield {"type": "warning", "text": "啧，这个模型不支持工具。本轮切到普通对话。"}
            for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    step_error = "任务已取消。"
                    break
                content, reasoning = extract_stream_delta(chunk)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    yield {"type": "reasoning", "text": reasoning}
                if content:
                    content_parts.append(content)
                    yield {"type": "content", "text": content}
                choices = _field(chunk, "choices")
                if choices:
                    choice = choices[0]
                    finish_reason = _field(choice, "finish_reason") or finish_reason
                    calls = _field(_field(choice, "delta"), "tool_calls")
                    if calls:
                        _merge_tool_call_fragments(tool_buffer, calls)
                if _field(chunk, "usage") is not None:
                    usage_obj = _field(chunk, "usage")
        except Exception:
            # 已输出的分片不能重放，否则会重复正文，甚至重复执行有副作用的工具。
            log.exception("run=%s step=%d 模型请求或流读取失败", run_id, step + 1)
            step_error = "啧，模型链路断了。这轮没跑完，查一下错误日志。"
        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                try:
                    stream.close()
                except Exception:
                    log.warning("run=%s 关闭模型流失败", run_id, exc_info=True)

        text = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)
        finish_reasons.append(finish_reason)
        # 工具名/参数也是输出 token；messages 尚未追加本轮结果，输入不会算重。
        prompt_text = json.dumps(messages, ensure_ascii=False, default=str)
        tool_text = json.dumps(list(tool_buffer.values()), ensure_ascii=False) if tool_buffer else ""
        try:
            has_usage = usage_obj is not None and tracker.add_provider_usage(usage_obj)
        except (TypeError, ValueError, OverflowError):
            log.warning("run=%s step=%d usage 格式无效，退回估算", run_id, step + 1, exc_info=True)
            has_usage = False
        if not has_usage:
            tracker.add_estimate(prompt_text, text + reasoning_text + tool_text)
        log.info("run=%s step=%d finish=%s tools=%d usage=%s", run_id, step + 1,
                 finish_reason, len(tool_buffer), tracker.summary())

        # 在硬闸、流异常、禁工具防护之前撤回过程话，所有出口都保持三端一致。
        if tool_buffer and text:
            yield {"type": "demote", "text": text}
        if step_error:
            error_text = step_error
            yield {"type": "warning", "text": error_text}
            break
        if finish_reason == "length":
            yield {"type": "warning", "text": "回复碰到长度上限了。工具参数没写完就不执行。"}
        if tool_buffer and (final_step or tools_unsupported or not tools_schema):
            error_text = "啧，模型越过了禁工具指令。这轮已拦截，换个模型再试。"
            log.error("run=%s step=%d 禁工具请求仍返回 tool_calls", run_id, step + 1)
            yield {"type": "warning", "text": error_text}
            break
        if not tool_buffer:
            if finish_reason == "tool_calls":
                if text:
                    yield {"type": "demote", "text": text}
                error_text = "工具调用帧不完整。这轮已停，查一下错误日志。"
                log.error("run=%s finish=tool_calls 但缺少分片", run_id)
                yield {"type": "warning", "text": error_text}
            elif text.strip():
                answered = True
            else:
                error_text = "……模型没给出正文。这轮没拿到最终回答。"
                yield {"type": "warning", "text": error_text}
            break

        total_now = tracker.summary()["total_tokens"]
        stop_tools = bool(max_total_tokens and total_now > max_total_tokens) or finish_reason == "length"
        if stop_tools:
            reason = "累计 token 已达上限" if finish_reason != "length" else "工具参数被截断"
            yield {"type": "warning", "text": f"{reason}，不再执行新工具。先用已有资料收尾。"}

        calls = []
        for idx, slot in sorted(tool_buffer.items()):
            calls.append({"id": slot["id"] or f"call_{run_id}_{step}_{idx}",
                          "name": slot["name"], "raw_arguments": slot["arguments"] or "{}"})
        messages.append({"role": "assistant", "content": text or None, "tool_calls": [
            {"id": c["id"], "type": "function", "function": {
                "name": c["name"], "arguments": c["raw_arguments"]}} for c in calls]})

        for c in calls:
            name, args = c["name"], {}
            denied = False
            try:
                if cancel_event and cancel_event.is_set():
                    raise ValueError("任务已取消，工具未执行")
                if stop_tools:
                    raise ValueError("预算或长度限制：工具未执行")
                if name not in allowed_names:
                    raise ValueError("模型请求了未启用的工具")
                args = _parse_tool_args(c["raw_arguments"])
                spec = agent_tools.tool_spec(name)
                if spec.get("approval_required"):
                    request_id = f"apv_{uuid.uuid4().hex[:12]}"
                    entry = {"approved": False, "event": threading.Event()}
                    APPROVAL_REGISTRY[request_id] = entry
                    try:
                        yield {"type": "tool_approval", "request_id": request_id, "name": name, "args": args}
                        deadline = time.monotonic() + APPROVAL_TIMEOUT
                        while not entry["event"].is_set() and time.monotonic() < deadline:
                            if cancel_event and cancel_event.is_set():
                                break
                            yield {"type": "approval_wait", "request_id": request_id}
                        waited = entry["event"].is_set()
                        denied = not (waited and entry["approved"])
                    finally:
                        # 包括生成器 close() 在审批 yield 处退出的路径。
                        APPROVAL_REGISTRY.pop(request_id, None)
                    if denied:
                        raise ValueError(entry.get('reason') or ("用户拒绝了这次操作" if waited else "等待批准超时，已自动拒绝"))
                yield {"type": "tool_call", "name": name, "args": args}
                # 每次工具执行前绑定，结束前恢复；ContextVar token 不跨流式 yield。
                with run_state.scoped(run_ctx):
                    if name in persistence.load(BASE_DIR/'data','ui_state',{}).get('disabled_tools',[]):
                        raise ValueError('工具已被用户关闭')
                    result = agent_tools.execute_tool(name, args)
                if not isinstance(result, dict) or not isinstance(result.get("content"), str):
                    raise ValueError("工具返回格式无效")
                if not isinstance(result.get("meta", {}), dict):
                    raise ValueError("工具 meta 必须是对象")
                result["ok"] = bool(result.get("ok", False))
            except Exception as exc:
                log.warning("run=%s step=%d tool=%s 执行跳过或失败", run_id, step + 1, name, exc_info=True)
                result = {"ok": False, "content": f"工具 {name} 未完成：{str(exc)[:200]}",
                          "meta": {"ok": False, "tool": name, "denied": denied}}
            tool_meta = result.get("meta", {})
            if tool_meta.get("subagent") and isinstance(tool_meta.get("usage"), dict):
                child_usage = tool_meta["usage"]
                # 子代理的模型账单同样属于本轮，不能只统计父循环。
                tracker.merge(child_usage)
            record = {"name": name, "args": args, "ok": result["ok"]}
            if denied:
                record["denied"] = True
            tools_used.append(record)
            _collect_tool_meta(name, tool_meta, searches, rag_used)
            # 先补齐历史再 yield 结果，避免暂停时留下缺失的 tool_call_id。
            try:
                stored_output = artifacts.preserve(result["content"], tool_output_limit)
            except ValueError as exc:
                stored_output = f"工具输出未保全：{exc}。请分段请求，不能假定内容已经保存。"
                result["ok"] = False
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": stored_output})
            logging.getLogger("agent.tools.execution").info(
                "run=%s step=%d tool=%s ok=%s denied=%s chars=%d", run_id, step + 1,
                name, result["ok"], denied, len(result["content"]))
            yield {"type": "tool_result", "name": name, "ok": result["ok"],
                   "summary": result["content"][:300], "meta": tool_meta}
            if tool_meta.get("plan") is not None:
                yield {"type": "plan", "steps": tool_meta["plan"]}
        if stop_tools:
            # 直接跳到唯一的禁工具总结轮，不再浪费余下的工具预算。
            steps_total = step + 1

    if not answered:
        if not error_text:
            error_text = "工具轮数已用尽，这轮没生成最终回答。"
            yield {"type": "warning", "text": error_text}
        yield {"type": "content", "text": f"……{error_text}"}
    extra = {"tools_unsupported": tools_unsupported, "finish_reasons": finish_reasons}
    if error_text:
        extra["error"] = error_text
    log.info("run=%s Agent 结束 answered=%s failed=%s", run_id, answered, bool(error_text))
    yield {"type": "usage", "usage": tracker.summary()}
    yield {"type": "done", "meta": _build_meta(tracker, provider, tools_used, searches, rag_used, extra)}


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
        if event["type"] == "tool_approval":
            # 非流式客户端拿不到审批事件；立即拒绝，避免静默占线程等60秒。
            resolve_approval(event["request_id"], False, '非流式请求不能交互审批，请使用流式聊天。')
        if event["type"] == "approval_wait":
            time.sleep(.1)  # 非流式本来就在专用工作线程，避免忙轮询。
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
