"""后台任务注册表：工具/API 启动独立 Agent，线程池最多四项并发，输出与状态原子落盘。

断开 HTTP 不会停止作业；kill 设置协作取消标志，在模型分片/步骤/审批边界检查。
重启把未完成状态标记 interrupted，保留输出；不自动重跑有副作用的工具。
"""

from concurrent.futures import ThreadPoolExecutor
import copy
import logging
import threading
import time
import uuid
from config_data import BASE_DIR, SYSTEM_PROMPT
from agent import persistence
import provider_config

JOB_DIR = BASE_DIR / "data" / "jobs"
_LOCK = threading.RLock()
_ACTIVE = {}
_POOL = None
_FUTURES = {}
logger = logging.getLogger("audit.tools")

# 只有这些键是 run_agent_stream 认识的显式参数；上下文预算等其他键由 runner 自行决定，
# 整包透传会直接 TypeError（见 changecode 案例）。
_LIMIT_KEYS = {"reply_max_tokens", "tool_output_limit", "max_total_tokens"}


def list_jobs():
    if not JOB_DIR.exists():
        return []
    rows = []
    for path in JOB_DIR.glob("*.json"):
        try:
            row = persistence.load(JOB_DIR, path.stem)
        except (OSError, ValueError) as exc:  # JSONDecodeError 也是 ValueError
            logger.warning("任务文件损坏，已跳过：%s（%s）", path.name, exc)
            continue
        if not isinstance(row, dict):
            logger.warning("任务文件不是对象，已跳过：%s", path.name)
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row.get("created_at", 0), reverse=True)


def output(identity):
    value = persistence.load(JOB_DIR, identity)
    if value is None:
        raise ValueError("任务不存在")
    return value


def start(
    message,
    provider_id,
    model=None,
    max_steps=4,
    *,
    enabled_tools=None,
    kb_slugs=None,
    limits=None,
    skill_ids=None,
    developer_grant=None
):
    """启动作业，返回安全元数据；凭据只存在 worker 内存中，不写任务 JSON。"""
    global _POOL
    message = str(message or "").strip()
    if not message or len(message) > 16000:
        raise ValueError("任务描述为空或超过 16000 字符")
    provider = provider_config.get_provider(provider_id)
    if not provider:
        raise ValueError("提供商不存在")
    if model:
        provider["model"] = str(model)
    if developer_grant:
        from agent.developer import require

        require(developer_grant)
        provider["_dev_grant"] = developer_grant
    with _LOCK:
        if len(_ACTIVE) >= 8:
            raise ValueError("任务槽位已满（最多 8 项待完成）")
        history = list_jobs()
        for old in reversed(history):
            if len(history) < 100:
                break
            if old["id"] not in _ACTIVE and old.get("status") not in {
                "running",
                "queued",
            }:
                persistence.location(JOB_DIR, old["id"]).unlink(missing_ok=True)
                history.remove(old)
        if len(history) >= 100:
            raise ValueError("任务历史已满，先删除已结束任务")
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sw-job")
        identity = "job_" + uuid.uuid4().hex
        cancel = threading.Event()
        _ACTIVE[identity] = cancel
        from agent import tools

        allowed = [
            s["function"]["name"] for s in tools.openai_tool_schemas(enabled_tools)
        ]
        row = {
            "id": identity,
            "message": message,
            "provider_id": provider_id,
            "model": provider.get("model"),
            "scope": {
                "tools": allowed,
                "kb_slugs": list(kb_slugs or []),
                "limits": {
                    k: v for k, v in (limits or {}).items() if k in _LIMIT_KEYS
                },
                "skills": list(
                    skill_ids
                    if skill_ids is not None
                    else persistence.load(BASE_DIR / "data", "ui_state", {}).get(
                        "enabled_skills", []
                    )
                ),
            },
            "status": "queued",
            "created_at": time.time(),
            "events": [],
            "text": "",
        }
        persistence.save(JOB_DIR, identity, row)
        from agent.runner import MAX_STEPS_LIMIT  # 局部导入避免 runner→tools→jobs 循环
        _FUTURES[identity] = _POOL.submit(
            _work, row, provider, cancel, max(1, min(int(max_steps), MAX_STEPS_LIMIT))
        )
        logger.info("后台任务登记 job=%s", identity)
    return {k: v for k, v in row.items() if k != "events"}


def _work(row, provider, cancel, steps):
    from agent.runner import run_agent_stream

    row["status"] = "running"
    source = None
    try:
        scope = row.get("scope", {})
        source = run_agent_stream(
            {
                **provider,
                "_cancel_event": cancel,
                "_kb_slugs": scope.get("kb_slugs", []),
                "_skill_ids": scope.get("skills", []),
            },
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": row["message"]},
            ],
            enabled_tools=scope.get("tools", []),
            max_steps=steps,
            **scope.get("limits", {})
        )
        for event in source:
            if cancel.is_set():
                break
            if event["type"] == "approval_wait":
                cancel.wait(0.1)
                continue
            with _LOCK:
                if event["type"] == "content":
                    row["text"] = (row["text"] + event["text"])[-200000:]
                elif event["type"] == "demote" and row["text"].endswith(event["text"]):
                    row["text"] = row["text"][: -len(event["text"])]
                elif event["type"] == "done":
                    row["meta"] = event["meta"]
                row["events"] = (row["events"] + [copy.deepcopy(event)])[-500:]
                persistence.save(JOB_DIR, row["id"], row)
        row["status"] = (
            "cancelled"
            if cancel.is_set()
            else "failed" if row.get("meta", {}).get("error") else "done"
        )
    except Exception as exc:
        logger.exception("后台任务失败 job=%s", row["id"])
        row.update(status="failed", error=str(exc)[:300])
    finally:
        if source:
            source.close()
        row["finished_at"] = time.time()
        persistence.save(JOB_DIR, row["id"], row)
        logger.info("后台任务结束 job=%s status=%s", row["id"], row["status"])
        with _LOCK:
            _ACTIVE.pop(row["id"], None)
            _FUTURES.pop(row["id"], None)


def kill(identity):
    with _LOCK:
        if identity in _ACTIVE:
            _ACTIVE[identity].set()
            logger.info("后台任务取消 job=%s", identity)
            return {"ok": True, "message": "取消已登记；等待当前模型或工具返回。"}
    return {"ok": False, "message": "任务已结束或重启后中断。"}


def delete(identity):
    with _LOCK:
        row = output(identity)
        if identity in _ACTIVE or row.get("status") in {"running", "queued"}:
            raise ValueError("先停止任务并等待结束，再删除记录")
        persistence.location(JOB_DIR, identity).unlink(missing_ok=True)
        logger.info("删除已结束任务 job=%s", identity)
        return {"ok": True}


def recover():
    for row in list_jobs():
        if row["status"] in {"queued", "running"}:
            row["status"] = "interrupted"
            persistence.save(JOB_DIR, row["id"], row)


def shutdown():
    global _POOL
    with _LOCK:
        for event in _ACTIVE.values():
            event.set()
        for identity, future in list(_FUTURES.items()):
            if future.cancel():
                row = output(identity)
                row["status"] = "cancelled"
                persistence.save(JOB_DIR, identity, row)
                _ACTIVE.pop(identity, None)
                _FUTURES.pop(identity, None)
        if _POOL:
            _POOL.shutdown(wait=False, cancel_futures=True)
            _POOL = None
