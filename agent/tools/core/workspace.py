"""
agent/tools/workspace.py - 工作区文件工具（沙箱读写，fs_list / fs_read / fs_write / fs_delete）

【这个文件干什么】
    让 Agent 不只「只能写知识库文本文件」，而是能在一个**受限的工作区目录**里
    读写普通文件（存草稿、整理笔记、把搜索结果的要点落成 md、给用户留一份可下载的产出）。
    这是把「只会聊」变成「能干活」的关键一步，但边界必须封死：
      - 只能碰 data/workspace/ 这一个目录（用户数据目录，gitignore，不污染仓库）；
      - 拒绝路径穿越（../、绝对路径、盘符切换、符号链接逃逸）；
      - 写/删动作必须过 log_change 留痕审计。

【路径沙箱原理】
    1. 传入的必须是「相对 workspace 根」的路径；
    2. 用 resolve(strict=False) 展开（去掉 . / .. / 符号链接），再检查展开后是否仍
       位于 workspace 根之内——任何逃逸直接拒绝；
    3. 目录不存在时 fs_write 会自动创建；fs_delete 只允许删「单文件或空目录」，
       拒绝递归删目录（防模型手滑把整个工作区删了）。

【谁在调用】模型 → 工具注册表 → 本文件底部的 TOOLS（自动发现）。
"""

from __future__ import annotations

import logging
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Dict

from config_data import BASE_DIR
from ..agent.self_manage import log_change
from agent import workspace_history

logger = logging.getLogger(__name__)

WORKSPACE_DIR = Path(BASE_DIR) / "data" / "workspace"
# 单次读/写进上下文的字符上限：超长时截断，避免一次工具输出把 prompt 撑爆
MAX_FILE_CHARS = 10_000
MAX_WRITE_BYTES = 1024 * 1024
MAX_WORKSPACE_BYTES = 50 * 1024 * 1024
_IO_LOCK = threading.RLock()


def _locked(handler):
    """把配额检查与磁盘修改锁在同一事务区间，避免并发写入一起越过配额。"""
    @wraps(handler)
    def execute(args):
        with _IO_LOCK:
            return handler(args)
    return execute


def _safe_resolve(rel_path: str) -> Path:
    """
    把「相对 workspace 的路径」解析为绝对路径；任何逃逸（../、绝对路径、符号链接）
    都会抛 ValueError。这是沙箱的唯一门禁，所有工具先过这一关再动手。
    """
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw or raw in (".", "./"):
        # 空路径 = 工作区根目录（fs_list 的默认参数）；读写/删除由调用方再拦截
        return WORKSPACE_DIR.resolve()
    if raw.startswith("/") or ":" in raw.split("/")[0]:
        raise ValueError("不允许使用绝对路径，请用相对工作区的路径")
    target = (WORKSPACE_DIR / raw).resolve(strict=False)
    if not target.is_relative_to(WORKSPACE_DIR.resolve()):
        raise ValueError("路径逃逸出工作区，被拦下了（不许用 .. 或符号链接出界）")
    return target


def _tool_fs_list(args: Dict[str, Any]):
    rel = str(args.get("path") or "").strip()
    try:
        target = _safe_resolve(rel)
    except ValueError as e:
        return str(e), {"ok": False}
    if not target.exists():
        return f"工作区里没有 {rel or '.'} 这个目录。", {"ok": True, "entries": []}
    if target.is_file():
        return f"{rel} 是一个文件，不是目录。", {"ok": True, "entries": []}
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else 0
            entries.append({"name": child.name, "kind": kind, "size": size})
    except PermissionError as e:
        return f"读取目录失败：{e}", {"ok": False}
    lines = [f"工作区目录 {rel or '.'} 的内容（{len(entries)} 项）："]
    lines += [f"- {e['name']}" + (f"（{e['size']}B）" if e["kind"] == "file" else "（目录）") for e in entries]
    return "\n".join(lines) or "（空目录）", {"ok": True, "entries": entries, "workspace": str(WORKSPACE_DIR)}


def _tool_fs_read(args: Dict[str, Any]):
    rel = str(args.get("path") or "").strip()
    try:
        target = _safe_resolve(rel)
    except ValueError as e:
        return str(e), {"ok": False}
    if not target.exists() or target.is_dir():
        return f"工作区里没有 {rel} 这个文件。", {"ok": False}
    if target.stat().st_size > MAX_WRITE_BYTES:
        return "文件超过 1 MiB，请拆分后读取。", {"ok": False}
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 不是 UTF-8 文本（二进制/别的编码）→ 明确拒绝，不让模型瞎猜
        return f"{rel} 不是 UTF-8 文本文件（可能是二进制或其它编码），读不了。", {"ok": False}
    except Exception as e:
        return f"读取失败：{e}", {"ok": False}
    return f"{rel}（{target.stat().st_size} 字节）：\n{text}", {"ok": True, "filename": rel}


