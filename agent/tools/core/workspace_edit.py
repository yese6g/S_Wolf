"""工作区精确编辑与变更查询：复用写入审批、配额和版本账本，不允许整仓任意路径。"""

import json
from agent import workspace_history
from . import workspace
from agent import developer
from .._run_state import state


def _edit(args):
    with workspace._IO_LOCK:
        target = workspace._safe_resolve(str(args.get("path", "")))
        if not target.is_file() or target.stat().st_size > workspace.MAX_WRITE_BYTES:
            return "目标不是可编辑的小文本文件。", {"ok": False}
        text = target.read_text(encoding="utf-8")
        old = str(args.get("old", ""))
        if not old or text.count(old) != 1:
            return "匹配必须唯一。先读文件，给出更精确的 old。", {"ok": False}
        return workspace._tool_fs_write(
            {
                "path": args["path"],
                "content": text.replace(old, str(args.get("new", "")), 1),
            }
        )


def _diff(args):
    row = workspace_history.diff(args.get("id"))
    return row["diff"], {"ok": True, "change_id": row["id"]}


def _rollback(args):
    row = workspace_history.rollback(args.get("id"))
    return json.dumps(row, ensure_ascii=False), row


TOOLS = {
    "fs_edit": {
        "description": "精确替换工作区文本的一处内容；old 必须唯一，先 fs_read 再编辑。",
        "parameters": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("path", "old", "new")},
            "required": ["path", "old", "new"],
        },
        "handler": _edit,
    },
    "fs_diff": {
        "description": "查看工作区 change_id 对应的前后差异。",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": _diff,
    },
    "fs_rollback": {
        "description": "回滚一次工作区修改；若文件后来被改过则拒绝，需批准。",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": _rollback,
    },
}


def project(action):
    """工作区接口复用开发者模块；模型不能自己生成、切换或扩大授权。"""

    def execute(args):
        key = (state() or {}).get("developer_grant")
        developer.require(key)
        if action == "list":
            data = developer.files(key, args.get("path", ""))
        elif action == "read":
            data = developer.read(key, args.get("path", ""))
            from middleware.logging_system import redact

            if redact(data["content"]) != data["content"]:
                return "源码中含凭据，不能送入模型。请在本机项目编辑器中处理。", {
                    "ok": False,
                    "path": data["path"],
                }
        elif action == "write":
            data = developer.write(
                key, args.get("path", ""), args.get("content", ""), args.get("revision")
            )
        elif action == "rollback":
            data = developer.rollback(key, args.get("id"))
        elif action == "command":
            data = developer.start_command(key, args.get("argv"), approved=True)
        return json.dumps(data, ensure_ascii=False), {
            "ok": True,
            **{k: v for k, v in data.items() if k not in {"content", "text"}},
        }

    return execute


for name, action, description, properties, required in (
    (
        "project_list",
        "list",
        "列出已授权工作区的源码目录。只在Lv.999工作区授权有效时可用。",
        {"path": {"type": "string"}},
        [],
    ),
    (
        "project_read",
        "read",
        "读取已授权工作区源码和revision，修改前先读文件。",
        {"path": {"type": "string"}},
        ["path"],
    ),
    (
        "project_write",
        "write",
        "按revision写入已授权工作区源码；新文件revision=absent。保留差异和冲突检查，需审批。",
        {k: {"type": "string"} for k in ("path", "content", "revision")},
        ["path", "content", "revision"],
    ),
    (
        "project_rollback",
        "rollback",
        "回滚已授权工作区的一次change_id；后来改过则拒绝。",
        {"id": {"type": "string"}},
        ["id"],
    ),
    (
        "project_command",
        "command",
        "在已授权工作区运行命令并返回后台job_id；逐条审批，最长5分钟，没有操作系统沙箱。",
        {"argv": {"type": "array", "items": {"type": "string"}}},
        ["argv"],
    ),
):
    TOOLS[name] = {
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "handler": project(action),
        "approval_required": action in {"write", "rollback", "command"},
        "risks": ["project"] + (["process"] if action == "command" else []),
    }
