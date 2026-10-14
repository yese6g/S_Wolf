"""
SilverWolf 路由模块 - 星核猎手黑客 AI
提供 APIRouter 供 main.py 挂载。
所有业务逻辑和路由定义在此文件中。

v0.6.0 变更：
- 流式输出修复：兼容 choices 为空 / delta 缺失 / content 为列表的异常结构，
  修复部分云端接口首帧即崩溃导致「不显示、刷新才出现」的问题
- 新增「模型提供商（Provider）」体系：本地 Ollama 与任意 OpenAI 兼容云端接口
  统一管理（数据保存在 data/providers.json），可在设置面板添加 API Key / 换模型
- 输入框支持在 Provider 与模型之间随时切换（工作台式）
- 支持 DeepSeek 等模型的 reasoning_content（思考过程）流式展示
"""

import uuid
import copy
import threading
from functools import wraps
import json
import logging
import mimetypes
import os
import time
from typing import Dict, List, Optional
import sys
from starlette.concurrency import run_in_threadpool
import chat_runtime

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from openai import APIError, APIConnectionError

# ── 项目内模块 ──
from config_data import (
    SYSTEM_PROMPT,
    ASSISTANT_GREETING,
    LOCAL_BASE_URL,
    APP_NAME,
    APP_VERSION,
    SUPPORTED_FILE_EXTENSIONS,
    DEFAULT_KB_SLUG,
    BASE_DIR,
    normalize_profiles,
)
from file_history_store import (
    init_db,
    create_session,
    list_sessions,
    get_session,
    rename_session,
    delete_session,
    save_message_with_meta,
    get_session_summary,
    set_session_summary,
    build_messages,
    generate_title,
)
from rag import build_rag_prompt, should_use_rag, selected_kbs, learn_from_exchange
from vector_stores import get_embedding_backend
from app_file_upload import router as kb_router
from knowledge_base import list_kb_files, validate_kb_slug, delete_kb_file
from middleware.stream_utils import normalize_delta_text as _normalize_delta_text
from agent import runner as agent_runner
from agent import tools as agent_tools
from agent.tools.core.memory import (
    add_memory as memory_add,
    clear_memories as memory_clear,
    delete_memory as memory_delete,
    load_memories as memory_list,
    search_memories as memory_search,
)
from middleware import context as context_manager
import provider_config
import file_attachments
import provider_presets
import appearance_presets
from agent import persistence
from agent import jobs, plans, mcp_client, workspace_history

# ── 日志配置 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("SilverWolf")

# ── 数据库初始化（幂等，首次启动自动建表） ──
init_db()

# ── 创建路由（使用 APIRouter，由 main.py 挂载到 app） ──
router = APIRouter()

# Vue 前端目录
FRONTEND_DIR = BASE_DIR / "frontend"

# v0.9.2：头像与界面状态持久化（data/ 下，随 gitignore 不入库）
AVATAR_DIR = BASE_DIR / "data" / "images"
UI_STATE_FILE = BASE_DIR / "data" / "ui_state.json"
AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024


def _avatar_files(role: str):
    return list(AVATAR_DIR.glob(f"{role}.*")) if AVATAR_DIR.exists() else []


def _avatar_exists(role: str) -> bool:
    identity = _ui_settings().get('avatar_'+role+'_preset')
    if identity == 'default':
        return False
    if identity:
        try:
            appearance_presets.avatar_path(role, identity)
            return True
        except ValueError:
            pass
    return bool(_avatar_files(role))


_UI_LOCK = persistence.LOCK


def _locked_ui(handler):
    """界面状态的读改写共享锁，防止头像选择与设置保存互相覆盖。"""
    @wraps(handler)
    def execute(*args, **kwargs):
        with _UI_LOCK:
            return handler(*args, **kwargs)
    return execute


@_locked_ui
def _set_avatar_preset(role, identity):
    try:
        data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8")) if UI_STATE_FILE.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("设置不是对象")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="设置文件暂时读不了，原配置保留。") from exc
    data['avatar_'+role+'_preset']=identity or ''
    persistence.save(UI_STATE_FILE.parent,UI_STATE_FILE.stem,data)


# ── 请求/响应模型 ──

class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class RenameSessionRequest(BaseModel):
    title: str


class AttachmentRef(BaseModel):
    """聊天附件引用（由 /api/chat/attachments 上传后返回）。"""
    name: str
    path: str


class UiStateRequest(BaseModel):
    """界面状态：当前知识库与 RAG 开关（持久化，重启恢复）。"""
    kb_slug: Optional[str] = None
    kb_slugs: Optional[List[str]] = None
    rag_auto_learn: Optional[bool] = None
    use_rag: Optional[bool] = None
    # v2.0.0：Agent 相关开关（持久化，重启后保持用户选择）
    agent_mode: Optional[bool] = None
    max_steps: Optional[int] = None
    enable_web: Optional[bool] = None
    # v2.0.0：联网搜索后端配置（二选一即可；留空表示不改动已保存的值）
    tavily_api_key: Optional[str] = None
    # v2.0.2：可选的网页正文抓取服务（Firecrawl，适合 JS 渲染站点）
    firecrawl_api_key: Optional[str] = None
    # v2.0.4：是否启用 Tavily（关掉后只用免 Key 爬虫，不消耗额度）
    tavily_enabled: Optional[bool] = None
    # v2.0.4 / v2.1.0 / v2.1.1：上下文管理（控制 token 消耗）
    context_budget: Optional[int] = None       # 上下文窗口 token 预算（含 system/RAG/历史）
    min_keep_turns: Optional[int] = None       # 保底保留的最近轮数
    unlimited_turns: Optional[bool] = None     # 无限轮数：一条历史都不丢（不再裁剪）
    context_compress: Optional[bool] = None    # 超预算的旧消息是否压缩成摘要
    tool_output_limit: Optional[int] = None    # 工具结果进入上下文的字符上限
    reply_max_tokens: Optional[int] = None     # 单次回复的最大 token
    reply_unlimited: Optional[bool] = None     # 无限长度：不限制单次回复长度


class SearchTestRequest(BaseModel):
    """搜索自检请求：engine 用于分别验证 Tavily 与免 Key 爬虫。"""
    engine: Optional[str] = "auto"


class ApprovalRequest(BaseModel):
    """高危工具审批回调（前端弹窗允许/拒绝后调用）。"""
    request_id: str
    approve: bool = True


