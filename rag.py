"""
RAG 核心服务 - SilverWolf
检索增强生成（Retrieval-Augmented Generation）核心逻辑。
负责：embedding 初始化、向量检索、上下文拼接。

架构：
- embedding 统一在 vector_stores 模块本地计算（离线优先，两级回退）
- 通过 vector_stores 模块进行向量持久化
- 对外暴露 consult_knowledge_base() 接口供主程序调用

v0.4.0 变更：
- embedding 完全本地化，不再依赖 Chroma 默认模型联网下载
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

from config_data import (
    RAG_TOP_K,
    RAG_SIMILARITY_THRESHOLD,
    RAG_CONTEXT_TEMPLATE,
    DEFAULT_KB_SLUG,
)
from vector_stores import (
    get_or_create_collection,
    query_documents,
    get_embedding_backend,
)

logger = logging.getLogger(__name__)

# 内置 hash embedding 的相似度尺度低于 transformer 模型，使用更低的检索阈值
HASH_BACKEND_THRESHOLD = 0.2


def _effective_threshold() -> float:
    """根据当前 embedding 后端返回合适的相似度阈值。"""
    backend = get_embedding_backend()
    if backend.startswith("local-hash"):
        return HASH_BACKEND_THRESHOLD
    return RAG_SIMILARITY_THRESHOLD

# ── 初始化 embedding 并注入到 collection ──

def init_knowledge_base_embedding(kb_slug: str = DEFAULT_KB_SLUG) -> None:
    """
    初始化知识库 collection 并绑定 embedding 函数。
    必须在首次使用知识库前调用。
    """
    collection = get_or_create_collection(kb_slug)
    logger.info("知识库 embedding 初始化完成: %s (backend=%s)", kb_slug, get_embedding_backend())


# ── RAG 检索核心 ──

def consult_knowledge_base(
    query: str,
    kb_slug: str = DEFAULT_KB_SLUG,
    top_k: int = RAG_TOP_K,
    similarity_threshold: Optional[float] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    从知识库检索与查询相关的文档片段，返回拼接的上下文字符串。

    Args:
        query: 用户查询文本
        kb_slug: 知识库稳定标识
        top_k: 检索返回的最大文档数
        similarity_threshold: 相似度阈值（0-1，越低越宽松）。
            为 None 时根据当前 embedding 后端自动选择（hash=0.2，transformer=0.5）

    Returns:
        (context_text, raw_results) 元组
        - context_text: 拼接后的上下文文本，未命中时为空字符串
        - raw_results: 原始检索结果列表
    """
    if similarity_threshold is None:
        similarity_threshold = _effective_threshold()

    try:
        collection = get_or_create_collection(kb_slug)
    except RuntimeError as e:
        logger.error("知识库加载失败: %s", e)
        return "", []

    if collection.count() == 0:
        logger.info("知识库为空，跳过检索: %s", kb_slug)
        return "", []

    results = query_documents(
        kb_slug=kb_slug,
        query_texts=[query],
        n_results=top_k,
    )

    # 距离换算：需与 collection 的距离空间一致
    #   - cosine 空间（本模块新建的 collection）：distance = 1 - cosine_similarity ∈ [0,2]
    #   - l2 空间（旧 collection / Chroma 默认）：distance = 平方L2 = 2(1-cos)
    space = (collection.metadata or {}).get("hnsw:space", "l2")
    context_parts = []
    raw_results = []

    if results["documents"] and results["documents"][0]:
        for i, (doc, distance) in enumerate(zip(
            results["documents"][0],
            results["distances"][0] if results["distances"] else []
        )):
            if space == "cosine":
                similarity = 1.0 - distance if distance is not None else 0.0
            else:
                similarity = 1.0 - (distance / 2.0) if distance is not None else 0.0
            if similarity >= similarity_threshold:
                context_parts.append(f"[文档{i+1}] {doc}")
                raw_results.append({
                    "index": i,
                    "document": doc,
                    "similarity": round(similarity, 4),
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                })

    context_text = "\n\n".join(context_parts) if context_parts else ""

    if context_text:
        logger.info("RAG 检索命中 %d 个片段（阈值=%.2f）", len(raw_results), similarity_threshold)
    else:
        logger.info("RAG 检索未命中任何片段（阈值=%.2f）", similarity_threshold)

    return context_text, raw_results


def build_rag_prompt(
    query: str,
    kb_slug: str = DEFAULT_KB_SLUG,
    top_k: int = RAG_TOP_K,
) -> Dict[str, Any]:
    """
    构建包含 RAG 检索结果的完整 prompt 上下文。

    Args:
        query: 用户查询
        kb_slug: 知识库标识

    Returns:
        {
            "has_context": bool,      # 是否命中知识库
            "context_text": str,      # 原始上下文文本
            "rag_prompt": str,        # 拼接后的 RAG 提示（可直接拼入 system message）
            "sources": list,          # 命中来源列表
        }
    """
    context_text, raw_results = consult_knowledge_base(
        query=query,
        kb_slug=kb_slug,
        top_k=top_k,
    )

    has_context = bool(context_text)

    if has_context:
        rag_prompt = RAG_CONTEXT_TEMPLATE.format(
            context=context_text,
            question=query,
        )
    else:
        rag_prompt = ""

    return {
        "has_context": has_context,
        "context_text": context_text,
        "rag_prompt": rag_prompt,
        "sources": [r["metadata"].get("filename", "unknown") for r in raw_results],
    }


def should_use_rag(query: str, kb_slug: str = DEFAULT_KB_SLUG) -> bool:
    """
    判断是否需要触发 RAG 检索。
    目前策略：始终尝试检索（如果知识库非空）。
    可扩展为关键词触发或 LLM 判断。
    """
    try:
        collection = get_or_create_collection(kb_slug)
        return collection.count() > 0
    except Exception:
        return False
