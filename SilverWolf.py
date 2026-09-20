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
import json
import logging
import mimetypes
import os
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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
)
from file_history_store import (
    init_db,
    create_session,
    list_sessions,
    get_session,
    rename_session,
    delete_session,
    save_message,
    save_message_with_meta,
    get_session_summary,
    set_session_summary,
    build_messages,
    generate_title,
    link_attachments,
    get_attachment_paths,
)
from rag import build_rag_prompt, should_use_rag
from vector_stores import get_embedding_backend
from app_file_upload import router as kb_router
from knowledge_base import list_kb_files
from middleware.stream_utils import extract_stream_delta as _extract_stream_delta, normalize_delta_text as _normalize_delta_text
from agent import runner as agent_runner
from agent import tools as agent_tools
from agent.tools.memory import (
    add_memory as memory_add,
    clear_memories as memory_clear,
    delete_memory as memory_delete,
    load_memories as memory_list,
    search_memories as memory_search,
)
from middleware import context as context_manager
import provider_config
import file_attachments

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
AVATAR_DIR = BASE_DIR / "data" / "avatars"
UI_STATE_FILE = BASE_DIR / "data" / "ui_state.json"
AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024


def _avatar_files(role: str):
    return list(AVATAR_DIR.glob(f"{role}.*")) if AVATAR_DIR.exists() else []


def _avatar_exists(role: str) -> bool:
    return bool(_avatar_files(role))


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


class ProviderRequest(BaseModel):
    id: Optional[str] = None
    name: str = ""
    kind: str = "cloud"          # local | cloud
    base_url: str = ""
    api_key: Optional[str] = None  # None/缺省=保留原值；空字符串=清除
    model: str = ""
    models: Optional[List[str]] = None  # 用户显式添加的模型列表（输入框只显示这些）
    is_default: bool = False


# ── 工具函数 ──

def _ollama_available() -> bool:
    """检查本地 Ollama 是否可连接（GET {base_url}/models，短超时）。"""
    import urllib.request
    local_provider = provider_config.get_local_provider()
    base_url = (local_provider or {}).get("base_url") or LOCAL_BASE_URL
    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _normalize_delta_text(value) -> Optional[str]:
    """把流式增量规范为字符串（兼容 str / list[dict] 两种格式）。"""
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