class ChatRequest(BaseModel):
    message: str
    # 兼容旧参数：use_local=True 表示本地，False 表示云端（新版本优先使用 provider_id）
    use_local: Optional[bool] = None
    session_id: Optional[str] = None
    kb_slug: Optional[str] = DEFAULT_KB_SLUG
    kb_slugs: Optional[List[str]] = None
    use_rag: bool = True
    # v0.6.0：按 Provider 选择模型
    provider_id: Optional[str] = None
    model: Optional[str] = None
    # v0.8.0：聊天附件（文本/压缩包/代码/文档等，先经 /api/chat/attachments 上传拿到引用）
    attachments: Optional[List[AttachmentRef]] = None
    # v2.0.0：Agent 模式（自动调用工具：联网搜索 / RAG / 自我维护工具与知识库）
    agent_mode: Optional[bool] = None      # None = 用服务端保存的设置
    enable_web: Optional[bool] = None      # 是否允许联网搜索工具
    max_steps: Optional[int] = None        # Agent 最大「模型→工具」轮数
    run_id: Optional[str] = None           # 可选的幂等生成标识，旧客户端行为不变
    last_event_id: int = 0
    resume: bool = False
    profile: Optional[str] = None
    # 输入框芯片：可同时挂 1~3 个方向；profile 是旧客户端兼容字段，两者取并集
    profiles: Optional[List[str]] = None
    developer_grant: Optional[str] = None


class ProviderRequest(BaseModel):
    id: Optional[str] = None
    name: str = ""
    kind: str = "cloud"          # local | cloud
    base_url: str = ""
    api_key: Optional[str] = None  # None/缺省=保留原值；空字符串=清除
    api_key_env: Optional[str] = None
    model: str = ""
    models: Optional[List[str]] = None  # 用户显式添加的模型列表（输入框只显示这些）
    is_default: bool = False


# ── 工具函数 ──

def _ollama_available() -> bool:
    """检查本地 Ollama 是否可连接（GET {base_url}/models，短超时）。"""
    import urllib.request
    local_provider = provider_config.get_local_provider()
    base_url = (local_provider or {}).get("base_url") or LOCAL_BASE_URL
    base_url = provider_config.local_base_url(base_url).rstrip('/')
    url = base_url.removesuffix('/v1') + '/api/version'
    with _HEALTH_LOCK:
        cached = _HEALTH_CACHE.get(url)
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1]
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1) as resp:
            result = resp.status == 200
    except Exception:
        result = False
    with _HEALTH_LOCK:
        _HEALTH_CACHE.clear()
        _HEALTH_CACHE[url] = (time.monotonic(), result)
    return result


_HEALTH_CACHE = {}
_HEALTH_LOCK = threading.Lock()


def _resolve_provider(
    provider_id: Optional[str],
    model: Optional[str],
    use_local: Optional[bool],
) -> Dict:
    """解析聊天请求使用的 Provider。优先级：provider_id > use_local（旧参数）> 默认。"""
    provider = None
    if provider_id:
        provider = provider_config.get_provider(provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"模型提供商不存在: {provider_id}")
    elif use_local is True:
        provider = provider_config.get_local_provider() or provider_config.get_default_provider()
    elif use_local is False:
        cloud = [p for p in provider_config.list_providers() if p["kind"] == "cloud"]
        provider = (
            provider_config.get_provider(cloud[0]["id"])
            if cloud else provider_config.get_default_provider()
        )
    else:
        provider = provider_config.get_default_provider()

    if not provider:
        raise HTTPException(
            status_code=503,
            detail="未配置任何模型提供商，请打开设置面板添加（或检查 data/providers.json）",
        )
    if model and model.strip():
        provider = {**provider, "model": model.strip()}
    return provider


def _account_response(provider, messages, response):
    tracker = provider.get('_usage_tracker')
    if tracker:
        tracker.next_step()
        try: accounted = tracker.add_provider_usage(response.usage)
        except (ValueError,TypeError): accounted = False
        if not accounted:
            message = response.choices[0].message
            tracker.add_estimate(json.dumps(messages,ensure_ascii=False),_normalize_delta_text(message.content)+_normalize_delta_text(getattr(message,'reasoning_content',None)))


def _create_stream(client, model, messages, max_tokens=None):
    kwargs = {'stream_options':{'include_usage':True}}
    # 不限长度（None）时干脆不传这个参数，避免个别服务商把 null 当非法请求
    if max_tokens: kwargs['max_tokens'] = max_tokens
    for attempt in range(3):
        try: return client.chat.completions.create(model=model,messages=messages,stream=True,**kwargs)
        except APIError as exc:
            msg = str(exc).lower()
            if attempt == 2 or getattr(exc,'status_code',None) not in {400,422}: raise
            if ('stream_options' in msg or 'include_usage' in msg) and 'stream_options' in kwargs:
                kwargs.pop('stream_options')
            elif ('max_tokens' in msg or 'max output' in msg) and 'max_tokens' in kwargs:
                kwargs.pop('max_tokens')
            else: raise
            logger.warning('普通流式请求兼容重试 model=%s 参数=%s',model,sorted(kwargs))


def _fallback_local(messages: List[Dict], warning: str, tracker=None):
    """云端失败时回退本地 Ollama。返回 (reply, was_fallback, error_msg)。"""
    local_provider = provider_config.get_local_provider()
    if local_provider and tracker: local_provider['_usage_tracker'] = tracker
    if not local_provider:
        return "", False, f"{warning}（且未配置本地模型可回退）"
    try:
        client = provider_config.build_client(local_provider)
        response = client.chat.completions.create(
            model=local_provider["model"],
            messages=messages,
        )
        _account_response(local_provider,messages,response)
        reply = response.choices[0].message.content
        return reply, True, warning
    except Exception as e2:
        return "", False, f"{warning}（本地回退也失败: {str(e2)[:100]}）"


def _fallback_local_stream(messages: List[Dict], warning: str, max_tokens: Optional[int] = None):
    """云端失败时回退本地 Ollama（流式）。返回 (stream, was_fallback, warning, provider)。"""
    local_provider = provider_config.get_local_provider()
    if not local_provider:
        raise HTTPException(status_code=503, detail=f"{warning}（且未配置本地模型可回退）")
    try:
        client = provider_config.build_client(local_provider)
        stream = _create_stream(client,local_provider['model'],messages,max_tokens)
        return stream, True, warning, local_provider
    except Exception as e2:
        raise HTTPException(status_code=503, detail=f"{warning}（本地回退也失败: {str(e2)[:100]}）")


# ── LLM 调用核心（带错误处理和回退） ──

