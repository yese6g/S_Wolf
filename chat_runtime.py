"""聊天流生命周期：SilverWolf 路由调用，阻塞工作放线程，断连时持久化已收到的正文。

异步桥逐次拉同步生成器；审批等待事件变成 SSE 心跳，在事件循环中等候而不占线程。
失败回退只接收初始上下文副本，禁止把未配对的工具消息带给另一个供应商。
"""
import asyncio
import copy
import json
import logging
import hashlib
import re
import threading
import time
from config_data import normalize_profiles
from middleware.token_usage import UsageTracker
from middleware.stream_utils import extract_stream_delta

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
_END = object()
_RUNS = {}


async def replay(row, cursor=0):
    """按事件序号重连；只回放同一次生成，绝不重复提交模型或工具。"""
    row['subscribers'] += 1
    try:
        while True:
            events=[(n,s) for n,s in row['events'] if n>cursor]
            if row['events'] and cursor < row['events'][0][0]-1:
                yield 'data: '+json.dumps({'error':'续传缓存已过期，请从会话历史恢复。'},ensure_ascii=False)+'\n\ndata: [DONE]\n\n'
                return
            for n,frame in events:
                yield f'id: {n}\n'+frame
                cursor=n
            if row['done']:return
            async with row['condition']:
                if row['sequence']>cursor:continue
                try:await asyncio.wait_for(row['condition'].wait(),15)
                except asyncio.TimeoutError:pass
                else:continue
            # 心跳在锁外发出：慢客户端写包时不能攥着 condition 挡住 retain 追加
            yield ': heartbeat\n\n'
    finally:
        row['subscribers']-=1
        if not row['subscribers'] and not row['done']:
            async def disconnected():
                await asyncio.sleep(15)
                if not row['subscribers'] and not row['done']:row['cancel'].set()
            asyncio.create_task(disconnected())


async def retain(row,source):
    async def append(frame):
        async with row['condition']:
            row['sequence']+=1;row['events'].append((row['sequence'],frame));row['bytes']+=len(frame.encode())
            while row['bytes']>2*1024*1024 and len(row['events'])>1:
                _,old=row['events'].pop(0);row['bytes']-=len(old.encode())
            row['condition'].notify_all()
    try:
        async for frame in source:
            if not frame.startswith(':'):await append(frame)
    except asyncio.CancelledError:
        await append('data: '+json.dumps({'stopped':True,'session_id':row.get('session_id')})+'\n\ndata: [DONE]\n\n')
    except Exception:
        logger.exception('续传生成失败')
        await append('data: {"error":"生成失败，已保存收到的内容。"}\n\ndata: [DONE]\n\n')
    finally:
        row['done']=True;row['expires']=time.monotonic()+300
        async with row['condition']:row['condition'].notify_all()


def stop_run(identity):
    from fastapi import HTTPException
    from middleware.security import access_token
    row=_RUNS.get(identity)
    if not row:return {'ok':False,'message':'生成已结束或缓存已清理'}
    if row['principal']!=hashlib.sha256(access_token().encode()).hexdigest():
        raise HTTPException(403,'生成不属于当前授权')
    row['cancel'].set()
    return {'ok':True}


async def iterate_events(iterator):
    """逐项在线程拉取同步迭代器；关闭发生在同一条消费链，异常/取消也释放模型流。"""
    try:
        while True:
            event = await run_in_threadpool(next, iterator, _END)
            if event is _END:
                break
            if isinstance(event, dict) and event.get("type") == "approval_wait":
                await asyncio.sleep(.1)
                yield None
            else:
                yield event
    finally:
        with anyio.CancelScope(shield=True):
            close = getattr(iterator, "close", None)
            if close:
                await run_in_threadpool(close)


def prepare(request, services):
    """同步准备会话/检索/摘要，返回会话、初始上下文、供应商和运行参数。"""
    mode, tools, steps = services._resolve_agent_options(request)
    sid, messages, rag = services._prepare_chat_context(request, False if mode else None)
    provider = services._resolve_provider(request.provider_id, request.model, request.use_local)
    # 方向在 _resolve_agent_options 里已校验过，这里再归一一次只为塞给 runner 做二次过滤
    try:
        wanted_profiles = normalize_profiles(getattr(request, "profile", None), getattr(request, "profiles", None))
    except ValueError:
        wanted_profiles = []
    provider = {**provider, '_usage_tracker':UsageTracker(),'_dev_grant':request.developer_grant,'_profiles':wanted_profiles}
    messages = services._apply_context_management(sid, messages, provider)
    attachment_context = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    services.save_message_with_meta(sid, "user", request.message,
        {"attachment_context": attachment_context} if request.attachments else None)
    return sid, messages, rag, provider, services._runtime_limits(), mode, tools, steps


