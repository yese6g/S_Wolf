"""
长期会话记忆存储服务 - SilverWolf
基于 SQLite 实现的会话和消息持久化管理。
提供会话的增删改查、消息存储、历史回溯等功能。
"""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

from config_data import DB_PATH, MAX_HISTORY


# ── 数据库上下文管理器 ──

@contextmanager
def get_db():
    """获取 SQLite 数据库连接的上下文管理器。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ── 数据库初始化 ──

def init_db() -> None:
    """初始化数据库表结构（幂等操作）。"""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        # v2.0.0：给消息附加元信息（token 用量 / 工具调用 / 搜索来源 / RAG 命中），
        # 这样刷新或重新打开会话时，气泡底部的「本轮总结」依然能看到。
        # SQLite 不支持 IF NOT EXISTS 加列，用 try/except 做幂等迁移。
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # v2.0.4：会话级「历史压缩摘要」——当上下文超出预算时，
        # 把被裁掉的旧消息压缩成一段摘要存这里，下次对话作为 system 消息带回，
        # 既控制 token，又不丢关键信息。
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass
        # v0.8.1：会话附件引用（用于删会话时清理 data/uploads 里的文件）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_session_path "
            "ON attachments(session_id, path)"
        )
        conn.commit()


# ── 会话管理 ──

def create_session(title: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """创建新会话，返回会话信息。"""
    session_id = session_id or str(uuid.uuid4())
    now = int(time.time())
    title = title or "New Chat"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now)
        )
        conn.commit()
    return {"id": session_id, "title": title, "created_at": now}


def list_sessions() -> List[Dict[str, Any]]:
    """获取所有会话列表，按更新时间倒序。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取单个会话详情及其所有消息。"""
    with get_db() as conn:
        session = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,)
        ).fetchone()
        if not session:
            return None
        messages = conn.execute(
            "SELECT id, role, content, created_at, meta FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,)
        ).fetchall()
    import json as _json
    parsed_messages = []
    for m in messages:
        item = dict(m)
        # meta 以 JSON 文本存储，读出来解析成对象供前端使用（解析失败就当没有）
        if item.get("meta"):
            try:
                item["meta"] = _json.loads(item["meta"])
            except Exception:
                item["meta"] = None
        parsed_messages.append(item)
    return {
        "id": session["id"],
        "title": session["title"],
        "created_at": session["created_at"],
        "messages": parsed_messages
    }


def get_session_summary(session_id: str) -> str:
    """读取会话的历史压缩摘要（没有就返回空串）。"""
    with get_db() as conn:
        row = conn.execute("SELECT summary FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return (row["summary"] or "") if row else ""


def set_session_summary(session_id: str, summary: str) -> None:
    """写入/更新会话的历史压缩摘要。"""
    with get_db() as conn:
        conn.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary or "", session_id))
        conn.commit()


def rename_session(session_id: str, title: str) -> bool:
    """重命名会话，返回是否成功。"""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?",
            (title, session_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_session(session_id: str) -> bool:
    """删除会话（级联删除消息），返回是否成功。"""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0


# ── 附件引用管理（v0.8.1） ──

def link_attachments(session_id: str, refs: list) -> None:
    """把会话与附件路径关联起来（同一会话重复引用同一文件只记一次）。"""
    now = int(time.time())
    with get_db() as conn:
        for ref in refs:
            conn.execute(
                "INSERT OR IGNORE INTO attachments (session_id, path, name, size, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    ref.get("path", ""),
                    ref.get("name", ""),
                    int(ref.get("size", 0) or 0),
                    ref.get("sha256", ""),
                    now,
                ),
            )
        conn.commit()


def get_attachment_paths(session_id: str) -> List[str]:
    """返回某会话引用过的附件相对路径。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT path FROM attachments WHERE session_id = ?", (session_id,)
        ).fetchall()
    return [r["path"] for r in rows]


def referenced_attachment_paths(paths: List[str]) -> set:
    """返回仍被任意会话引用的路径集合（用于判断物理文件能否删除）。"""
    if not paths:
        return set()
    marks = ",".join("?" * len(paths))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT path FROM attachments WHERE path IN ({marks})", paths
        ).fetchall()
    return {r["path"] for r in rows}


def all_attachment_paths() -> set:
    """返回全部被引用过的附件路径（孤儿清理用）。"""
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT path FROM attachments").fetchall()
    return {r["path"] for r in rows}


# ── 消息管理 ──

def save_message(session_id: str, role: str, content: str) -> None:
    """保存一条消息并更新会话的更新时间。"""
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now)
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id)
        )
        conn.commit()


def save_message_with_meta(session_id: str, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """
    保存一条消息（可选附带 meta）。

    meta 用于前端渲染气泡底部的总结：
      {"usage": {...}, "tools_used": [...], "web_searches": [...], "rag_used": [...]}
    """
    import json as _json
    now = int(time.time())
    meta_text = _json.dumps(meta, ensure_ascii=False) if meta else None
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, meta) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, meta_text)
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()


def build_messages(
    session_id: str,
    user_message: str,
    system_prompt: str,
    assistant_greeting: str,
    max_history: int = MAX_HISTORY,
) -> List[Dict[str, str]]:
    """
    构建发送给 LLM 的完整消息列表。
    包含：系统提示 + 助手开场 + 历史消息 + 当前用户消息。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": assistant_greeting},
    ]
    if session_id:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, max_history)
            ).fetchall()
        history = list(reversed(rows))
        for row in history:
            messages.append({"role": row["role"], "content": row["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


def generate_title(message: str) -> str:
    """根据用户消息生成会话标题（取前6个词）。"""
    words = message.strip().split()[:6]
    return " ".join(words) if words else "New Chat"