def _extract_stream_delta(chunk):
    """从流式 chunk 提取 (content, reasoning_content)，兼容异常结构。

    部分云端接口（如阿里云百炼兼容模式）会发送 choices 为空的 chunk
    （例如只带 usage 的收尾帧），旧代码直接 chunk.choices[0] 抛 IndexError，
    导致整段流式输出中断、页面只能刷新后看到结果。
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None, None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None, None
    content = _normalize_delta_text(getattr(delta, "content", None))
    reasoning = _normalize_delta_text(getattr(delta, "reasoning_content", None))
    return content, reasoning


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


def _fallback_local(messages: List[Dict], warning: str):
    """云端失败时回退本地 Ollama。返回 (reply, was_fallback, error_msg)。"""
    local_provider = provider_config.get_local_provider()
    if not local_provider:
        return "", False, f"{warning}（且未配置本地模型可回退）"
    try:
        client = provider_config.build_client(local_provider)
        response = client.chat.completions.create(
            model=local_provider["model"],
            messages=messages,
        )
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
        # 不限长度时（None）不传 max_tokens：有些服务商把 None 当成非法值
        token_kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        stream = client.chat.completions.create(
            model=local_provider["model"],
            messages=messages,
            stream=True,
            **token_kwargs,
        )
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
        return response.choices[0].message.content, False, ""
    except APIConnectionError as e:
        logger.error("LLM 连接失败(%s): %s", provider["id"], e)
        if provider.get("kind") == "local":
            return "", False, f"无法连接本地模型（Ollama）。请确认 Ollama 已启动（ollama serve）并已拉取模型：{model}"
        return _fallback_local(messages, f"云端连接失败，已自动回退到本地模型。({str(e)[:80]})")
    except APIError as e:
        logger.error("LLM API 错误(%s): %s", provider["id"], e)
        error_msg = str(e)
        if "403" in error_msg or "quota" in error_msg.lower() or "insufficient" in error_msg.lower():
            if provider.get("kind") != "local":
                logger.warning("云端配额/鉴权失败，回退到本地模型...")
                return _fallback_local(messages, "云端配额耗尽或鉴权失败，已自动回退到本地模型。")
            return "", False, f"API 错误：{error_msg}"
        return "", False, f"API 错误：{error_msg}"
    except Exception as e:
        logger.error("LLM 调用异常(%s): %s", provider["id"], e)
        if provider.get("kind") != "local":
            logger.warning("云端调用失败，回退到本地模型...")
            return _fallback_local(messages, f"云端调用失败，已自动回退到本地模型。({str(e)[:80]})")
        return "", False, f"调用失败：{str(e)}"


def _call_llm_stream(provider: Dict, messages: List[Dict], max_tokens: Optional[int] = None):
    """调用 LLM（流式），云端失败时自动回退到本地。返回 (stream, was_fallback, warning, actual_provider)。"""
    client = provider_config.build_client(provider)
    model = provider["model"]
    # 不限长度（None）时干脆不传这个参数，避免个别服务商把 null 当非法请求
    token_kwargs = {"max_tokens": max_tokens} if max_tokens else {}
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **token_kwargs
        )
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
async def api_config():
    """返回前端所需的运行配置（提供商列表、默认值、embedding 后端、支持的文件类型等）。"""
    providers = provider_config.list_providers()
    default_provider = provider_config.get_default_provider()
    local_provider = provider_config.get_local_provider()
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
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
async def api_health():
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
async def api_list_providers():
    return {"providers": provider_config.list_providers()}


@router.post("/api/providers")
async def api_save_provider(req: ProviderRequest):
    try:
        data = req.model_dump(exclude_unset=True)
        return provider_config.upsert_provider(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/providers/{provider_id}")
async def api_delete_provider(provider_id: str):
    try:
        ok = provider_config.delete_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="提供商不存在")
    return {"ok": True, "provider_id": provider_id}


@router.post("/api/providers/{provider_id}/default")
async def api_set_default_provider(provider_id: str):
    ok = provider_config.set_default_provider(provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提供商不存在")
    return {"ok": True, "provider_id": provider_id}


@router.post("/api/providers/{provider_id}/test")
async def api_test_provider(provider_id: str):
    try:
        return provider_config.test_provider(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/providers/{provider_id}/models")
async def api_provider_models(provider_id: str):
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
async def api_agent_tools():
    """列出 Agent 当前可用的工具（内置 + 自定义），供设置面板展示。"""
    return {"tools": agent_tools.list_tools()}


@router.get("/api/agent/changes")
async def api_agent_changes(limit: int = 50):
    """
    返回 Agent 的自我修改记录（data/agent_changes.log 的最后 N 条）。
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
async def api_agent_memory():
    """返回全部长期记忆（倒序，新的在前），供设置面板「记忆」标签页展示。"""
    items = memory_list()
    items.sort(key=lambda it: it.get("ts", 0), reverse=True)
    return {"items": items}


@router.post("/api/agent/memory")
async def api_agent_memory_add(req: Optional[Dict] = None):
    """手动添加一条长期记忆（用户在界面输入，与 Agent 的 remember 共用存储）。"""
    content = str((req or {}).get("content", "") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆内容不能为空。")
    result = memory_add(content, str((req or {}).get("tags", "") or ""), source="user")
    return result


@router.delete("/api/agent/memory/{mem_id}")
async def api_agent_memory_delete(mem_id: str):
    """删除一条长期记忆。"""
    result = memory_delete(mem_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "删除失败"))
    return result


@router.delete("/api/agent/memory")
async def api_agent_memory_clear():
    """清空全部长期记忆。"""
    return {"ok": True, "removed": memory_clear()}


