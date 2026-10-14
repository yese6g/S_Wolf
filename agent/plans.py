"""会话计划存储：runner 按 session_id 恢复，plan 工具更新后原子保存；任务子上下文隔离。"""
from config_data import BASE_DIR
from agent import persistence

PLAN_DIR = BASE_DIR / "data" / "plans"


def load(session_id):
    return persistence.load(PLAN_DIR, session_id, []) if session_id else []


def save(session_id, steps):
    if session_id:
        persistence.save(PLAN_DIR, session_id, steps)