def _call_llm_non_stream(provider: Dict, messages: List[Dict], max_tokens: Optional[int] = None):
    """调用 LLM（非流式），云端失败时自动回退到本地。"""
    client = provider_config.build_client(provider)
    model = provider["model"]
    try:
        token_kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        response = client.chat.completions.create(model=model, messages=messages, **token_kwargs)
        _account_response(provider,messages,response)
        return response.choices[0].message.content, False, ""
    except APIConnectionError as e:
        logger.error("LLM 连接失败(%s): %s", provider["id"], e)
        if provider.get("kind") == "local":
            return "", False, f"无法连接本地模型（Ollama）。请确认 Ollama 已启动（ollama serve）并已拉取模型：{model}"
        return _fallback_local(messages, f"云端连接失败，已自动回退到本地模型。({str(e)[:80]})",provider.get('_usage_tracker'))
    except APIError as e:
        logger.error("LLM API 错误(%s): %s", provider["id"], e)
        error_msg = str(e)
        if "403" in error_msg or "quota" in error_msg.lower() or "insufficient" in error_msg.lower():
            if provider.get("kind") != "local":
                logger.warning("云端配额/鉴权失败，回退到本地模型...")
                return _fallback_local(messages, "云端配额耗尽或鉴权失败，已自动回退到本地模型。",provider.get('_usage_tracker'))
            return "", False, f"API 错误：{error_msg}"
        return "", False, f"API 错误：{error_msg}"
    except Exception as e:
        logger.error("LLM 调用异常(%s): %s", provider["id"], e)
        if provider.get("kind") != "local":
            logger.warning("云端调用失败，回退到本地模型...")
            return _fallback_local(messages, f"云端调用失败，已自动回退到本地模型。({str(e)[:80]})",provider.get('_usage_tracker'))
        return "", False, f"调用失败：{str(e)}"


def _call_llm_stream(provider: Dict, messages: List[Dict], max_tokens: Optional[int] = None):
    """调用 LLM（流式），云端失败时自动回退到本地。返回 (stream, was_fallback, warning, actual_provider)。"""
    client = provider_config.build_client(provider)
    model = provider["model"]
    try:
        stream = _create_stream(client, model, messages, max_tokens)
        return stream, False, "", provider
    except APIError as e:
        error_msg = str(e)
        # 用户可能把「单次回复上限」设得比模型允许的输出上限还大，服务端会直接报错；
        # 这时去掉 max_tokens 再试一次，别让一个设置项把对话搞挂。
        if max_tokens and ("max_tokens" in error_msg.lower() or "max output" in error_msg.lower()):
            logger.warning("模型不接受 max_tokens=%s，去掉该参数重试", max_tokens)
            try:
                stream = client.chat.completions.create(model=model, messages=messages, stream=True)
                return stream, False, "", provider
            except Exception as retry_err:  # 还是不行就继续走下面的回退逻辑
                logger.warning("去掉 max_tokens 重试仍失败: %s", str(retry_err)[:150])
        if provider.get("kind") != "local" and (
            "403" in error_msg or "quota" in error_msg.lower() or "insufficient" in error_msg.lower()
        ):
            logger.warning("云端配额/鉴权失败，回退到本地模型（流式）...")
            return _fallback_local_stream(messages, "云端配额耗尽或鉴权失败，已自动回退到本地模型。", max_tokens)
        raise HTTPException(status_code=502, detail=f"API 错误：{error_msg}")
    except APIConnectionError as e:
        if provider.get("kind") == "local":
            raise HTTPException(
                status_code=502,
                detail=f"无法连接本地模型（Ollama）。请确认 Ollama 已启动（ollama serve）并已拉取模型：{model}",
            )
        logger.warning("云端连接失败，回退到本地模型（流式）...")
        return _fallback_local_stream(messages, "云端连接失败，已自动回退到本地模型。", max_tokens)
    except Exception as e:
        if provider.get("kind") != "local":
            logger.warning("云端调用异常，回退到本地模型（流式）...")
            return _fallback_local_stream(messages, "云端调用失败，已自动回退到本地模型。", max_tokens)
        raise HTTPException(status_code=500, detail=f"调用失败：{str(e)}")


# ── 系统状态 API ──

@router.get("/api/config")
def api_config():
    """返回前端所需的运行配置（提供商列表、默认值、embedding 后端、支持的文件类型等）。"""
    providers = provider_config.list_providers()
    default_provider = provider_config.get_default_provider()
    local_provider = provider_config.get_local_provider()
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "assistant_greeting": ASSISTANT_GREETING,
        "default_provider_id": default_provider["id"] if default_provider else None,
        "providers": providers,
        "local_base_url": (local_provider or {}).get("base_url", ""),
        "cloud_configured": any(
            p["kind"] == "cloud" and p["has_api_key"] for p in providers
        ),
        "default_local": bool(default_provider and default_provider["kind"] == "local"),
        "embedding_backend": get_embedding_backend(),
        "supported_extensions": sorted(SUPPORTED_FILE_EXTENSIONS),
        "default_kb_slug": DEFAULT_KB_SLUG,
    }


@router.get("/api/health")
def api_health():
    """健康检查：服务状态 + 本地 Ollama / 云端可用性。"""
    local_ok = _ollama_available()
    providers = provider_config.list_providers()
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "ollama_available": local_ok,
        "provider_count": len(providers),
        "cloud_configured": any(
            p["kind"] == "cloud" and p["has_api_key"] for p in providers
        ),
        "embedding_backend": get_embedding_backend(),
    }


# ── 模型提供商管理 API ──

@router.get("/api/providers")
def api_list_providers():
    return {"providers": provider_config.list_providers()}


@router.get("/api/provider-presets")
def api_provider_presets():
    return {"presets": provider_presets.PRESETS}


@router.get("/api/appearance-presets")
def api_appearance_presets():
    return appearance_presets.catalog()


@router.post("/api/appearance-presets/backgrounds")
async def api_upload_background(file: UploadFile = File(...)):
    """背景与头像共用图片收藏，切换预设不会丢上传。"""
    try:
        return appearance_presets.save_upload("backgrounds", file.filename, await file.read(3 * 1024 * 1024 + 1))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/appearance-presets/{group}/{identity}/image")
def api_appearance_image(group: str, identity: str):
    """本机预设图片使用 API 鉴权，与上传头像遵循同一访问规则。"""
    try:
        return FileResponse(appearance_presets.asset_path(group, identity))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/avatar/{role}/preset")