@router.post("/api/agent/approval")
async def api_agent_approval(req: ApprovalRequest):
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
async def api_agent_rag_files(kb_slug: str = DEFAULT_KB_SLUG):
    """列出某个知识库中已持久化的 RAG 文件（Agent 可增删改这些文件）。"""
    return {"kb_slug": kb_slug, "files": list_kb_files(kb_slug)}


@router.post("/api/agent/search_test")
async def api_agent_search_test(req: Optional[SearchTestRequest] = None):
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
async def api_ui_state():
    """返回界面状态：上次使用的知识库 / RAG 开关 / 头像是否存在。"""
    state = {
        "kb_slug": DEFAULT_KB_SLUG,
        "use_rag": True,
        "avatar_bot": _avatar_exists("bot"),
        "avatar_user": _avatar_exists("user"),
        # Agent 默认设置：默认开启 Agent 模式与联网能力，最大 4 轮工具调用
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
                state["max_steps"] = max(1, min(data["max_steps"], 8))
            state["tavily_configured"] = bool(str(data.get("tavily_api_key", "") or "").strip())
            state["firecrawl_configured"] = bool(str(data.get("firecrawl_api_key", "") or "").strip())
            # 读取用户保存的 Agent 运行参数（带范围约束，避免手改 JSON 后越界）
            if isinstance(data.get("tavily_enabled"), bool):
                state["tavily_enabled"] = data["tavily_enabled"]
            state.update(_clamp_context_settings(data))
    except Exception as e:
        logger.warning("读取界面状态失败: %s", e)
    return state


@router.post("/api/ui/state")
async def api_save_ui_state(req: UiStateRequest):
    """保存界面状态（当前知识库 / RAG 开关）。"""
    UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if req.kb_slug is not None:
        data["kb_slug"] = req.kb_slug or DEFAULT_KB_SLUG
    if req.use_rag is not None:
        data["use_rag"] = bool(req.use_rag)
    if req.agent_mode is not None:
        data["agent_mode"] = bool(req.agent_mode)
    if req.enable_web is not None:
        data["enable_web"] = bool(req.enable_web)
    if req.max_steps is not None:
        data["max_steps"] = max(1, min(int(req.max_steps), 8))
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
    UI_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.post("/api/avatar/{role}")
async def api_upload_avatar(role: str, file: UploadFile = File(...)):
    """上传头像（bot=银狼 / user=我的），存 data/avatars/，重启不丢。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in AVATAR_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/jpeg/gif/webp 图片")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="头像请控制在 2MB 以内")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for old in _avatar_files(role):
        try:
            old.unlink()
        except OSError:
            pass
    (AVATAR_DIR / f"{role}{ext}").write_bytes(data)
    logger.info("头像已更新: %s", role)
    return {"ok": True, "url": f"/api/avatar/{role}?t={int(time.time())}"}


@router.delete("/api/avatar/{role}")
async def api_delete_avatar(role: str):
    """删除头像（恢复默认）。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    for old in _avatar_files(role):
        try:
            old.unlink()
        except OSError:
            pass
    return {"ok": True}


@router.get("/api/avatar/{role}")
async def api_get_avatar(role: str):
    """读取头像文件。"""
    if role not in ("bot", "user"):
        raise HTTPException(status_code=400, detail="role 只能是 bot 或 user")
    files = _avatar_files(role)
    if not files:
        raise HTTPException(status_code=404, detail="头像不存在")
    path = files[0]
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return FileResponse(str(path), media_type=media_type)


# ── 根路由（Vue 前端） ──

@router.get("/")
async def root():
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
async def api_create_session(req: Optional[CreateSessionRequest] = None):
    title = req.title if req else None
    return create_session(title=title)


@router.get("/api/sessions")
async def api_list_sessions():
    return list_sessions()


@router.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/api/sessions/{session_id}/export")
async def api_export_session(session_id: str):
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
async def api_rename_session(session_id: str, req: RenameSessionRequest):
    success = rename_session(session_id, req.title)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "title": req.title}


