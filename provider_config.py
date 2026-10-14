"""
provider_config.py - 模型提供商（Provider）配置管理

SilverWolf v0.6.0 新增：把「本地 Ollama」和任意「云端 OpenAI 兼容接口」统一抽象为
Provider（提供商）。配置持久化到 data/providers.json（自动创建、已被 gitignore），
首次启动**不预置任何模型**，完全由用户在设置面板/接口中添加。

每个 Provider 包含：
    id        唯一标识（自动生成或自定义）
    name      显示名称
    kind      "local"（本地 Ollama）/ "cloud"（云端 API）
    base_url  OpenAI 兼容接口地址
    api_key   API Key（本地可留空；JSON 中明文保存，仅供本机使用）
    model     默认模型名
    is_default 是否默认（新对话默认使用）
"""

import json
import logging
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

from openai import OpenAI

from config_data import BASE_DIR
from middleware.logging_system import register_secret

logger = logging.getLogger(__name__)

# ── 存储位置 ──
DATA_DIR = BASE_DIR / "data"
PROVIDERS_FILE = DATA_DIR / "providers.json"

_lock = threading.Lock()

# 允许的 kind 取值
KIND_LOCAL = "local"
KIND_CLOUD = "cloud"


# ── 工具函数 ──

def mask_api_key(key: str) -> Optional[str]:
    """脱敏 API Key：仅保留前 3 位与后 4 位。"""
    if not key:
        return None
    key = key.strip()
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def _public_provider(p: Dict[str, Any]) -> Dict[str, Any]:
    """返回给前端的 Provider 信息（不包含完整 api_key）。"""
    register_secret(p.get('api_key') or '')
    if p.get('api_key_env'):register_secret(os.getenv(p['api_key_env'],'').strip())
    return {
        "id": p["id"],
        "name": p.get("name", p["id"]),
        "kind": p.get("kind", KIND_CLOUD),
        "base_url": p.get("base_url", ""),
        "model": p.get("model", ""),
        "models": list(p.get("models") or []),
        "is_default": bool(p.get("is_default", False)),
        "has_api_key": bool(os.getenv(p['api_key_env'],'') if p.get('api_key_env') else p.get("api_key")),
        "api_key_masked": '环境变量' if p.get('api_key_env') else mask_api_key(p.get("api_key", "")),
        'api_key_env':p.get('api_key_env',''),
    }


# ── 默认播种（从 .env） ──

def _migrate(providers: List[Dict[str, Any]]) -> bool:
    """
    兼容旧数据：v0.7.0 起每个 Provider 有用户自维护的 models 列表。
    仅当 models 字段缺失/类型错误时，用默认 model 补一个初始项；
    用户显式清空（[]）的一律保留为空。
    """
    changed = False
    for p in providers:
        if "models" not in p or not isinstance(p.get("models"), list):
            default = (p.get("model") or "").strip()
            p["models"] = [default] if default else []
            changed = True
    return changed


# ── 读写 ──

