"""原文分页工具：自动注册，配合 agent.artifacts 的溢出 locator 找回头尾之外的信息。"""
from agent import artifacts


def _read(args):
    data = artifacts.read(args.get("locator"), args.get("offset", 0), args.get("limit", 3000))
    return data["text"], {"ok": True, **data}


TOOLS = {"read_tool_output": {"description": "用工具结果里的 locator 分页读取完整原文，offset 是字符偏移。",
    "parameters": {"type": "object", "properties": {"locator": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["locator"]}, "handler": _read}}
