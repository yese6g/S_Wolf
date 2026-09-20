"""
agent/tools/plan.py - 任务规划工具（update_plan / finish_plan）

【这个文件干什么】
    让多步任务有「显式进度」：模型在动手前先用 update_plan 列出一份步骤清单，
    每完成一步就更新状态（pending → running → done），前端把清单渲染成可勾选的
    进度条 —— 用户能一眼看出来它在按计划干活，而不是黑箱里瞎转。

【状态机】
    每步状态：pending（未开始）→ running（进行中）→ done（完成）。
    模型可以「全量替换」计划，也可以只更新某一步的状态。

【存储】
    计划存在 agent/tools/_run_state.py 的 `plan` 字段里（每轮运行一个计划盒），
    工具的 meta 会把最新清单带给 runner，由 runner 转发成 SSE 的 `plan` 事件
    给前端渲染 —— 工具本身不碰前端。

【谁在调用】模型 → 工具注册表 → 本文件底部的 TOOLS（自动发现）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._run_state import state

VALID_STATUS = {"pending", "running", "done"}


def _normalize_steps(steps) -> List[Dict[str, Any]]:
    """清洗模型给的计划步骤：只留 id/text/status 三个键，状态非法就归为 pending。"""
    clean: List[Dict[str, Any]] = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        text = str(s.get("text") or s.get("desc") or "").strip()
        if not text:
            continue
        status = str(s.get("status") or "pending").lower()
        if status not in VALID_STATUS:
            status = "pending"
        clean.append({
            "id": str(s.get("id") or i + 1),
            "text": text[:200],
            "status": status,
        })
    return clean


def _plan_text(plan: List[Dict[str, Any]]) -> str:
    """把计划清单格式化成给模型看的文本（便于它记住后续步骤）。"""
    if not plan:
        return "（计划为空）"
    marks = {"pending": "[ ]", "running": "[>]", "done": "[x]"}
    return "\n".join(f"{marks.get(s['status'], '[ ]')} {s['id']}. {s['text']}" for s in plan)


def _tool_update_plan(args: Dict[str, Any]):
    """
    更新任务计划（全量替换或单步状态更新）。steps 每次给全量清单更稳，
    单步更新用 update={"id": ..., "status": ...}。
    """
    ctx = state()
    steps = args.get("steps")
    if ctx is None:
        return "当前不在 Agent 运行上下文里，无法保存计划。", {"ok": True, "plan": []}

    if isinstance(steps, list):
        plan = _normalize_steps(steps)
        ctx["plan"] = plan
        return (
            f"计划已更新（{len(plan)} 步）：\n" + _plan_text(plan),
            {"ok": True, "plan": plan},
        )

    update = args.get("update")
    if isinstance(update, dict) and update.get("id"):
        updated = False
        for s in ctx.get("plan", []):
            if str(s.get("id")) == str(update.get("id")):
                status = str(update.get("status") or "").lower()
                if status in VALID_STATUS:
                    s["status"] = status
                    updated = True
                break
        plan = ctx.get("plan", [])
        if not updated:
            return f"没有计划步骤 {update.get('id')}，先 update_plan 列全量清单。", {"ok": False, "plan": plan}
        return "已更新步骤状态。\n" + _plan_text(plan), {"ok": True, "plan": plan}

    return "update_plan 需要 steps（全量清单）或 update（单步状态）。", {"ok": False}


def _tool_show_plan(args: Dict[str, Any]):
    """查看当前计划（步骤少的时候不需要调用，主要是辅助模型记忆）。"""
    ctx = state()
    plan = (ctx or {}).get("plan", [])
    if not plan:
        return "还没有计划。拿到任务先 update_plan 列步骤，别一头扎进去。", {"ok": True, "plan": []}
    return "当前计划：\n" + _plan_text(plan), {"ok": True, "plan": plan}


TOOLS = {
    "update_plan": {
        "description": "复杂任务开始时，先把执行计划列成步骤清单（id/text/status），"
                       "每完成一步就把它标成 done，需要用户等的步骤标 running。"
                       "这能让用户实时看到进度，也防止你做到一半忘了原本的目标。"
                       "steps 传全量清单（会覆盖旧计划）；想只改某步状态用 update={\"id\":…,\"status\":…}。",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "计划步骤全量清单：[{\"id\":\"1\",\"text\":\"搜资料\",\"status\":\"done\"}]，status 可选 pending/running/done",
                },
                "update": {
                    "type": "object",
                    "description": "单步状态更新：{\"id\":\"2\",\"status\":\"running\"}",
                },
            },
        },
        "handler": _tool_update_plan,
    },
    "show_plan": {
        "description": "查看当前任务计划清单（回忆自己列过哪些步骤、做到哪一步了）。",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_show_plan,
    },
}