def _load() -> List[Dict[str, Any]]:
    """加载 Provider 列表；文件不存在时创建空列表（不预置任何模型）。"""
    if not PROVIDERS_FILE.exists():
        providers = []
        _save(providers)
        logger.info("首次运行：未预置模型提供商，请到「设置 → 模型提供商」添加 -> %s", PROVIDERS_FILE)
        return providers
    try:
        with open(PROVIDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        providers = data.get("providers", data) if isinstance(data, dict) else data
        if not isinstance(providers, list):
            raise ValueError("providers 格式错误")
        if _migrate(providers):
            _save(providers)
        return providers
    except Exception as e:
        logger.exception("读取 providers.json 失败，保留原文件")
        raise ValueError("模型配置文件损坏；原文件已保留，请检查后再试") from e


def _save(providers: List[Dict[str, Any]]) -> None:
    """原子写入 providers.json。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROVIDERS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"providers": providers}, f, ensure_ascii=False, indent=2)
    tmp.replace(PROVIDERS_FILE)


# ── 对外 API ──

def list_providers() -> List[Dict[str, Any]]:
    """返回脱敏后的 Provider 列表。"""
    with _lock:
        return [_public_provider(p) for p in _load()]


def get_provider(provider_id: str) -> Optional[Dict[str, Any]]:
    """获取完整 Provider（含 api_key，仅供后端内部使用）。"""
    with _lock:
        for p in _load():
            if p["id"] == provider_id:
                return dict(p)
    return None


def get_default_provider() -> Optional[Dict[str, Any]]:
    """获取默认 Provider；未标记时取第一个。"""
    with _lock:
        providers = _load()
        for p in providers:
            if p.get("is_default"):
                return dict(p)
        return dict(providers[0]) if providers else None


def get_local_provider() -> Optional[Dict[str, Any]]:
    """获取第一个本地（Ollama）Provider，云端失败回退时使用。"""
    with _lock:
        for p in _load():
            if p.get("kind") == KIND_LOCAL:
                return dict(p)
    return None


def upsert_provider(data: Dict[str, Any]) -> Dict[str, Any]:
    """新增或更新 Provider。返回脱敏后的结果。"""
    with _lock:
        providers = _load()
        pid = (data.get("id") or "").strip() or f"p-{uuid.uuid4().hex[:8]}"
        name = (data.get("name") or "").strip() or pid
        kind = data.get("kind") or KIND_CLOUD
        if kind not in (KIND_LOCAL, KIND_CLOUD):
            raise ValueError("kind 只能是 local 或 cloud")
        base_url = (data.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("接口地址 base_url 不能为空")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        model = (data.get("model") or "").strip()
        if not model:
            raise ValueError("模型名不能为空")
        models = data.get("models")
        if models is not None and not isinstance(models, list):
            raise ValueError("models 必须是字符串列表")
        models_cleaned = [str(m).strip() for m in (models or []) if str(m).strip()]
        reference=data.get('api_key_env')
        if reference and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',reference):raise ValueError('凭据引用必须是环境变量名称')

        existing = next((p for p in providers if p["id"] == pid), None)
        if existing:
            # 未传 api_key（None/缺省）→ 保留原值；传空字符串 → 清除
            if "api_key" in data and data["api_key"] is not None:
                existing["api_key"] = (data["api_key"] or "").strip()
            existing["name"] = name
            existing["kind"] = kind
            existing["base_url"] = base_url
            existing["model"] = model
            if models is not None:
                existing["models"] = models_cleaned
            if data.get("is_default"):
                for p in providers:
                    p["is_default"] = p["id"] == pid
            provider = existing
            if reference is not None:
                provider['api_key_env']=reference.strip()
                if reference:provider['api_key']=''
        else:
            provider = {
                "id": pid,
                "name": name,
                "kind": kind,
                "base_url": base_url,
                "api_key": (data.get("api_key") or "").strip(),
                "model": model,
                "models": models_cleaned,
                "is_default": False,
            }
            providers.append(provider)
            provider['api_key_env']=reference or ''
            if reference:provider['api_key']=''
            if data.get("is_default") or len(providers) == 1:
                for p in providers:
                    p["is_default"] = p["id"] == pid
        _save(providers)
        return _public_provider(provider)


def delete_provider(provider_id: str) -> bool:
    """删除 Provider。允许删到 0 个（回到“未配置”状态）。返回是否成功。"""
    with _lock:
        providers = _load()
        remaining = [p for p in providers if p["id"] != provider_id]
        if len(remaining) == len(providers):
            return False
        if remaining and not any(p.get("is_default") for p in remaining):
            remaining[0]["is_default"] = True
        _save(remaining)
        return True


def set_default_provider(provider_id: str) -> bool:
    """设为默认 Provider。返回是否成功。"""
    with _lock:
        providers = _load()
        if not any(p["id"] == provider_id for p in providers):
            return False
        for p in providers:
            p["is_default"] = p["id"] == provider_id
        _save(providers)
        return True


def list_models(provider_id: str) -> Dict[str, Any]:
    """
    列出 Provider 可用的模型（GET {base_url}/models，OpenAI 兼容）。
    失败时回退为配置中的默认模型名，并标记 fallback=true。
    """
    provider = get_provider(provider_id)
    if not provider:
        raise KeyError(f"提供商不存在: {provider_id}")
    try:
        client = build_client(provider)
        resp = client.models.list()
        models = sorted({m.id for m in resp.data if getattr(m, "id", None)})
        if not models:
            raise RuntimeError("接口未返回模型列表")
        return {"provider_id": provider_id, "models": models, "fallback": False}
    except Exception as e:
        logger.warning("获取 %s 模型列表失败，使用默认模型名: %s", provider_id, e)
        return {
            "provider_id": provider_id,
            "models": [provider["model"]],
            "fallback": True,
            "error": str(e)[:200],
        }


def test_provider(provider_id: str) -> Dict[str, Any]:
    """测试 Provider 连通性（拉取模型列表）。"""
    provider = get_provider(provider_id)
    if not provider:
        raise KeyError(f"提供商不存在: {provider_id}")
    try:
        result = list_models(provider_id)
        return {
            "ok": not result.get("fallback"),
            "provider_id": provider_id,
            "model_count": len(result["models"]),
            "models": result["models"],
            "message": "连接成功" if not result.get("fallback") else f"接口可达，但未能列出模型：{result.get('error', '')}",
        }
    except Exception as e:
        return {"ok": False, "provider_id": provider_id, "message": str(e)[:200]}


def build_client(provider: Dict[str, Any]) -> OpenAI:
    """复用连接池；本地 localhost 固定 IPv4，避免 Windows IPv6 回退等待。"""
    api_key = (provider.get("api_key") or "").strip() or "sk-local-no-key"
    if provider.get('api_key_env'):
        api_key=os.getenv(provider['api_key_env'],'').strip()
        if not api_key:raise ValueError('提供商凭据环境变量未配置')
    register_secret(api_key)
    url = local_base_url(provider['base_url']) if provider.get('kind') == 'local' else provider['base_url']
    key = (url, api_key)
    with _CLIENT_LOCK:
        if key not in _CLIENTS or _CLIENTS[key].is_closed():
            if len(_CLIENTS) >= 16:
                # 淘汰的客户端可能仍被在用请求持有，不立刻关闭，
                # 移入退役表交给 close_clients 统一兜底，避免连接永久泄漏。
                _RETIRED.append(_CLIENTS.pop(next(iter(_CLIENTS))))
                if len(_RETIRED) > 32:
                    try:
                        _RETIRED.pop(0).close()
                    except Exception:
                        pass
            _CLIENTS[key] = OpenAI(api_key=api_key, base_url=url, timeout=300.0, max_retries=1)
        return _CLIENTS[key]


def local_base_url(url):
    from urllib.parse import urlsplit, urlunsplit
    value = urlsplit(url)
    if value.hostname and value.hostname.lower() == 'localhost':
        host = '127.0.0.1' + (':' + str(value.port) if value.port else '')
        return urlunsplit((value.scheme, host, value.path, value.query, value.fragment))
    return url


_CLIENT_LOCK = threading.RLock()
_CLIENTS = {}
_RETIRED = []


def close_clients():
    with _CLIENT_LOCK:
        clients = list(_CLIENTS.values()) + list(_RETIRED)
        _CLIENTS.clear()
        _RETIRED.clear()
    for client in clients:
        client.close()
