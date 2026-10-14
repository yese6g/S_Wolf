"""
agent/tools/knowledge.py - RAG 知识库工具（检索 + 增删改）

【这个文件干什么】
    让 Agent 能像查笔记一样使用本地知识库：
      - rag_search：按问题检索相关片段（回答需要引用本地资料时用）；
      - rag_list_files / rag_list_knowledge_bases：看有哪些文件/知识库；
      - rag_write_file / rag_delete_file：把整理好的资料写进知识库、或删掉（会重新向量化）。

【为什么这 5 个工具放在一个文件里】
    它们同属一个功能（知识库维护），拆成 5 个文件反而更难对照阅读。
    本目录的组织原则是「按功能分文件」——一个文件里的多个工具共享同一批依赖与约定。

【谁在调用】模型 → 注册表 → 本文件底部的 TOOLS。

【自我修改的边界（重要）】
    Agent 只能写 knowledge_bases/<kb>/files/ 下的文本文件，不能改代码或配置；
    每次写入/删除都会通过 log_change() 记到 data/logs/changes.log 留痕。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from knowledge_base import (
    delete_kb_file,
    list_kb_files,
    list_knowledge_bases,
    save_kb_file,
)
from rag import build_rag_prompt, selected_kbs

from .. import _run_state as run_state
from .fetch_page import fetch_page
from ..agent.self_manage import log_change


def _scope(args, multiple=False):
    """模型只能访问设置中启用的库；无指定时查询全部，写入选第一项。"""
    ctx = run_state.state() or {}
    current = selected_kbs()
    configured = ctx.get("kb_slugs")
    enabled = current if configured is None else [s for s in configured if s in current]
    slug = args.get("kb_slug")
    if slug and slug not in enabled:
        raise ValueError("这个知识库没开启。去设置勾选后再用。")
    if not enabled:
        raise ValueError("知识库已关闭")
    if multiple:
        return [slug] if slug else enabled
    return slug or enabled[0]


def _tool_rag_search(args: Dict[str, Any]):
    query = str(args.get("query", "")).strip()
    libraries = _scope(args, multiple=True)
    result = build_rag_prompt(query, libraries)
    context, sources = result["context_text"], result["sources"]
    kb_slug = ", ".join(libraries)
    if not context:
        return f"知识库 {kb_slug} 未检索到相关内容。", {"ok": True, "kb_slug": kb_slug, "sources": []}
    return f"知识库 {kb_slug} 检索结果：\n{context}", {"ok": True, "kb_slug": kb_slug, "sources": sources}


def _tool_rag_list_files(args: Dict[str, Any]):
    kb_slug = _scope(args)
    files = list_kb_files(kb_slug)
    if not files:
        return f"知识库 {kb_slug} 目前没有持久化文件。", {"ok": True, "kb_slug": kb_slug, "files": []}
    lines = [f"{f['filename']}（{f['size']} 字节）" for f in files]
    return f"知识库 {kb_slug} 的文件：\n" + "\n".join(lines), {"ok": True, "kb_slug": kb_slug, "files": files}


def _tool_rag_write_file(args: Dict[str, Any]):
    kb_slug = _scope(args)
    filename = str(args.get("filename", "")).strip()
    content = str(args.get("content", ""))
    result = save_kb_file(kb_slug, filename, content)
    if not result.get("success"):
        return f"写入 RAG 文件失败：{result.get('error')}", {"ok": False, "filename": filename}
    log_change("rag_write_file", {"kb_slug": kb_slug, "filename": filename, "chunks": result.get("chunks")})
    return (
        f"已写入并入库：{filename}（分块 {result.get('chunks')}，替换旧分块 {result.get('replaced_chunks')}）",
        {"ok": True, "kb_slug": kb_slug, "filename": filename, "chunks": result.get("chunks")},
    )


def _tool_rag_delete_file(args: Dict[str, Any]):
    kb_slug = _scope(args)
    filename = str(args.get("filename", "")).strip()
    result = delete_kb_file(kb_slug, filename)
    if not result.get("success"):
        return f"删除 RAG 文件失败：{result.get('error')}", {"ok": False, "filename": filename}
    log_change("rag_delete_file", {"kb_slug": kb_slug, "filename": filename,
                                   "removed_chunks": result.get("removed_chunks")})
    return (
        f"已删除 RAG 文件 {filename}（移除向量分块 {result.get('removed_chunks')}）",
        {"ok": True, "kb_slug": kb_slug, "filename": filename},
    )


def _tool_rag_list_kbs(args: Dict[str, Any]):
    kbs = [k for k in list_knowledge_bases() if k.get("kb_slug") in selected_kbs()]
    if not kbs:
        return "当前没有知识库。", {"ok": True, "knowledge_bases": []}
    lines = [f"{k.get('kb_slug')}（文档数 {k.get('document_count', 0)}）" for k in kbs]
    return "现有知识库：\n" + "\n".join(lines), {"ok": True, "knowledge_bases": kbs}


# 入库正文上限：网页正文转成 md 后的最大字符数（太大塞进向量库没意义，还会拖慢入库）
RAG_IMPORT_MAX_CHARS = 15000
# 一轮对话里最多导入的次数（配合 _run_state 的 fetches 计数一起夹）
MAX_RAG_IMPORT_PER_RUN = 3


def _tool_rag_import_url(args: Dict[str, Any]):
    """
    抓取一个网页正文，转成 Markdown 存进知识库（自动向量化），以后用 rag_search 就能检索到。
    典型场景：看到一篇好文章/文档页面，想让它成为自己的本地资料。
    """
    url = str(args.get("url", "")).strip()
    if not url:
        return "需要提供 url（要导入的网页链接）。", {"ok": False}

    # 本轮次数限制：导入自带网络抓取成本，别让模型一口气导一打页面
    run = run_state.state()
    if run is not None and run.get("rag_imports", 0) >= MAX_RAG_IMPORT_PER_RUN:
        return (f"本轮已导入 {MAX_RAG_IMPORT_PER_RUN} 个网页，先停一下 —— "
                "把已经导入的资料用起来再说。", {"ok": False, "url": url})

    result = fetch_page(url, max_chars=RAG_IMPORT_MAX_CHARS)
    if not result.get("ok"):
        return f"抓取网页失败：{result.get('error', '未知错误')}", {"ok": False, "url": url}

    title = result.get("title", "") or url
    # 文件名：优先用调用方给的，否则从标题/域名生成；只有文本扩展名能入库
    filename = str(args.get("filename") or "").strip()
    if not filename or Path(filename).suffix.lower() not in {".md", ".txt", ".markdown"}:
        base = re.sub(r'[\\/:*?"<>|\s]+', "_", title)[:40] or "web"
        # 后缀看生成的 base：".md" 这种纯后缀的 filename 本身不能当文件名，标题自带 .md 也不叠双后缀
        filename = base if Path(base).suffix.lower() in {".md", ".txt", ".markdown"} else base + ".md"
    kb_slug = _scope(args)

    content = f"来源：{title}\nURL：{url}\n\n{result['text']}"
    saved = save_kb_file(kb_slug, filename, content)
    if not saved.get("success"):
        return f"导入知识库失败：{saved.get('error')}", {"ok": False, "filename": filename}
    if run is not None:
        run["rag_imports"] = run.get("rag_imports", 0) + 1  # 记入本轮导入次数
    log_change("rag_import_url", {"kb_slug": kb_slug, "filename": filename,
                                  "url": url, "chunks": saved.get("chunks")})
    return (
        f"已把《{title}》导入知识库 {kb_slug}，存为 {filename}（分块 {saved.get('chunks')} 个）。"
        "以后问相关内容时，用 rag_search 就能检索到它。",
        {"ok": True, "kb_slug": kb_slug, "filename": filename, "chunks": saved.get("chunks")},
    )


TOOLS = {
    "rag_search": {
        "description": "在本地知识库中检索相关片段。回答需要引用本地资料时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题"},
                "kb_slug": {"type": "string", "description": "知识库标识，未指定时使用已开启的库"},
            },
            "required": ["query"],
        },
        "handler": _tool_rag_search,
    },
    "rag_import_url": {
        "description": "抓取一个网页的正文，转成 Markdown 存进知识库并自动向量化，"
                       "之后用 rag_search 就能检索到。适合把好的文章/文档页面变成本地资料。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要导入的网页完整链接"},
                "filename": {"type": "string", "description": "可选；入库文件名（.md/.txt），默认按标题生成"},
                "kb_slug": {"type": "string", "description": "目标知识库，未指定时使用已开启的库"},
            },
            "required": ["url"],
        },
        "handler": _tool_rag_import_url,
    },
    "rag_list_files": {
        "description": "列出某个知识库里已保存的文件。",
        "parameters": {"type": "object", "properties": {"kb_slug": {"type": "string"}}},
        "handler": _tool_rag_list_files,
    },
    "rag_write_file": {
        "description": "新增或修改知识库中的文本文件（会自动重新向量化入库）。用于把整理好的资料写进自己的知识库。",
        "parameters": {
            "type": "object",
            "properties": {
                "kb_slug": {"type": "string", "description": "目标知识库，未指定时使用已开启的库"},
                "filename": {"type": "string", "description": "文件名，如 notes.md"},
                "content": {"type": "string", "description": "完整的文件内容（文本）"},
            },
            "required": ["filename", "content"],
        },
        "handler": _tool_rag_write_file,
    },
    "rag_delete_file": {
        "description": "从知识库删除一个文件（同时移除对应向量分块）。",
        "parameters": {
            "type": "object",
            "properties": {
                "kb_slug": {"type": "string"},
                "filename": {"type": "string", "description": "要删除的文件名"},
            },
            "required": ["filename"],
        },
        "handler": _tool_rag_delete_file,
    },
    "rag_list_knowledge_bases": {
        "description": "列出当前所有知识库及其文档数量。",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_rag_list_kbs,
    },
}
