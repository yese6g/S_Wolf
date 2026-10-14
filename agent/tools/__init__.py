"""
agent.tools - 工具仓库（模型可以主动调用的能力的唯一入口）

【本目录的组织方式：一个功能一个文件】
    web_search.py    联网搜索（Tavily / Bing 爬虫，含后端实现）
    fetch_page.py    打开网页读正文
    datetime_tool.py 当前日期时间
    knowledge.py     RAG 知识库的检索与增删改（这个功能相关，所以放同一个文件）
    self_manage.py   自定义工具管理（tools_list / tools_upsert / tools_delete）
    _web.py          下划线开头 = 内部辅助模块，**不会被注册成工具**（共用 HTTP/解析工具函数）

【新增一个工具要做什么】
    1. 在本目录新建 `xxx.py`；
    2. 在里面写 `TOOLS = {"工具名": {"description": ..., "parameters": {...}, "handler": fn}}`；
    3. handler 的约定：接收 dict 参数，返回 `(给模型看的文本, 给前端展示的 meta)`。
    不需要改本文件——**导入时自动发现**（见下面的 _discover）。
    删除工具同理：删掉对应文件（或文件里的那个 TOOLS 条目）即可。

【为什么用自动发现】
    老版本把所有工具塞在一个 460 行的 agent_tools.py 里，加/删工具都要翻大文件、
    改注册表，容易漏改。自动发现把"注册"这件事交给文件系统：文件在，工具就在。

【自定义工具（Agent 自己增删改的）】
    存在 data/tools.json，是**声明式 HTTP 工具**（方法 + URL 模板 + 参数表），
    不执行任意代码。读写逻辑在 self_manage.py。

【对外 API】（SilverWolf.py / agent/runner.py 只用这几个）
    BUILTIN_TOOLS        内置工具表 {名字: 定义}
    list_tools()         全部工具（内置 + 自定义）的简要信息，供接口/前端展示
    openai_tool_schemas() 生成 OpenAI 兼容的 tools 参数
    execute_tool()       真正执行一个工具，异常统一兜住
    log_change()         记录 Agent 的自我修改（写 data/logs/changes.log）
    CHANGES_LOG          变更日志路径（接口读取它做展示）
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from ._policy import classify

from .agent.self_manage import (
    CHANGES_LOG,
    load_custom_tools as _load_custom_tools,
    log_change,
    run_custom_http_tool as _run_custom_http_tool,
)

logger = logging.getLogger(__name__)

# 内置工具表：由 _discover() 在导入时填充
BUILTIN_TOOLS: Dict[str, Dict[str, Any]] = {}


def _discover() -> None:
    """扫描本目录下的工具模块并注册（下划线开头的模块视为内部辅助，跳过）。"""
    package_dir = Path(__file__).resolve().parent
    for mod_info in sorted(pkgutil.walk_packages([str(package_dir)],prefix=__name__+'.'),key=lambda m:m.name):
        module_name = mod_info.name
        relative = module_name[len(__name__)+1:]
        if mod_info.ispkg or any(part.startswith('_') for part in relative.split('.')):
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception as e:  # 单个工具模块坏掉不该拖垮整个 Agent
            logger.exception("工具模块 %s 导入失败: %s", module_name, e)
            continue
        tools = getattr(module, "TOOLS", None)
        if not isinstance(tools, dict):
            logger.warning("工具模块 %s 未导出 TOOLS 字典，已跳过", module_name)
            continue
        for tool_name, spec in tools.items():
            if not isinstance(spec, dict) or "handler" not in spec:
                logger.warning("工具 %s 的定义不完整（缺少 handler），已跳过", tool_name)
                continue
            if tool_name in BUILTIN_TOOLS:
                logger.error("工具名重复：%s（拒绝后加载的 %s）", tool_name, module_name)
                continue
            spec['category'] = relative.split('.')[0] if '.' in relative else 'core'
            BUILTIN_TOOLS[tool_name] = classify(tool_name, spec)
    logger.info("已注册内置工具 %d 个：%s", len(BUILTIN_TOOLS), "、".join(BUILTIN_TOOLS))


_discover()
# MCP 连接/断开会在运行中动态增删外部工具，读写都过这把锁；
# 快照复制（{**BUILTIN_TOOLS, **EXTERNAL_TOOLS}）时被并发改字典会 RuntimeError。
EXTERNAL_LOCK = threading.RLock()
EXTERNAL_TOOLS: Dict[str, Dict[str, Any]] = {}


def _external_snapshot() -> Dict[str, Dict[str, Any]]:
    """带锁复制外部工具表，供遍历/合并使用。"""
    with EXTERNAL_LOCK:
        return dict(EXTERNAL_TOOLS)


def tool_spec(name):
    """返回内置/外部/自定义统一风险定义；未知调用默认审批且仍会在执行层拒绝。"""
    if name in BUILTIN_TOOLS:
        return BUILTIN_TOOLS[name]
    if name in EXTERNAL_TOOLS:
        return EXTERNAL_TOOLS[name]
    return {"approval_required": True, "risks": ["external", "network"]}


def list_tools() -> List[Dict[str, Any]]:
    """返回所有可用工具的简要信息（内置 + 自定义），供接口 / 前端展示。"""
    items = [
        {"name": name, "description": spec["description"], "kind": "builtin" if name in BUILTIN_TOOLS else "mcp",
         'category':spec.get('category','core'),'parameters':spec.get('parameters',{}),
         "risks": spec.get("risks", []), "approval_required": bool(spec.get("approval_required"))}
        for name, spec in {**BUILTIN_TOOLS, **_external_snapshot()}.items()
    ]
    for t in _load_custom_tools():
        items.append({'name':t.get('name'),'description':t.get('description',''),'kind':'custom','category':t.get('category','core'),
                      'risks':['external','network'],'approval_required':True,'enabled':t.get('enabled',True),
                      'parameters':{'type':'object','properties':t.get('params',{})}})
    return items


def openai_tool_schemas(enabled: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    生成 OpenAI 兼容的 tools 参数（function calling 格式）。

    enabled 为 None 时启用全部；否则只启用列表中的工具（用于「联网开关」等场景）。
    """
    schemas: List[Dict[str, Any]] = []
    for name, spec in {**BUILTIN_TOOLS, **_external_snapshot()}.items():
        if enabled is not None and name not in enabled:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        })
    for tool in _load_custom_tools():
        name = tool.get("name")
        if not name or not tool.get('enabled',True) or (enabled is not None and name not in enabled):
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": tool.get("params", {}) or {},
                },
            },
        })
    return schemas


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from ._policy import apply_hook
    try:
        denied=apply_hook('before_tool',name)
        if denied:return {'ok':False,'content':denied,'meta':{'ok':False,'denied':True,'tool':name}}
        result=_execute_tool(name,args)
        apply_hook('after_tool',name,result['ok'])
        return result
    except Exception:
        logger.exception('工具守卫或执行失败: %s',name)
        return {'ok':False,'content':'工具未完成，请检查配置与错误日志。','meta':{'ok':False,'tool':name}}