def api_avatar_preset(role: str, req: Dict):
    if role not in ('bot','user'):raise HTTPException(status_code=400,detail='头像角色无效')
    identity=req.get('id')
    if not identity:
        uploaded = [p for p in appearance_presets.catalog()[role] if p.get("origin") == "upload"]
        identity = uploaded[0]["id"] if uploaded else None
    if identity:
        try:appearance_presets.avatar_path(role,identity)
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    _set_avatar_preset(role,identity)
    return {"ok":True,"id":identity,"url":f"/api/avatar/{role}?t={time.time()}" if _avatar_exists(role) else ''}


@router.get("/api/ollama/discover")
def api_ollama_discover(base_url: str = "http://localhost:11434/v1"):
    try:
        return provider_presets.discover_ollama(base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/jobs")
def api_jobs():
    return {"jobs": [{k: v for k,v in row.items() if k != "events"} for row in jobs.list_jobs()]}


@router.post("/api/jobs")
def api_job_start(req: Dict):
    try:
        request = ChatRequest(**req)
        mode, enabled, steps = _resolve_agent_options(request)
        return jobs.start(req.get("message"), req.get("provider_id"), req.get("model"), steps,
                          enabled_tools=enabled if mode else [], kb_slugs=_rag_libraries(request), limits=_runtime_limits())
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc))


@router.get("/api/jobs/{identity}")
def api_job_output(identity: str):
    try:return jobs.output(identity)
    except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))


@router.post("/api/jobs/{identity}/kill")
def api_job_kill(identity: str):
    return jobs.kill(identity)


@router.delete('/api/jobs/{identity}')
def api_job_delete(identity: str):
    try: return jobs.delete(identity)
    except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc))


@router.get("/api/mcp")
def api_mcp():
    return {"servers":mcp_client.catalog()}


@router.get("/api/workspace/changes")
def api_workspace_changes():
    return {"changes":workspace_history.list_changes()}


@router.get("/api/workspace/changes/{identity}")
def api_workspace_diff(identity: str):
    try:return workspace_history.diff(identity)
    except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))


@router.post("/api/workspace/changes/{identity}/rollback")
def api_workspace_rollback(identity: str):
    try:return workspace_history.rollback(identity)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))


@router.post("/api/mcp/{name}/connect")
def api_mcp_connect(name: str):
    try:return {"ok":True,"tools":mcp_client.connect(name)}
    except Exception as exc:raise HTTPException(status_code=400,detail=str(exc)[:300])


@router.post("/api/mcp/{name}/disconnect")
def api_mcp_disconnect(name: str):
    mcp_client.disconnect(name)
    return {"ok":True}


@router.post("/api/providers")
def api_save_provider(req: ProviderRequest):
    try:
        data = req.model_dump(exclude_unset=True)
        return provider_config.upsert_provider(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/providers/{provider_id}")
def api_delete_provider(provider_id: str):
    try:
        ok = provider_config.delete_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="提供商不存在")
    return {"ok": True, "provider_id": provider_id}


