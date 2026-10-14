"""
agent/tools/self_manage.py - 自定义工具的增删改 + 自我修改留痕

【这个文件干什么】
    1. 提供三个让 Agent 管理自己工具的工具：
         tools_list   查看自己有哪些自定义工具
         tools_upsert 新增/修改一个自定义工具
         tools_delete 删除一个自定义工具
    2. 负责自定义工具的**存储**（data/tools.json）与**变更留痕**（data/logs/changes.log）。

【自定义工具长什么样（声明式 HTTP 工具）】
    {"name": "get_weather", "method": "GET", "url": "https://api.x.com/w?city={city}",
     "params": {"city": {"type": "string"}}, "headers": {"Authorization": "Bearer ..."}}
    执行时只做「参数替换进 URL → 发请求」，**不执行任意代码**，
    这样 Agent 即使乱写工具，也伤不到本机。

【自我修改的边界】
    Agent 只能写 knowledge_bases/<kb>/files/ 下的文本文件与 data/tools.json；
    不能改代码、配置、数据库、其它目录。所有改动都写进 data/logs/changes.log 供审计。

【为什么 log_change 放在这里】
    它是「自我修改」这件事的记账本，和写工具的动作成对出现；
    知识库工具（knowledge.py）要留痕时也从这里 import，避免各处重复实现。

【谁在调用】
    - 模型 → 注册表 → 本文件底部的 TOOLS；
    - agent/tools/__init__.py → load_custom_tools / run_custom_http_tool（自定义工具的读取与执行）；
    - SilverWolf.py 的「自我修改记录」接口 → CHANGES_LOG（读取日志文件）。
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote, urlsplit
from middleware.outbound import request_public
from middleware.logging_system import register_secret
from pathlib import Path
from typing import Any, Dict, List


from config_data import BASE_DIR
from agent import persistence

logger = logging.getLogger(__name__)

DATA_DIR = Path(BASE_DIR) / "data"
TOOLS_FILE = DATA_DIR / "tools.json"            # Agent 自定义工具定义
CHANGES_LOG = DATA_DIR / "logs" / "changes.log" # 页面与总管共用一份 JSONL 修改记录


# ── 自定义工具（声明式 HTTP）读写 ──

def load_custom_tools() -> List[Dict[str, Any]]:
    """读取 data/tools.json；不存在返回空列表（不自动创建，避免脏文件）。"""
    if not TOOLS_FILE.exists():
        return []
    try:
        data = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
        tools = data.get("tools", data) if isinstance(data, dict) else data
        return tools if isinstance(tools, list) else []
    except Exception as e:
        logger.warning("读取自定义工具失败: %s", e)
        return []


def save_custom_tools(tools: List[Dict[str, Any]]) -> None:
    """原子写入 data/tools.json（先写临时文件再替换，避免写坏半截）。"""
    persistence.save(TOOLS_FILE.parent,TOOLS_FILE.stem,{'tools':tools})


def public_definition(tool):
    return {**tool,'headers':{key:'已配置' for key in (tool.get('headers') or {})}}


def set_custom_tool_enabled(name: str, enabled: bool) -> bool:
    """同步 tools.json 里的 enabled 字段（工作台开关的另一半来源）。

    capabilities 以「enabled 字段 且 不在 disabled_tools」双条件为真，
    只改 ui_state 的话，tools.json 里 enabled=false 的工具开不回来。
    返回是否命中某个自定义工具；内置/MCP 工具不落在这里。
    """
    tools = load_custom_tools()
    hit = False
    for tool in tools:
        if tool.get("name") == name:
            tool["enabled"] = enabled
            hit = True
    if hit:
        save_custom_tools(tools)
    return hit


def log_change(action: str, detail: Dict[str, Any]) -> None:
    """记录 Agent 的自我修改（时间/动作/细节），用于事后审计。"""
    logging.getLogger("audit.changes").info("action=%s fields=%s", action, sorted(detail),
        extra={'change': {'action': action, 'detail': detail}})


def run_custom_http_tool(tool: Dict[str, Any], args: Dict[str, Any]):
    """
    执行声明式 HTTP 工具：把 {参数} 替换进 URL，然后发起请求。

    返回 (给模型看的文本, meta)。任何异常都转成可读文本，不让 Agent 循环中断。
    """
    url = tool.get("url", "")
    for key, value in (args or {}).items():
        url = url.replace("{" + str(key) + "}", quote(str(value), safe=""))
    method = str(tool.get("method", "GET")).upper()
    headers = dict(tool.get("headers") or {})
    import os
    for header,reference in (tool.get('headers_env') or {}).items():
        value=os.getenv(reference,'')
        if not value:raise ValueError('工具环境凭据尚未配置')
        headers[header]=value
    for key,value in headers.items():
        if any(word in key.lower() for word in ('authorization','key','token','cookie')):
            register_secret(str(value))
    try:
        text = request_public(url, method, headers, args, tool.get("allowed_hosts", []))
        return f"工具 {tool['name']} 返回：\n{text}", {"ok": True, "tool": tool["name"], "url": url}
    except Exception as e:
        return (f"工具 {tool['name']} 调用失败：{str(e)[:200]}",
                {"ok": False, "tool": tool["name"], "url": url})


# ── 三个自我维护工具 ──

def _tool_tools_list(args: Dict[str, Any]):
    custom = load_custom_tools()
    names = [t.get("name") for t in custom if t.get("name")]
    if not names:
        return "还没有自定义工具（只启用了内置工具）。", {"ok": True, "tools": []}
    return "自定义工具：" + "、".join(names), {"ok": True, "tools": [public_definition(t) for t in custom]}


@persistence.transaction
def _tool_tools_upsert(args: Dict[str, Any]):
    """
    新增/修改一个自定义 HTTP 工具。

    参数：
        name        工具名（英文，模型调用时使用）
        description 什么时候该用这个工具（写给模型看）
        method      GET / POST
        url         URL 模板，用 {参数名} 占位，例如 https://api.x.com/w?city={city}
        params      参数定义（JSON 字符串或对象），形如 {"city": {"type": "string", "description": "城市"}}
        headers     可选请求头（JSON 字符串或对象，例如 API Key）
    """
    name = str(args.get("name", "")).strip()
    import re
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}',name):
        return "工具名必须是字母/数字/下划线组合。", {"ok": False}
    from agent import tools as registry
    if name in registry.BUILTIN_TOOLS or name in registry.EXTERNAL_TOOLS:
        return '这个名称已属于内置或MCP工具，不能覆盖。',{'ok':False}
    try:
        params = args.get("params") or {}
        if isinstance(params, str):
            params = json.loads(params) if params.strip() else {}
        headers = args.get("headers") or {}
        if isinstance(headers, str):
            headers = json.loads(headers) if headers.strip() else {}
        if not isinstance(params,dict) or not isinstance(headers,dict):raise ValueError('参数和请求头必须是对象')
        if any(not isinstance(v,dict) or v.get('type') not in {'string','integer','number','boolean','object','array','null'} for v in params.values()):
            raise ValueError('参数需要有效的JSON Schema类型')
    except Exception as e:
        return f"params/headers 不是合法 JSON：{e}", {"ok": False}

    tool = {
        "name": name,
        "description": str(args.get("description", "")).strip() or f"自定义工具 {name}",
        "method": str(args.get("method") or "GET").upper(),
        "url": str(args.get("url", "")).strip(),
        "params": params,
        "headers": headers,
        'headers_env':args.get('headers_env') or {},
        'category':args.get('category','core'),'enabled':bool(args.get('enabled',True)),
    }
    if not tool["url"].startswith(("http://", "https://")):
        return "url 必须以 http:// 或 https:// 开头。", {"ok": False}
    if tool['method'] not in {'GET','POST'} or tool['category'] not in {'core','agent','python','cyberSecurity'}:
        return '方法或分类无效。',{'ok':False}
    if not isinstance(tool['headers_env'],dict) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',str(v)) for v in tool['headers_env'].values()):
        return '凭据只填写环境变量名称。',{'ok':False}
    parsed = urlsplit(tool["url"])
    if not parsed.hostname or "{" in parsed.netloc or parsed.username or parsed.password:
        return "工具域名必须固定，参数只能放路径或查询里。", {"ok": False}
    tool["allowed_hosts"] = [parsed.hostname]

    tools = load_custom_tools()
    tools = [t for t in tools if t.get("name") != name] + [tool]
    save_custom_tools(tools)
    log_change("tools_upsert", {"name": name, "url": tool["url"], "method": tool["method"]})
    return f"已保存工具 {name}（{tool['method']} {tool['url']}）", {"ok": True, "tool": public_definition(tool)}


@persistence.transaction
def _tool_tools_delete(args: Dict[str, Any]):
    name = str(args.get("name", "")).strip()
    tools = load_custom_tools()
    remaining = [t for t in tools if t.get("name") != name]
    if len(remaining) == len(tools):
        return f"没有找到工具 {name}。", {"ok": False}
    save_custom_tools(remaining)
    log_change("tools_delete", {"name": name})
    return f"已删除工具 {name}。", {"ok": True}


TOOLS = {
    "tools_list": {
        "description": "查看自己当前拥有的自定义工具。",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_tools_list,
    },
    "tools_upsert": {
        "description": "新增或修改一个自定义 HTTP 工具（声明式：方法 + URL 模板 + 参数），用于扩展自己的能力。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名（英文/下划线）"},
                "description": {"type": "string", "description": "这个工具做什么、什么时候用"},
                "method": {"type": "string", "description": "GET 或 POST"},
                "url": {"type": "string", "description": "URL 模板，参数用 {name} 占位"},
                "params": {"type": "string", "description": "参数定义 JSON，如 {\"city\":{\"type\":\"string\"}}"},
                "headers": {"type": "string", "description": "可选请求头 JSON（例如 API Key）"},
            },
            "required": ["name", "url"],
        },
        "approval_required": True,  # 自定义工具可携带请求头（可能含密钥），必须用户确认
        "handler": _tool_tools_upsert,
    },
    "tools_delete": {
        "description": "删除一个自定义工具。",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "approval_required": True,  # 删除不可逆
        "handler": _tool_tools_delete,
    },
}