def _execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行一个工具，返回统一结构：
        {"ok": bool, "content": "给模型看的文本", "meta": {给前端展示的信息}}
    未知工具 / 参数错误都会被兜住，保证 Agent 循环不会因为工具异常而中断。
    """
    args = args or {}
    spec = BUILTIN_TOOLS.get(name) or EXTERNAL_TOOLS.get(name)
    if spec:
        try:
            content, meta = spec["handler"](args)
            return {"ok": bool(meta.get("ok", True)), "content": content, "meta": meta}
        except Exception as e:
            logger.exception("内置工具执行失败: %s", name)
            return {"ok": False, "content": f"工具 {name} 执行异常：{str(e)[:200]}", "meta": {"ok": False, "tool": name}}

    custom = next((t for t in _load_custom_tools() if t.get("name") == name), None)
    if custom:
        try:
            content, meta = _run_custom_http_tool(custom, args)
            return {"ok": bool(meta.get("ok", True)), "content": content, "meta": meta}
        except Exception:
            logger.exception("自定义工具执行失败: %s", name)
            return {"ok":False,"content":"工具执行失败，请检查配置与错误日志。","meta":{"ok":False,"tool":name}}

    return {"ok": False, "content": f"没有名为 {name} 的工具。", "meta": {"ok": False, "tool": name}}


__all__ = [
    "BUILTIN_TOOLS",
    "CHANGES_LOG",
    "execute_tool",
    "list_tools",
    "log_change",
    "openai_tool_schemas",
]