async def stream_response(request, services):
    row=None
    if request.run_id:
        from fastapi import HTTPException
        from middleware.security import access_token
        identity=request.run_id
        if not re.fullmatch(r'[a-f0-9]{32}',identity):raise HTTPException(400,'生成标识无效')
        principal=hashlib.sha256(access_token().encode()).hexdigest()
        for key,old in list(_RUNS.items()):
            if old['done'] and old['expires']<time.monotonic():_RUNS.pop(key)
        fingerprint=hashlib.sha256(json.dumps(request.model_dump(exclude={'resume','last_event_id'}),sort_keys=True,default=str).encode()).hexdigest()
        if identity in _RUNS:
            row=_RUNS[identity]
            if row['principal']!=principal:raise HTTPException(403,'生成不属于当前授权')
            if row['fingerprint']!=fingerprint:raise HTTPException(409,'生成标识对应的请求内容不同')
            return StreamingResponse(replay(row,max(0,request.last_event_id)),media_type='text/event-stream')
        if request.resume:raise HTTPException(410,'续传缓存不存在，请读取已保存的会话')
        if sum(not old['done'] for old in _RUNS.values())>=8:raise HTTPException(429,'最多同时生成8项')
        for key in sorted(_RUNS,key=lambda key:_RUNS[key].get('expires',float('inf'))):
            if len(_RUNS)<32:break
            if _RUNS[key]['done']:_RUNS.pop(key)
        row={'events':[],'sequence':0,'bytes':0,'condition':asyncio.Condition(),'cancel':threading.Event(),
             'done':False,'expires':float('inf'),'subscribers':0,'principal':principal,'fingerprint':fingerprint}
        _RUNS[identity]=row
    try:sid, initial, rag, provider, limits, mode, tools, steps = await run_in_threadpool(prepare, request, services)
    except BaseException:
        if row:
            row['done']=True
            async with row['condition']:row['condition'].notify_all()
            _RUNS.pop(request.run_id,None)
        raise
    if row:row['session_id']=sid;provider['_cancel_event']=row['cancel']

    def packet(value):
        return "data: " + json.dumps({**value, "session_id": sid}, ensure_ascii=False) + "\n\n"

    async def generate():
        content, reasoning, meta = "", "", {}
        complete = False
        active = provider
        fallback_warning = ""
        try:
            candidates = [provider]
            local = await run_in_threadpool(services.provider_config.get_local_provider)
            if provider.get("kind") != "local" and local:
                candidates.append({**local,'_usage_tracker':provider['_usage_tracker'],'_cancel_event':provider.get('_cancel_event'),
                                   '_dev_grant':provider.get('_dev_grant'),'_profiles':provider.get('_profiles')})
            for index, active in enumerate(candidates):
                if index:
                    if content:
                        yield packet({"demote": content})
                        reasoning += "\n" + content
                        content = ""
                    meta = {}
                    fallback_warning = "云端链路断了，切到本地模型重试。"
                    yield packet({"fallback_warning": fallback_warning})
                try:
                    if mode:
                        event_source = services.agent_runner.run_agent_stream(
                            {**active, "_session_id": sid, "_kb_slugs": services._rag_libraries(request)}, copy.deepcopy(initial), tools, steps,
                            reply_max_tokens=limits["reply_max_tokens"],
                            tool_output_limit=limits["tool_output_limit"],
                        )
                    else:
                        stream, fallback, warning, active = await run_in_threadpool(
                            services._call_llm_stream, active, copy.deepcopy(initial), limits["reply_max_tokens"])
                        active['_usage_tracker'] = provider['_usage_tracker']
                        def plain_events():
                            tracker = active['_usage_tracker']
                            tracker.next_step()
                            parts, thoughts, usage = [], [], None
                            try:
                                for chunk in stream:
                                    if row and row['cancel'].is_set():raise asyncio.CancelledError()
                                    chunk_usage = chunk.get('usage') if isinstance(chunk,dict) else getattr(chunk,'usage',None)
                                    if chunk_usage is not None:usage = chunk_usage
                                    text, thought = extract_stream_delta(chunk)
                                    if thought:
                                        thoughts.append(thought)
                                        yield {"type": "reasoning", "text": thought}
                                    if text:
                                        parts.append(text)
                                        yield {"type": "content", "text": text}
                            finally:
                                if callable(getattr(stream, "close", None)):
                                    stream.close()
                                try: has_usage = tracker.add_provider_usage(usage)
                                except (TypeError,ValueError): has_usage = False
                                if not has_usage: tracker.add_estimate(json.dumps(initial,ensure_ascii=False),''.join(parts+thoughts))
                            yield {'type':'usage','usage':tracker.summary()}
                            yield {'type':'done','meta':{'usage':tracker.summary()}}
                        event_source = plain_events()
                    event_stream = iterate_events(event_source)
                    try:
                        async for event in event_stream:
                            if row and row['cancel'].is_set():raise asyncio.CancelledError()
                            if event is None:
                                yield ": approval-wait\n\n"
                                continue
                            kind = event.get("type")
                            if kind == "content":
                                content += event["text"]
                                yield packet({"content": event["text"]})
                            elif kind == "reasoning":
                                reasoning += event["text"]
                                yield packet({"reasoning": event["text"]})
                            elif kind == "demote":
                                dropped = event["text"]
                                if dropped and content.endswith(dropped):
                                    content = content[:-len(dropped)]
                                reasoning += "\n" + dropped
                                yield packet({"demote": dropped})
                            elif kind == "done":
                                meta = event.get("meta", {})
                            elif kind in {"warning", "usage", "plan"}:
                                field = {"warning": "text", "usage": "usage", "plan": "steps"}[kind]
                                yield packet({kind: event[field]})
                            elif kind in {"tool_call", "tool_result", "tool_approval"}:
                                yield packet({kind: {k: v for k, v in event.items() if k != "type"}})
                    finally:
                        with anyio.CancelScope(shield=True):
                            await event_stream.aclose()
                    if meta.get("error"):
                        raise RuntimeError(meta["error"])
                    break
                except Exception as exc:
                    logger.exception("会话 %s 流式调用失败", sid)
                    if index + 1 == len(candidates):
                        meta["error"] = str(exc)[:300]
                        yield packet({"error": meta["error"]})
            if not content.strip():
                content = "……这轮没拿到结论。把问题收窄一点再试。"
                yield packet({"content": content})
            meta.update({"session_id": sid, "agent_mode": mode,
                         "provider_id": active["id"], "model": active["model"],
                         "rag_used": bool(rag["has_context"] or meta.get("rag_hits")),
                         "rag_sources": list(dict.fromkeys(rag["sources"] + [s for hit in meta.get("rag_hits", []) for s in hit.get("sources", [])]))})
            if fallback_warning:
                meta["fallback_warning"] = fallback_warning
            if not meta.get("error"):
                if row and row['cancel'].is_set():raise asyncio.CancelledError()
                meta["knowledge_updates"] = await run_in_threadpool(services.learn_from_exchange,
                    request.message, content, active, services._rag_libraries(request))
            if row and row['cancel'].is_set():raise asyncio.CancelledError()
            meta['usage'] = active['_usage_tracker'].summary()
            complete = True
            yield packet(meta)
            yield "data: [DONE]\n\n"
        finally:
            # GeneratorExit/取消属于 BaseException；finally 中无 yield，盾牌保证落库完成。
            saved = {**meta, "reasoning": reasoning, "stopped": not complete}
            saved['usage'] = active['_usage_tracker'].summary()
            if not content and not complete:
                content = "……这轮被停止了。"
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(services.save_message_with_meta, sid, "assistant", content, saved)
                from agent.tools._policy import apply_hook
                try:await run_in_threadpool(apply_hook,'after_response','chat',complete)
                except Exception:logger.exception('回答后守卫记录失败')

    if row:
        row['task']=asyncio.create_task(retain(row,generate()))
        return StreamingResponse(replay(row),media_type='text/event-stream')
    return StreamingResponse(generate(), media_type="text/event-stream")


async def shutdown_runs():
    tasks=[]
    for row in _RUNS.values():
        row['cancel'].set()
        if row.get('task') and not row['done']:row['task'].cancel();tasks.append(row['task'])
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    _RUNS.clear()
