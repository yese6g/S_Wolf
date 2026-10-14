"""
agent/tools/memory.py - 长期记忆工具（remember / recall / forget）

【这个文件干什么】
    让 Agent 拥有「跨会话的长期记忆」。现在最大的失忆点就是：会话之间什么都不记得，
    middleware/context.py 的摘要只服务当前会话，换个会话就从头开始。
    这里给三个工具：
      - remember  记住一条信息（用户偏好 / 任务约定 / 重要事实，可以带 tags）
      - recall    按关键词匹配找回与当前话题相关的记忆（每次新对话或话题变深时调用）
      - forget    忘记某一条记忆（按 id，用户明确说"忘掉 XX"时用）

【存储】
    data/memory.json，结构：
        {"items": [{"id": "m_xxx", "content": "...", "tags": "...", "ts": 1234567890, "source": "agent"}]}
    纯本地明文，用户可在「设置 → 记忆」里查看全部条目并删除（读同一个存储）。

【recall 为什么不用向量检索】
    长期记忆条目通常很短（一两句话），用相邻双字 bigram 重叠打分零依赖、够快够准；
    真要语义检索，项目里已有 RAG 知识库那条链路，不必在这个轻量工具上加重磅依赖。

【谁在调用】
    - 模型 → 工具注册表 → 本文件底部的 TOOLS（自动发现，无需改注册表）；
    - SilverWolf.py 的 /api/agent/memory 接口（设置页「记忆」标签页的展示/删除）也走这里的
      load_memories / add_memory / delete_memory / clear_memories，保证读写逻辑只有一份。

【自我修改边界】
    只能写 data/memory.json 一个文件；所有写动作都过 self_manage.log_change() 留痕审计。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from config_data import BASE_DIR
from ..agent.self_manage import log_change
from agent import persistence

logger = logging.getLogger(__name__)

MEMORY_FILE = Path(BASE_DIR) / "data" / "memory.json"
# recall 最多返回几条相关记忆（一次性塞太多进上下文是白烧 token）
MAX_RECALL_RESULTS = 5


# ── 存储读写（与接口层共用） ──

def load_memories() -> List[Dict[str, Any]]:
    """读取全部记忆条目；文件不存在或损坏时返回空列表（不自动建脏文件）。"""
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        items = data.get("items", data) if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except Exception as e:
        logger.warning("读取记忆文件失败: %s", e)
        return []


def _save_memories(items: List[Dict[str, Any]]) -> None:
    """原子写入 data/memory.json（先写临时文件再替换，避免写坏半截）。"""
    persistence.save(MEMORY_FILE.parent,MEMORY_FILE.stem,{'items':items})


@persistence.transaction
def add_memory(content: str, tags: str = "", source: str = "agent") -> Dict[str, Any]:
    """新增一条记忆；返回 {"ok", "entry"}。供工具与手动添加接口共用。"""
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "记忆内容不能为空。"}
    entry = {
        "id": f"m_{int(time.time())}_{random.randrange(1000, 9999)}",
        "content": content[:2000],           # 单条记忆长度上限，防模型把整段对话怼进来
        "tags": (tags or "").strip()[:200],
        "ts": int(time.time()),
        "source": source,
    }
    items = load_memories()
    items.append(entry)
    _save_memories(items)
    log_change("memory_remember", {"id": entry["id"], "content": content[:100]})
    return {"ok": True, "entry": entry}


@persistence.transaction
def delete_memory(mem_id: str) -> Dict[str, Any]:
    """按 id 删除一条记忆；返回 {"ok"} 或 {"ok": False, "error"}。"""
    mem_id = (mem_id or "").strip()
    if not mem_id:
        return {"ok": False, "error": "缺少记忆 id。"}
    items = load_memories()
    remaining = [it for it in items if it.get("id") != mem_id]
    if len(remaining) == len(items):
        return {"ok": False, "error": f"没有找到记忆 {mem_id}。"}
    _save_memories(remaining)
    log_change("memory_forget", {"id": mem_id, "content": next((it.get("content", "")[:100] for it in items if it.get("id") == mem_id), "")})
    return {"ok": True}


@persistence.transaction
def clear_memories() -> int:
    """清空全部记忆，返回删除条数（设置页「清空全部」用）。"""
    items = load_memories()
    _save_memories([])
    log_change("memory_clear", {"count": len(items)})
    return len(items)


# ── recall 的关键词匹配（零依赖） ──

def _content_terms(text: str) -> set:
    """把文本切成本地「检索词」：英文/数字按单词切，中文按相邻双字切 bigram。"""
    text = (text or "").lower()
    terms = set(re.findall(r"[a-z0-9]+", text))
    for han in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(han[i:i + 2] for i in range(max(0, len(han) - 1)))
    return terms


def _match_score(query: str, content: str) -> float:
    """
    相关度打分：query 的检索词在 content 里命中多少个（占比）+ 长词命中加一点奖励。
    中文天然无空格分词，1gram 太噪、整句子串太严，2gram 是成本最低的折中。
    """
    q_terms = _content_terms(query)
    if not q_terms:
        return 0.0
    content_lower = (content or "").lower()
    hits = [t for t in q_terms if t in content_lower]
    if not hits:
        return 0.0
    base = len(hits) / len(q_terms)                              # 命中占比（0~1）
    bonus = min(1.0, max(len(t) for t in hits) / 8.0) * 0.15     # 更长命中词更可信
    return base + bonus


def search_memories(query: str, limit: int = MAX_RECALL_RESULTS) -> List[Dict[str, Any]]:
    """按相关度返回与 query 匹配的记忆条目（从高到低），无命中返回空列表。"""
    query = (query or "").strip()
    if not query:
        return []
    scored = [(it, _match_score(query, it.get("content", ""))) for it in load_memories()]
    scored = [pair for pair in scored if pair[1] > 0]
    scored.sort(key=lambda x: -x[1])
    return [it for it, _ in scored[:limit]]


# ── 三个工具 ──

def _tool_remember(args: Dict[str, Any]):
    content = str(args.get("content", ""))
    tags = str(args.get("tags", "") or "")
    result = add_memory(content, tags, source="agent")
    if not result.get("ok"):
        return result["error"], {"ok": False}
    entry = result["entry"]
    return (
        f"已记住：{entry['content'][:120]}\n"
        f"（记忆 id：{entry['id']}，下次对话也可以用「忘掉这条」来删除它）",
        {"ok": True, "id": entry["id"], "content": entry["content"]},
    )


def _tool_recall(args: Dict[str, Any]):
    query = str(args.get("query", "")).strip()
    if not query:
        return "请你提供要检索什么（比如用户名、任务、前缀）。", {"ok": True, "matches": []}
    matches = search_memories(query)
    if not matches:
        return "没有找到相关记忆。", {"ok": True, "matches": []}
    lines = ["相关记忆（从高到低相关度）："]
    for i, it in enumerate(matches, 1):
        ts = time.strftime("%Y-%m-%d", time.localtime(it.get("ts", 0)))
        lines.append(f"{i}. [{ts}] {it.get('content', '')}（id: {it.get('id')}）")
    return "\n".join(lines), {"ok": True, "matches": matches}


def _tool_forget(args: Dict[str, Any]):
    mem_id = str(args.get("id", "")).strip()
    if not mem_id:
        return "需要提供要删除的记忆 id（remember/recall 时会给）。", {"ok": False}
    result = delete_memory(mem_id)
    if not result.get("ok"):
        return result.get("error", "删除失败"), {"ok": False}
    return f"已忘记记忆 {mem_id}。", {"ok": True, "id": mem_id}


TOOLS = {
    "remember": {
        "description": "把一条重要信息记入长期记忆（跨会话有效），比如用户的偏好、习惯、任务约定、"
                       "已经拍板的事实。当用户说了「记下来 / 别忘了 / 以后都这样」这类话，"
                       "或透露了今后都要用到的信息时用它；tags 可给关键词便于日后检索。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容（简洁、完整，别再让下个会话猜）"},
                "tags": {"type": "string", "description": "可选关键词（逗号分隔），便于 recall 命中"},
            },
            "required": ["content"],
        },
        "handler": _tool_remember,
    },
    "recall": {
        "description": "检索长期记忆：找出与给定话题相关的、以前记住的信息。"
                       "每次对话开始、或用户问题涉及“以前/上次/之前说过”时先调用它，"
                       "别装作还记得。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "要检索的话题或关键词"}},
            "required": ["query"],
        },
        "handler": _tool_recall,
    },
    "forget": {
        "description": "删除一条长期记忆（按 id，来自 remember 或 recall 的返回）。"
                       "用户明确要求「忘掉 / 删除这条」时使用。",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "要删除的记忆 id"}},
            "required": ["id"],
        },
        "handler": _tool_forget,
    },
}