@_locked
def _tool_fs_write(args: Dict[str, Any]):
    rel = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    try:
        target = _safe_resolve(rel)
    except ValueError as e:
        return str(e), {"ok": False}
    encoded_size = len(content.encode("utf-8"))
    if target == WORKSPACE_DIR.resolve() or encoded_size > MAX_WRITE_BYTES:
        return "写入超过 1 MiB 或目标是根目录，已拦截。", {"ok": False}
    current = sum(p.stat().st_size for p in WORKSPACE_DIR.rglob("*") if p.is_file()) if WORKSPACE_DIR.exists() else 0
    previous = target.stat().st_size if target.exists() and target.is_file() else 0
    if args.get("append") and previous + encoded_size > MAX_WRITE_BYTES:
        return "追加后的文件超过 1 MiB，已拦截。", {"ok": False}
    added = encoded_size if args.get("append") else encoded_size - previous
    if current + added > MAX_WORKSPACE_BYTES:
        return "工作区超过 50 MiB 配额，先清理再写。", {"ok": False}
    before = workspace_history.snapshot(target)
    # 先验证历史配额，再修改文件；记录失败不能把修改伪装成可回滚。
    workspace_history.reserve(len(before['data']) + (encoded_size + (previous if args.get('append') else 0))*2 + 2048)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.get("append"):
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"写入失败：{e}", {"ok": False}
    log_change("workspace_write", {"path": rel, "append": bool(args.get("append")), "chars": len(content)})
    change_id = workspace_history.record(rel, before, workspace_history.snapshot(target))
    return f"已写入工作区：{rel}（{len(content)} 字符）", {"ok": True, "filename": rel, "chars": len(content), "change_id": change_id}


@_locked
def _tool_fs_delete(args: Dict[str, Any]):
    rel = str(args.get("path") or "").strip()
    try:
        target = _safe_resolve(rel)
    except ValueError as e:
        return str(e), {"ok": False}
    if target == WORKSPACE_DIR.resolve():
        return "不能删除工作区根目录本身。", {"ok": False}
    if not target.exists():
        return f"工作区里没有 {rel}。", {"ok": False}
    before = workspace_history.snapshot(target)
    workspace_history.reserve(len(before['data']) + 2048)
    try:
        if target.is_dir():
            if any(target.iterdir()):
                return f"{rel} 是非空目录，不能用这个工具批量删；请先逐个删除里面的文件。", {"ok": False}
            target.rmdir()
        else:
            target.unlink()
    except Exception as e:
        return f"删除失败：{e}", {"ok": False}
    log_change("workspace_delete", {"path": rel})
    change_id = workspace_history.record(rel, before, workspace_history.snapshot(target))
    return f"已删除：{rel}", {"ok": True, "filename": rel, "change_id": change_id}


TOOLS = {
    "fs_list": {
        "description": "列出工作区目录（data/workspace/，路径一律写相对路径）下的文件与子目录。"
                       "先看工作区里有什么，再决定读哪个文件。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要列出的相对目录路径，留空 = 工作区根目录"}},
        },
        "handler": _tool_fs_list,
    },
    "fs_read": {
        "description": "读取工作区里一个文本文件的内容（UTF-8），供已被授权的工作区文件参考或引用。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要读取的相对路径，例如 notes/plan.md"}},
            "required": ["path"],
        },
        "handler": _tool_fs_read,
    },
    "fs_write": {
        "description": "在工作区里写入或追加一个文本文件（UTF-8；父目录不存在会自动创建）。"
                       "用于保存整理好的资料、草稿、计划、以及用户明确要求的产出物。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的相对路径，例如 notes/summary.md"},
                "content": {"type": "string", "description": "文件内容（会覆盖已有内容）"},
                "append": {"type": "boolean", "description": "true = 追加到文件末尾，false/省略 = 覆盖写"},
            },
            "required": ["path", "content"],
        },
        "approval_required": True,  # 写文件是"改变磁盘状态"的高危操作，执行前需用户允许
        "handler": _tool_fs_write,
    },
    "fs_delete": {
        "description": "删除工作区里的一个文件或空目录。**高风险**：删除前先跟用户确认这确实是不要的东西；"
                       "非空目录会拒绝删除。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要删除的相对路径"}},
            "required": ["path"],
        },
        "approval_required": True,  # 删除不可逆，必须用户显式允许
        "handler": _tool_fs_delete,
    },
}