@router.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    # v0.8.1：删会话时一并清理其附件文件（仍被其它会话引用的保留）
    paths = get_attachment_paths(session_id)
    success = delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    removed = file_attachments.cleanup_session_attachments(session_id, paths)
    if removed:
        logger.info("删除会话时清理了 %d 个附件文件", removed)
    return {"ok": True}


# ── 兼容旧版路由 ──

@router.post("/sessions")
async def compat_create_session(req: Optional[CreateSessionRequest] = None):
    return await api_create_session(req)


@router.get("/sessions")
async def compat_list_sessions():
    return await api_list_sessions()


@router.get("/sessions/{session_id}")
async def compat_get_session(session_id: str):
    return await api_get_session(session_id)


@router.put("/sessions/{session_id}")
async def compat_rename_session(session_id: str, req: RenameSessionRequest):
    return await api_rename_session(session_id, req)


@router.delete("/sessions/{session_id}")
async def compat_delete_session(session_id: str):
    return await api_delete_session(session_id)


# ── 聊天 API ──

def _ui_settings() -> Dict:
    """读取 data/ui_state.json（界面状态：知识库 / RAG / Agent 设置）。"""
    if not UI_STATE_FILE.exists():
        return {}
    try:
        return json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
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

    enabled_tools = None
    if not enable_web:
        # 关闭联网时，把联网类工具从工具表里剔除（模型看不到，也就不会调用）
        enabled_tools = [
            t["name"] for t in agent_tools.list_tools()
            if t["name"] not in ("web_search", "fetch_page")
        ]
    return agent_mode, enabled_tools, int(max_steps)


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

def _prepare_chat_context(chat: ChatRequest, use_rag_override: Optional[bool] = None):
    """公共逻辑：创建/获取会话、构建消息、执行 RAG 检索。返回 (session_id, messages, rag_info)。"""
    session_id = chat.session_id
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
            ctx = file_attachments.build_attachments_context(resolved)
            if ctx:
                messages[-1] = {
                    **messages[-1],
                    "content": ctx + "\n\n" + messages[-1]["content"],
                }
            # v0.8.1：记录附件与会话的关联，删会话时据此清理物理文件
            link_attachments(session_id, resolved)
            logger.info("聊天附件已关联会话: %d 个", len(resolved))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    use_rag = chat.use_rag if use_rag_override is None else use_rag_override
    rag_info = {"has_context": False, "sources": []}
    if use_rag and should_use_rag(chat.message, chat.kb_slug):
        try:
            rag_result = build_rag_prompt(chat.message, chat.kb_slug)
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


@router.post("/api/chat")
async def chat(chat: ChatRequest):
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
    # 上下文管理：按预算裁剪历史 + 可选压缩（详细原理见 middleware/context.py）
    messages = _apply_context_management(session_id, messages, provider)
    limits = _runtime_limits()

    meta: Dict = {}
    was_fallback = False
    error_msg = ""
    if agent_mode:
        try:
            reply, meta = agent_runner.run_agent(
                provider, messages, enabled_tools, max_steps,
                reply_max_tokens=limits["reply_max_tokens"],
                tool_output_limit=limits["tool_output_limit"],
            )
        except Exception as e:
            logger.warning("Agent 模式执行失败，降级为普通对话: %s", e)
            agent_mode = False
    if not agent_mode:
        reply, was_fallback, error_msg = _call_llm_non_stream(
            provider, messages, max_tokens=limits["reply_max_tokens"]
        )

    if error_msg and not reply:
        raise HTTPException(status_code=502, detail=error_msg)

    save_message_with_meta(session_id, "user", chat.message)
    save_message_with_meta(session_id, "assistant", reply, meta or None)

    result = {
        "reply": reply,
        "session_id": session_id,
        "provider_id": provider["id"],
        "model": provider["model"],
        "agent_mode": agent_mode,
        "rag_used": rag_info["has_context"],
        "rag_sources": rag_info["sources"],
    }
    result.update({k: v for k, v in meta.items() if k in ("usage", "tools_used", "web_searches", "rag_hits")})
    if was_fallback:
        result["fallback_warning"] = error_msg

    return result