@router.post("/api/providers/{provider_id}/default")
def api_set_default_provider(provider_id: str):
    ok = provider_config.set_default_provider(provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提供商不存在")
    return {"ok": True, "provider_id": provider_id}


@router.post("/api/providers/{provider_id}/test")
def api_test_provider(provider_id: str):
    try:
        return provider_config.test_provider(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/providers/{provider_id}/models")
def api_provider_models(provider_id: str):
    try:
        return provider_config.list_models(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 聊天附件 API ──

@router.post("/api/chat/attachments")
async def api_upload_attachments(files: List[UploadFile] = File(...)):
    """上传聊天附件（文本/压缩包/代码/Office 文档/可执行文件元信息等），返回可引用的文件记录。"""
    try:
        records = await file_attachments.save_upload(files)
        return {"files": records}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 头像与界面状态 API（v0.9.2：服务端持久化，重启不丢） ──

# ── Agent 能力查询 API（v2.0.0） ──

@router.get("/api/agent/tools")
def api_agent_tools():
    """列出 Agent 当前可用的工具（内置 + 自定义），供设置面板展示。"""
    return {"tools": agent_tools.list_tools()}


@router.get("/api/agent/changes")
def api_agent_changes(limit: int = 50):
    """
    返回 Agent 的自我修改记录（data/logs/changes.log 的最后 N 条）。
    用户可据此审计「它到底改了什么」。
    """
    log_file = agent_tools.CHANGES_LOG
    if not log_file.exists():
        return {"changes": []}
    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        items = []
        for line in lines[-max(1, min(limit, 500)):]:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
        return {"changes": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取变更日志失败: {e}")


@router.get("/api/agent/memory")
def api_agent_memory():
    """返回全部长期记忆（倒序，新的在前），供设置面板「记忆」标签页展示。"""
    items = memory_list()
    items.sort(key=lambda it: it.get("ts", 0), reverse=True)
    return {"items": items}


@router.post("/api/agent/memory")
def api_agent_memory_add(req: Optional[Dict] = None):
    """手动添加一条长期记忆（用户在界面输入，与 Agent 的 remember 共用存储）。"""
    content = str((req or {}).get("content", "") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆内容不能为空。")
    result = memory_add(content, str((req or {}).get("tags", "") or ""), source="user")
    return result


@router.delete("/api/agent/memory/{mem_id}")
def api_agent_memory_delete(mem_id: str):
    """删除一条长期记忆。"""
    result = memory_delete(mem_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "删除失败"))
    return result


@router.delete("/api/agent/memory")
def api_agent_memory_clear():
    """清空全部长期记忆。"""
    return {"ok": True, "removed": memory_clear()}


@router.post("/api/agent/approval")
def api_agent_approval(req: ApprovalRequest):
    """
    高危工具审批回调：前端弹窗点「允许 / 拒绝」后把 request_id 发到这里，
    唤醒 runner 里等待中的线程。请求已超时被清理时返回 ok=False（前端静默即可）。
    """
    entry = agent_runner.APPROVAL_REGISTRY.get(req.request_id)
    if not entry:
        return {"ok": False, "error": "审批请求不存在或已超时"}
    entry["approved"] = req.approve
    entry["event"].set()
    return {"ok": True}


@router.get("/api/agent/rag/files")
def api_agent_rag_files(kb_slug: str = DEFAULT_KB_SLUG):
    """列出某个知识库中已持久化的 RAG 文件（Agent 可增删改这些文件）。"""
    return {"kb_slug": kb_slug, "files": list_kb_files(kb_slug)}


@router.delete("/api/agent/rag/notes/{kb_slug}/{filename}")
def api_delete_learning(kb_slug: str, filename: str):
    """只撤回自动笔记，不扩大到任意文件。"""
    import re
    if not re.fullmatch(r"note-[0-9a-f]{24}\.md", filename):
        raise HTTPException(status_code=400, detail="这里只能撤回经验笔记")
    try:
        validate_kb_slug(kb_slug)
        result = delete_kb_file(kb_slug, filename)
        agent_tools.log_change("rag_note_remove", {"kb_slug": kb_slug, "filename": filename})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/agent/search_test")
def api_agent_search_test(req: Optional[SearchTestRequest] = None):
    """
    测试联网搜索后端是否可用（设置面板的「测试搜索」按钮调用）。
    固定查询一句话，返回用了哪个引擎、命中多少条，方便用户确认配置是否生效。
    engine 可传 auto / tavily / bing，用于分别验证 Tavily 与免 Key 爬虫
    （百度会返回安全验证页，已从可用后端里移除）。
    """
    from agent.tools import web_search
    engine = (req.engine if req else None) or "auto"
    result = web_search.search("OpenAI 官方文档", max_results=3, engine=engine)
    return {
        "ok": result["ok"],
        "engine": result["engine"],
        "count": len(result.get("results", [])),
        "results": result.get("results", [])[:3],
        "error": result.get("error", ""),
    }

# ── 上下文相关设置的范围（超出就会把程序或钱包玩坏，所以统一在这里夹住）──
# 说明：范围不是拍脑袋定的 ——
#   context_budget  下限 4K（再小没法对话）／上限 1M（对齐长上下文模型，如 Gemini 1M）
#   min_keep_turns  1~20 轮：保底记忆，太多就失去"裁剪"的意义
#   tool_output_limit 500~50000 字符：一次搜索/抓页的合理上限
#   reply_max_tokens  256~32768：主流模型的单次输出上限
_CONTEXT_RANGES = {
    "context_budget": (context_manager.MIN_CONTEXT_BUDGET, context_manager.MAX_CONTEXT_BUDGET),
    "min_keep_turns": (1, context_manager.MAX_MIN_KEEP_TURNS),
    "tool_output_limit": (500, 50_000),
    "reply_max_tokens": (256, 32_768),
}
# 开关类设置（存进 ui_state 时必须是真正的 bool）
_CONTEXT_BOOL_KEYS = ("context_compress", "unlimited_turns", "reply_unlimited")


def _clamp_context_settings(raw: Dict) -> Dict:
    """
    把上下文相关设置夹到合法范围，返回可直接合并进 ui_state 的字典。

    只处理 raw 里**出现过**的键，没传的键保持原值（与"留空=不改动"的既有约定一致）。
    注意 bool 是 int 的子类，必须先排除，否则 True/False 会被当成 1/0 写进数值字段。
    """
    result: Dict = {}
    for key, (lo, hi) in _CONTEXT_RANGES.items():
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        result[key] = max(lo, min(int(value), hi))
    for key in _CONTEXT_BOOL_KEYS:
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    return result


@router.get("/api/ui/state")
def api_ui_state():
    """返回界面状态：上次使用的知识库 / RAG 开关 / 头像是否存在。"""
    state = {
        "kb_slug": DEFAULT_KB_SLUG,
        "use_rag": True,
        "avatar_bot": _avatar_exists("bot"),
        "avatar_user": _avatar_exists("user"),
        # Agent 默认设置：默认开启 Agent 模式与联网能力，工具轮数默认 4（上限见 MAX_STEPS_LIMIT）
        "agent_mode": True,
        "enable_web": True,
        "max_steps": agent_runner.DEFAULT_MAX_STEPS,
        # 搜索后端配置状态（Tavily Key 不回传明文，只回传是否已配置）
        "tavily_configured": False,
        "firecrawl_configured": False,
        "tavily_enabled": True,
        # 上下文管理默认值（可在设置里调整并持久化；范围见 _CONTEXT_RANGES）
        # 默认偏"够用"：200K 窗口 / 保底 10 轮 / 单次 8192；两个"无限"开关默认关闭
        "context_budget": context_manager.DEFAULT_CONTEXT_BUDGET,
        "min_keep_turns": context_manager.DEFAULT_MIN_KEEP_TURNS,
        "unlimited_turns": False,
        "context_compress": True,
        "tool_output_limit": 3000,
        "reply_max_tokens": agent_runner.DEFAULT_REPLY_MAX_TOKENS,
        "reply_unlimited": False,
    }
    try:
        if UI_STATE_FILE.exists():
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("kb_slug"), str) and data["kb_slug"]:
                state["kb_slug"] = data["kb_slug"]
            if isinstance(data.get("use_rag"), bool):
                state["use_rag"] = data["use_rag"]
            if isinstance(data.get("agent_mode"), bool):
                state["agent_mode"] = data["agent_mode"]
            if isinstance(data.get("enable_web"), bool):
                state["enable_web"] = data["enable_web"]
            if isinstance(data.get("max_steps"), int):
                state["max_steps"] = max(1, min(data["max_steps"], agent_runner.MAX_STEPS_LIMIT))
            state["tavily_configured"] = bool(str(data.get("tavily_api_key", "") or "").strip())
            state["firecrawl_configured"] = bool(str(data.get("firecrawl_api_key", "") or "").strip())
            # 读取用户保存的 Agent 运行参数（带范围约束，避免手改 JSON 后越界）
            if isinstance(data.get("tavily_enabled"), bool):
                state["tavily_enabled"] = data["tavily_enabled"]
            state.update(_clamp_context_settings(data))
    except Exception as e:
        logger.warning("读取界面状态失败: %s", e)
    settings = _ui_settings()
    state["kb_slugs"] = selected_kbs({**settings, "use_rag": True})
    state["avatar_bot_preset"] = settings.get("avatar_bot_preset", "")
    state["avatar_user_preset"] = settings.get("avatar_user_preset", "")
    state["rag_auto_learn"] = settings.get("rag_auto_learn", True)
    state["kb_slug"] = state["kb_slug"] if state["kb_slug"] in state["kb_slugs"] else next(iter(state["kb_slugs"]), DEFAULT_KB_SLUG)
    return state


@router.post("/api/ui/state")
@_locked_ui
def api_save_ui_state(req: UiStateRequest):
    """保存界面状态（当前知识库 / RAG 开关）。"""
    UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=409, detail="设置文件暂时读不了。先修复文件，别覆盖原配置。")
    if req.kb_slug is not None:
        try:
            data["kb_slug"] = validate_kb_slug(req.kb_slug or DEFAULT_KB_SLUG)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.kb_slugs is not None:
        try:
            data["kb_slugs"] = list(dict.fromkeys(validate_kb_slug(s) for s in req.kb_slugs))[:20]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.rag_auto_learn is not None:
        data["rag_auto_learn"] = req.rag_auto_learn
    if req.use_rag is not None:
        data["use_rag"] = bool(req.use_rag)
    if req.agent_mode is not None:
        data["agent_mode"] = bool(req.agent_mode)
    if req.enable_web is not None:
        data["enable_web"] = bool(req.enable_web)
    if req.max_steps is not None:
        data["max_steps"] = max(1, min(int(req.max_steps), agent_runner.MAX_STEPS_LIMIT))
    # 搜索后端：传空字符串=清除，传 None/不传=保留（与 Provider 的 API Key 规则一致）
    if req.tavily_api_key is not None:
        data["tavily_api_key"] = req.tavily_api_key.strip()
    if req.firecrawl_api_key is not None:
        data["firecrawl_api_key"] = req.firecrawl_api_key.strip()
    if req.tavily_enabled is not None:
        data["tavily_enabled"] = bool(req.tavily_enabled)
    # 上下文相关的数值统一走范围校验收口（手改 JSON 越界也不会把程序玩坏）
    data.update(_clamp_context_settings({
        k: getattr(req, k) for k in
        ("context_budget", "min_keep_turns", "unlimited_turns", "context_compress",
         "tool_output_limit", "reply_max_tokens", "reply_unlimited")
        if getattr(req, k) is not None
    }))
    persistence.save(UI_STATE_FILE.parent, UI_STATE_FILE.stem, data)
    return {"ok": True}


@router.post("/api/avatar/{role}")
async def api_upload_avatar(role: str, file: UploadFile = File(...)):
    """上传头像（bot=银狼 / user=我的），存 data/images/，重启不丢。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in AVATAR_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/jpeg/gif/webp 图片")
    data = await file.read(MAX_AVATAR_SIZE + 1)
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="头像请控制在 2MB 以内")
    try:
        saved = appearance_presets.save_upload(role, file.filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_avatar_preset(role, saved["id"])
    logger.info("头像已更新: %s", role)
    return {"ok": True, "id": saved["id"], "url": f"/api/avatar/{role}?t={int(time.time())}"}


@router.delete("/api/avatar/{role}")
def api_delete_avatar(role: str):
    """删除头像（恢复默认）。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    _set_avatar_preset(role, 'default')
    return {"ok": True}


@router.get("/api/avatar/{role}")
def api_get_avatar(role: str):
    """读取头像文件。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    identity = _ui_settings().get('avatar_'+role+'_preset')
    if identity == 'default':
        raise HTTPException(status_code=404, detail="正在使用默认头像")
    if identity:
        try:
            return FileResponse(str(appearance_presets.avatar_path(role, identity)))
        except ValueError:
            pass
    files = _avatar_files(role)
    if not files:
        raise HTTPException(status_code=404, detail="头像不存在")
    path = files[0]
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return FileResponse(str(path), media_type=media_type)


# ── 根路由（Vue 前端） ──

@router.get("/")
def root():
    """返回 Vue 前端入口页面。"""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;background:#120822;color:#E8DFF5;"
        "display:flex;align-items:center;justify-content:center;height:100vh;'>"
        "<div><h2>⚠️ 前端文件缺失</h2><p>未找到 frontend/index.html，请检查项目完整性。</p></div>"
        "</body></html>",
        status_code=503,
    )


# ── 会话管理 API ──

@router.post("/api/sessions")
def api_create_session(req: Optional[CreateSessionRequest] = None):
    title = req.title if req else None
    return create_session(title=title)


@router.get("/api/sessions")
def api_list_sessions():
    return list_sessions()


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/api/sessions/{session_id}/plan")
def api_session_plan(session_id: str):
    if not get_session(session_id): raise HTTPException(status_code=404,detail="会话不存在")
    return {"steps":plans.load(session_id)}


@router.get("/api/sessions/{session_id}/export")
def api_export_session(session_id: str):
    """
    导出会话为 Markdown 文件（下载）。按消息顺序拼成 ## 会话记录，
    user/assistant 角色清晰标注，机器人回答含 meta 里的 model 信息。
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    lines = [
        f"# {session.get('title', '会话记录')}",
        "",
        f"> 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]
    for m in session.get("messages", []):
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role == "user":
            lines.append("## 委托人\n")
            lines.append(content + "\n")
        elif role == "assistant":
            model = ""
            if m.get("meta") and isinstance(m.get("meta"), dict):
                model = m["meta"].get("model", "")
            head = "## 银狼" + (f"（{model}）" if model else "")
            lines.append(head + "\n")
            lines.append(content + "\n")
        # 其它 role（tool 等）不导出，避免污染可读性

    filename = f"silverwolf_session_{session_id[:8]}.md"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    from fastapi.responses import Response
    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )


@router.put("/api/sessions/{session_id}")
def api_rename_session(session_id: str, req: RenameSessionRequest):
    success = rename_session(session_id, req.title)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "title": req.title}


@router.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    success = delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    persistence.location(plans.PLAN_DIR,session_id).unlink(missing_ok=True)
    return {"ok": True}


# ── 兼容旧版路由 ──

@router.post("/sessions")
async def compat_create_session(req: Optional[CreateSessionRequest] = None):
    return await run_in_threadpool(api_create_session, req)


@router.get("/sessions")
async def compat_list_sessions():
    return await run_in_threadpool(api_list_sessions)


@router.get("/sessions/{session_id}")
async def compat_get_session(session_id: str):
    return await run_in_threadpool(api_get_session, session_id)


@router.put("/sessions/{session_id}")
async def compat_rename_session(session_id: str, req: RenameSessionRequest):
    return await run_in_threadpool(api_rename_session, session_id, req)


@router.delete("/sessions/{session_id}")
async def compat_delete_session(session_id: str):
    return await run_in_threadpool(api_delete_session, session_id)


# ── 聊天 API ──

def _ui_settings() -> Dict:
    """读取 data/ui_state.json（界面状态：知识库 / RAG / Agent 设置）。"""
    if not UI_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # 损坏时设置会回退默认值，出日志方便排障
        logger.warning("读取界面状态失败: %s", e)
        return {}


def _resolve_agent_options(chat: ChatRequest):
    """
    解析本次请求的 Agent 选项。

    优先级：请求参数 > 界面保存的设置 > 默认值（默认开启 Agent 与联网）。
    返回 (agent_mode, enabled_tools, max_steps)；enabled_tools=None 表示启用全部工具。
    """
    settings = _ui_settings()
    agent_mode = chat.agent_mode if chat.agent_mode is not None else bool(settings.get("agent_mode", True))
    enable_web = chat.enable_web if chat.enable_web is not None else bool(settings.get("enable_web", True))
    max_steps = chat.max_steps or settings.get("max_steps") or agent_runner.DEFAULT_MAX_STEPS

    excluded = set()
    try:
        wanted_profiles = normalize_profiles(chat.profile, chat.profiles)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if wanted_profiles:
        allowed_categories = {'core', 'agent', *wanted_profiles}
        excluded.update(t['name'] for t in agent_tools.list_tools() if t.get('category') not in allowed_categories)
    if chat.developer_grant:
        from agent import developer
        try:developer.require(chat.developer_grant)
        except PermissionError as exc:raise HTTPException(403,str(exc))
    else:excluded.update(t['name'] for t in agent_tools.list_tools() if t['name'].startswith('project_'))
    excluded.update(settings.get('disabled_tools',[]))
    if not enable_web:
        excluded.update(t['name'] for t in agent_tools.list_tools() if 'network' in t.get('risks',[]))
    if not _rag_libraries(chat):
        excluded.update(t["name"] for t in agent_tools.list_tools() if t["name"].startswith("rag_"))
    enabled_tools = [t["name"] for t in agent_tools.list_tools() if t["name"] not in excluded] if excluded else None
    return agent_mode, enabled_tools, max(1, min(int(max_steps), agent_runner.MAX_STEPS_LIMIT))


def _runtime_limits() -> Dict[str, Optional[int]]:
    """
    读取「上下文管理」相关的运行参数（用户在设置里可调，存 ui_state.json）。

    - context_budget：上下文窗口 token 预算（含 system/RAG/历史/当前提问）；
    - min_keep_turns：保底保留的最近轮数（预算再紧也不丢）；
    - unlimited_turns：无限轮数（True 时裁剪直接跳过，一条历史都不丢）；
    - tool_output_limit：工具结果进入上下文的字符上限（截断最省 token）；
    - reply_max_tokens：单次回复 token 上限；开了「无限长度」时返回 None（调用方不再传该参数）。
    """
    settings = _ui_settings()
    limits = {
        "context_budget": context_manager.DEFAULT_CONTEXT_BUDGET,
        "min_keep_turns": context_manager.DEFAULT_MIN_KEEP_TURNS,
        "unlimited_turns": False,
        "tool_output_limit": 3000,
        "reply_max_tokens": agent_runner.DEFAULT_REPLY_MAX_TOKENS,
    }
    limits.update(_clamp_context_settings(settings))
    if bool(settings.get("reply_unlimited")):
        # None = 不限长度：调用方看到 None 就不会传 max_tokens
        limits["reply_max_tokens"] = None
    return limits


def _apply_context_management(
    session_id: str,
    messages: List[Dict],
    provider: Dict,
) -> List[Dict]:
    """
    对话开始前的上下文整理（核心省钱逻辑）。

    步骤：
      1. 若会话已有历史摘要，把它作为一条 system 消息插在开场之后；
      2. 按预算裁剪历史（裁剪永远执行，纯计算零成本）；
      3. 如果裁掉了内容且用户开启了「上下文压缩」，把这些内容压成摘要存回会话，
         下次对话再带回 —— 既不丢关键信息，又大幅降低 token。
      4. 压缩失败不影响对话（只丢历史），并记录日志。
    """
    settings = _ui_settings()
    limits = _runtime_limits()
    budget = limits["context_budget"]
    min_keep_turns = limits["min_keep_turns"]
    unlimited = bool(limits["unlimited_turns"])
    compress = bool(settings.get("context_compress", True))

    previous_summary = get_session_summary(session_id)
    summary_msg = context_manager.build_summary_message(previous_summary)
    keep_head = 2
    if summary_msg:
        messages.insert(2, summary_msg)
        keep_head = 3          # 摘要必须保留，不能被裁剪掉

    kept, dropped = context_manager.trim_messages(
        messages, budget, keep_head=keep_head,
        min_keep_turns=min_keep_turns, unlimited=unlimited,
    )
    if dropped:
        logger.info(
            "上下文裁剪：原始 %d 条 → 保留 %d 条（丢弃 %d 条，窗口 %d tokens，保底 %d 轮）",
            len(messages), len(kept), len(dropped), budget, min_keep_turns,
        )
    elif unlimited:
        logger.info("无限轮数模式：保留全部 %d 条消息，本轮不裁剪", len(kept))
    if dropped and compress:
        new_summary = context_manager.compress_history(dropped, provider, previous_summary)
        if new_summary and new_summary != previous_summary:
            set_session_summary(session_id, new_summary)
            # 用更新后的摘要替换旧摘要（保持位置一致）
            kept = [
                m for m in kept
                if not (m.get("role") == "system"
                        and str(m.get("content", "")).startswith("【早前对话摘要"))
            ]
            refreshed = context_manager.build_summary_message(new_summary)
            if refreshed:
                kept.insert(min(2, len(kept) - 1), refreshed)
    return kept

def _rag_libraries(chat: ChatRequest):
    """请求范围必须是设置中已开启库的子集；旧客户端仍兼容单库参数。"""
    enabled = selected_kbs(_ui_settings())
    if not chat.use_rag:
        return []
    return enabled if chat.kb_slugs is None else [s for s in chat.kb_slugs if s in enabled]


def _prepare_chat_context(chat: ChatRequest, use_rag_override: Optional[bool] = None):
    """公共逻辑：创建/获取会话、构建消息、执行 RAG 检索。返回 (session_id, messages, rag_info)。"""
    session_id = chat.session_id
    if session_id and not get_session(session_id):
        raise HTTPException(status_code=404, detail="会话已经不在了。开个新会话再继续。")
    if not session_id:
        session_id = str(uuid.uuid4())
        create_session(title=generate_title(chat.message), session_id=session_id)

    messages = build_messages(
        session_id=session_id,
        user_message=chat.message,
        system_prompt=SYSTEM_PROMPT,
        assistant_greeting=ASSISTANT_GREETING,
    )

    # v0.8.0：聊天附件注入到用户消息前
    if chat.attachments:
        try:
            refs = [a.model_dump() for a in chat.attachments]
            resolved = file_attachments.resolve_attachment_refs(refs)
            try:
                ctx = file_attachments.build_attachments_context(resolved)
            finally:
                file_attachments.discard_parsed_attachments(resolved)
            if ctx:
                messages[-1] = {
                    **messages[-1],
                    "content": ctx + "\n\n" + messages[-1]["content"],
                }
            # 解析文字已进入本轮消息，临时文件在 finally 里随解析一并清理
            logger.info("聊天附件已读入上下文: %d 个", len(resolved))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    use_rag = chat.use_rag if use_rag_override is None else use_rag_override
    rag_info = {"has_context": False, "sources": []}
    libraries = _rag_libraries(chat)
    if libraries:
        messages.insert(2, {"role": "system", "content": "【当前开启的知识库】" + ", ".join(libraries) + "。只检索和维护这些库；技术笔记的自动积累由回答结束后的系统完成。"})
    if use_rag and should_use_rag(chat.message, libraries):
        try:
            rag_result = build_rag_prompt(chat.message, libraries)
            rag_info = {
                "has_context": rag_result["has_context"],
                "sources": rag_result["sources"],
            }
            if rag_result["has_context"]:
                messages.insert(2, {"role": "system", "content": rag_result["rag_prompt"]})
                logger.info("RAG 增强已启用，来源: %s", rag_result["sources"])
        except Exception as e:
            logger.warning("RAG 检索失败，跳过: %s", e)

    # v2.3.0：长期记忆自动注入（跨会话）。按当前提问匹配相关记忆并拼进 system，
    # 这样普通对话与 Agent 对话都能「想起以前」——Agent 模式仍可主动调 recall/remember。
    try:
        relevant = memory_search(chat.message, limit=3)
        if relevant:
            mem_lines = ["【长期记忆 · 跨会话】（你以前记住的、与当前话题相关的信息，可放心引用）："]
            mem_lines += [f"- {it.get('content', '')}" for it in relevant]
            messages.insert(2, {"role": "system", "content": "\n".join(mem_lines)})
            logger.info("长期记忆注入 %d 条", len(relevant))
    except Exception as e:
        logger.debug("长期记忆注入失败（非致命）: %s", e)

    return session_id, messages, rag_info


def _chat_sync(chat: ChatRequest):
    """
    非流式聊天接口。

    v2.0.0 起默认走 **Agent 模式**（agent/runner.py）：模型可以自动调用工具
    （联网搜索 / RAG 检索 / 读写知识库文件 / 增删改自定义工具）。
    关掉 Agent 模式时退回原来的「单轮对话 + 自动回退」逻辑。
    """
    agent_mode, enabled_tools, max_steps = _resolve_agent_options(chat)
    # Agent 模式下 RAG 由 rag_search 工具按需触发，避免再自动注入一遍上下文
    session_id, messages, rag_info = _prepare_chat_context(
        chat, use_rag_override=(False if agent_mode else None)
    )
    provider = _resolve_provider(chat.provider_id, chat.model, chat.use_local)
    from middleware.token_usage import UsageTracker
    try:
        wanted_profiles = normalize_profiles(chat.profile, chat.profiles)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    provider = {**provider,'_usage_tracker':UsageTracker(),'_dev_grant':chat.developer_grant,'_profiles':wanted_profiles}
    # 上下文管理：按预算裁剪历史 + 可选压缩（详细原理见 middleware/context.py）
    messages = _apply_context_management(session_id, messages, provider)
    limits = _runtime_limits()

    # v3.3.1：用户消息与流式链路一致先落库——失败只丢回答，不留空壳会话丢提问
    attachment_context = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    save_message_with_meta(session_id, "user", chat.message, {"attachment_context": attachment_context} if chat.attachments else None)

    meta: Dict = {}
    was_fallback = False
    error_msg = ""
    if agent_mode:
        try:
            reply, meta = agent_runner.run_agent(
                {**provider, "_session_id": session_id, "_kb_slugs": _rag_libraries(chat)}, copy.deepcopy(messages), enabled_tools, max_steps,
                reply_max_tokens=limits["reply_max_tokens"],
                tool_output_limit=limits["tool_output_limit"],
            )
            if meta.get("error"):
                # runner 用终结 meta 优雅报告失败；沿用路由层已有的普通对话/本地回退。
                raise RuntimeError(meta["error"])
        except Exception as e:
            logger.warning("Agent 模式执行失败，降级为普通对话: %s", e)
            agent_mode = False
    if not agent_mode:
        reply, was_fallback, error_msg = _call_llm_non_stream(
            provider, messages, max_tokens=limits["reply_max_tokens"]
        )

    if error_msg and not reply:
        raise HTTPException(status_code=502, detail=error_msg)
    if was_fallback:
        local=provider_config.get_local_provider()
        if local:provider={**local,'_usage_tracker':provider['_usage_tracker']}
    if reply and meta.get('error'):
        meta['agent_warning']=meta.pop('error')

    if not was_fallback and not meta.get("error"):
        meta["knowledge_updates"] = learn_from_exchange(chat.message, reply, provider, _rag_libraries(chat))
    meta['usage'] = provider['_usage_tracker'].summary()
    save_message_with_meta(session_id, "assistant", reply, meta or None)
    from agent.tools._policy import apply_hook
    try:apply_hook('after_response','chat',True)
    except Exception:logger.exception('回答后守卫记录失败')

    result = {
        "reply": reply,
        "session_id": session_id,
        "provider_id": provider["id"],
        "model": provider["model"],
        "agent_mode": agent_mode,
        "rag_used": rag_info["has_context"],
        "rag_sources": rag_info["sources"],
    }
    result.update({k: v for k, v in meta.items() if k in ("usage", "tools_used", "web_searches", "rag_hits", "knowledge_updates")})
    if was_fallback:
        result["fallback_warning"] = error_msg

    return result


@router.post("/api/chat")
async def chat(request: ChatRequest):
    return await run_in_threadpool(_chat_sync, request)


@router.post("/Silver_Wolf")
async def compat_chat(request: ChatRequest):
    """兼容旧版路由。"""
    return await chat(request)


@router.post("/api/chat/stream")
async def chat_stream(chat: ChatRequest):
    return await chat_runtime.stream_response(chat, sys.modules[__name__])


@router.post("/Silver_Wolf/stream")
async def compat_chat_stream(chat: ChatRequest):
    """兼容旧版路由。"""
    return await chat_stream(chat)


# ── 挂载知识库路由（作为子路由） ──
router.include_router(kb_router)
