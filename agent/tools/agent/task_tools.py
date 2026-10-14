"""任务工具：后台作业管理与隔离子代理，自动注册；子代理默认只读，
整棵委托树共享三个子任务与联网额度（见 runner 的 tree 预算盒），最多两层。"""
import json
from config_data import SYSTEM_PROMPT
from agent import jobs
from .._run_state import state


def _delegate(args):
    ctx = state() or {}
    depth = int(ctx.get("depth", 0))
    # 子任务额度按整棵委托树共享（runner 的 run_ctx["tree"]，经 _tree 传给子代理），
    # 否则每层各拿一份新配额，一条消息能派出十几个代理、几十次联网。
    tree = ctx.get("tree") or ctx
    if tree.get("delegations", 0) >= 3:
        return "整个任务树最多派三个子任务，先整理已有结论。", {"ok": False}
    if depth >= 2:
        return "子代理最多两层，别套娃了。", {"ok": False}
    from agent import tools
    from agent.runner import run_agent
    provider = ctx.get("provider")
    if not provider:
        return "当前没有模型上下文。", {"ok": False}
    task = str(args.get("task", "")).strip()
    if not task or len(task) > 8000:
        return "子任务为空或太长。", {"ok": False}
    allowed = [name for name, spec in tools.BUILTIN_TOOLS.items()
               if not spec.get("approval_required")
               # job_output 也排除：否则只读子代理能拉任意后台任务（含其他会话）的完整输出
               and name not in {"job_start", "job_kill", "job_output", "update_plan", "show_plan"}]
    inherited = ctx.get("enabled_tools")
    if inherited is not None:
        allowed = [name for name in allowed if name in inherited]
    child_provider = {k:v for k,v in provider.items() if k != '_usage_tracker'}
    tree["delegations"] = tree.get("delegations", 0) + 1
    answer, meta = run_agent({**child_provider, "_depth": depth + 1, "_session_id": None, "_tree": ctx.get("tree")},
                            [{"role": "system", "content": SYSTEM_PROMPT + "\n当前是隔离的只读子任务。完成指定问题，返回简短结论、证据和缺口。不要写文件。"},
                             {"role": "user", "content": task}], enabled_tools=allowed,
                            max_steps=3, reply_max_tokens=2000, max_total_tokens=20000)
    return answer[:6000], {"ok": not bool(meta.get("error")), "subagent": True, "usage": meta.get("usage")}


def _start(args):
    ctx = state() or {}
    if ctx.get("depth", 0):
        return "子代理不能再挂后台任务。", {"ok": False}
    provider = ctx.get('provider') or {}
    row = jobs.start(args.get("message"), provider.get("id"), provider.get('model'), max_steps=4,
                     enabled_tools=ctx.get('enabled_tools'), kb_slugs=ctx.get('kb_slugs'), limits=ctx.get('limits'),skill_ids=ctx.get('skill_ids',[]),developer_grant=ctx.get('developer_grant'))
    return json.dumps(row, ensure_ascii=False), {"ok": True, "job_id": row["id"]}


def _output(args):
    row = jobs.output(args.get("id"))
    return json.dumps(row, ensure_ascii=False), {"ok": True, "job_id": row["id"]}


def _kill(args):
    row = jobs.kill(args.get("id"))
    return row["message"], row


def spec(description, prop, handler):
    return {"description": description, "parameters": {"type":"object", "properties":{prop:{"type":"string"}}, "required":[prop]}, "handler":handler}


TOOLS = {
    "delegate_task": spec("派一个隔离只读子代理查证具体问题，只返回摘要与证据；最多两层，不共享对话历史。", "task", _delegate),
    "job_start": spec("把独立长任务放后台执行，返回任务 id；任务不受当前聊天断连影响。", "message", _start),
    "job_output": spec("查询后台任务的状态和输出。", "id", _output),
    "job_kill": spec("请求取消后台任务；正在运行的工具会在返回后停止。", "id", _kill),
}
TOOLS["job_start"].update(approval_required=True, risks=["background", "model"])