@router.post("/Silver_Wolf")
async def compat_chat(chat: ChatRequest):
    """兼容旧版路由。"""
    return await chat(chat)


@router.post("/api/chat/stream")
async def chat_stream(chat: ChatRequest):
    """
    流式聊天接口（SSE）。

    v2.0.0 起默认走 Agent 模式：
      模型 → (可选)调用工具 → 把工具结果喂回模型 → 输出最终回答；
      SSE 事件类型：content / reasoning / tool_call / tool_result / usage / warning / meta / [DONE]。
    Agent 关闭或不可用时，退回原来的纯流式输出。
    """
    agent_mode, enabled_tools, max_steps = _resolve_agent_options(chat)
    session_id, messages, rag_info = _prepare_chat_context(
        chat, use_rag_override=(False if agent_mode else None)
    )
    provider = _resolve_provider(chat.provider_id, chat.model, chat.use_local)
    # 上下文管理：裁剪 + 可选压缩（与 /api/chat 共用同一套逻辑）
    messages = _apply_context_management(session_id, messages, provider)
    limits = _runtime_limits()

    if agent_mode:
        def generate_agent():
            full_content = ""
            # v2.0.1：把模型的思考过程也收集起来——它只在流式里发给前端，
            # 不落库的话，重启服务/重新打开会话后「狼尊时刻」就会消失。
            full_reasoning = ""
            final_meta: Dict = {}

            def stream_once(active_provider):
                """跑一轮 Agent 事件流，把事件转成 SSE 文本；返回值写进收集器。"""
                nonlocal full_content, full_reasoning, final_meta
                for event in agent_runner.run_agent_stream(
                    active_provider, messages, enabled_tools, max_steps,
                    reply_max_tokens=limits["reply_max_tokens"],
                    tool_output_limit=limits["tool_output_limit"],
                ):
                    etype = event.get("type")
                    if etype == "content":
                        full_content += event["text"]
                        yield f"data: {json.dumps({'content': event['text'], 'session_id': session_id})}\n\n", False
                    elif etype == "reasoning":
                        full_reasoning += event["text"]
                        yield f"data: {json.dumps({'reasoning': event['text'], 'session_id': session_id})}\n\n", False
                    elif etype == "demote":
                        # 中间步骤的碎碎念：从回答里撤回，挪进「思考过程」。
                        # 不这么做的话，最终落库的"回答"就是这些过程话
                        # （实测事故：整条回复只有"搜索结果全是导航站，没用。换几个精确的关键词。"）
                        dropped = event["text"]
                        if full_content.endswith(dropped):
                            full_content = full_content[: -len(dropped)]
                        else:
                            full_content = full_content.replace(dropped, "")
                        if dropped.strip():
                            full_reasoning += ("\n" if full_reasoning else "") + dropped.strip()
                        yield f"data: {json.dumps({'demote': dropped, 'session_id': session_id})}\n\n", False
                    elif etype == "warning":
                        yield f"data: {json.dumps({'warning': event['text'], 'session_id': session_id})}\n\n", False
                    elif etype == "tool_call":
                        yield f"data: {json.dumps({'tool_call': {'name': event['name'], 'args': event['args']}, 'session_id': session_id})}\n\n", False
                    elif etype == "tool_approval":
                        # 高危工具请求用户授权：前端弹出「允许 / 拒绝」，用 request_id 回传
                        yield f"data: {json.dumps({'tool_approval': {'request_id': event['request_id'], 'name': event['name'], 'args': event['args']}, 'session_id': session_id})}\n\n", False
                    elif etype == "plan":
                        # 任务计划清单（update_plan 工具），前端渲染进度
                        yield f"data: {json.dumps({'plan': event['steps'], 'session_id': session_id})}\n\n", False
                    elif etype == "tool_result":
                        yield f"data: {json.dumps({'tool_result': {'name': event['name'], 'ok': event['ok'], 'summary': event['summary']}, 'session_id': session_id})}\n\n", False
                    elif etype == "usage":
                        yield f"data: {json.dumps({'usage': event['usage'], 'session_id': session_id})}\n\n", False
                    elif etype == "done":
                        final_meta = event.get("meta", {})

            used_provider = provider
            try:
                for sse, _ in stream_once(used_provider):
                    yield sse
            except Exception as e:
                # 云端 Provider 连接/鉴权失败时，自动回退到本地 Ollama 再试一次
                local_provider = provider_config.get_local_provider() if provider.get("kind") != "local" else None
                if local_provider:
                    logger.warning("Agent 云端失败(%s)，回退本地模型重试", str(e)[:120])
                    yield f"data: {json.dumps({'fallback_warning': '云端调用失败，已切换到本地模型重试', 'session_id': session_id})}\n\n"
                    used_provider = local_provider
                    try:
                        for sse, _ in stream_once(used_provider):
                            yield sse
                    except Exception as e2:
                        logger.error("Agent 本地回退也失败: %s", e2)
                        yield f"data: {json.dumps({'error': str(e2), 'session_id': session_id})}\n\n"
                else:
                    logger.error("Agent 流式执行异常: %s", e)
                    yield f"data: {json.dumps({'error': str(e), 'session_id': session_id})}\n\n"

            try:
                saved_meta = dict(final_meta)
                if full_reasoning:
                    saved_meta["reasoning"] = full_reasoning
                # 兜底：万一模型整轮只输出过程话（被 demote 撤回）而没给答案，
                # 也不能把空白消息存进历史 —— 给一句符合人设的交代。
                if not full_content.strip():
                    full_content = "……这轮没捞到能用的东西。把问题收窄一点，或者换个说法再问银狼一次。"
                    yield f"data: {json.dumps({'content': full_content, 'session_id': session_id})}\n\n"
                save_message_with_meta(session_id, "user", chat.message)
                save_message_with_meta(session_id, "assistant", full_content, saved_meta or None)
            except Exception as e:
                logger.error("消息持久化失败: %s", e)

            meta = {
                "session_id": session_id,
                "agent_mode": True,
                "provider_id": used_provider["id"],
                "model": used_provider["model"],
                "rag_used": rag_info["has_context"],
                "rag_sources": rag_info["sources"],
            }
            meta.update(final_meta)
            yield f"data: {json.dumps(meta)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate_agent(), media_type="text/event-stream")

    # ── 普通模式（Agent 关闭）：沿用原有流式逻辑 ──
    try:
        stream, was_fallback, fallback_msg, actual_provider = _call_llm_stream(
            provider, messages, max_tokens=limits["reply_max_tokens"]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def generate():
        full_content = ""
        full_reasoning = ""  # 同上：思考过程需要落库，重启后才看得到
        try:
            for chunk in stream:
                content, reasoning = _extract_stream_delta(chunk)
                if reasoning:
                    full_reasoning += reasoning
                    yield f"data: {json.dumps({'reasoning': reasoning, 'session_id': session_id})}\n\n"
                if content:
                    full_content += content
                    yield f"data: {json.dumps({'content': content, 'session_id': session_id})}\n\n"
        except Exception as e:
            logger.error("流式输出异常: %s", e)
            yield f"data: {json.dumps({'error': str(e), 'session_id': session_id})}\n\n"

        try:
            save_message_with_meta(session_id, "user", chat.message)
            save_message_with_meta(
                session_id, "assistant", full_content,
                {"reasoning": full_reasoning} if full_reasoning else None,
            )
        except Exception as e:
            logger.error("消息持久化失败: %s", e)

        meta = {
            "session_id": session_id,
            "agent_mode": False,
            "rag_used": rag_info["has_context"],
            "rag_sources": rag_info["sources"],
            "provider_id": actual_provider["id"],
            "model": actual_provider["model"],
        }
        if was_fallback:
            meta["fallback_warning"] = fallback_msg
        yield f"data: {json.dumps(meta)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/Silver_Wolf/stream")
async def compat_chat_stream(chat: ChatRequest):
    """兼容旧版路由。"""
    return await chat_stream(chat)


# ── 挂载知识库路由（作为子路由） ──
router.include_router(kb_router)
