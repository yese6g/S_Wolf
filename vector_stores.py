"""
向量存储服务 - SilverWolf
基于 ChromaDB 实现的向量持久化存储。

v0.4.0 变更（离线优先重构）：
- 所有 embedding 均在本地计算并显式传入 ChromaDB，绝不触发 Chroma 默认 ONNX 模型联网下载
- embedding 后端两级回退：
    1. sentence-transformers 本地模型（项目 models/embedding 目录 / EMBEDDING_MODEL_PATH / HF 缓存快照）
    2. 内置 LocalHashEmbeddingFunction（确定性 hash 特征，1024 维，开箱即用，零下载）
- collection 创建时显式声明 cosine 距离空间

核心设计原则：
- collection_name 使用稳定的 ASCII 标识（kb_{slug}），禁止运行时自增 ID
- embedding 模型元数据绑定到 collection，加载时校验一致性
- embedding 变更时自动检测维度不匹配并报错，而非静默创建新 collection
"""

import os
import math
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

from config_data import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_PATH,
    EMBEDDING_DIMENSION,
    BASE_DIR,
)

logger = logging.getLogger(__name__)

# ── ChromaDB 客户端单例 ──

_client: Optional[chromadb.PersistentClient] = None


def get_chroma_client() -> chromadb.PersistentClient:
    """获取 ChromaDB 持久化客户端（单例）。"""
    global _client
    if _client is None:
        os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


# ── 离线 Embedding 后端 ──

class LocalHashEmbeddingFunction:
    """
    内置离线 embedding：基于字符 n-gram 的确定性 hash 特征向量。

    - 无需任何模型文件、无需联网下载
    - 特征：字符 unigram(权重1) + bigram(权重2) + trigram(权重3)，计数累加
    - 输出维度固定为 EMBEDDING_DIMENSION（1024），与 bge-large-zh 一致
    - 向量做 L2 归一化，距离度量与余弦相似度单调等价
    质量低于 transformer 模型，但保证完全离线可用。
    """

    def __init__(self, dimension: int = EMBEDDING_DIMENSION):
        self.dimension = dimension

    def _hash_index(self, prefix: str, token: str) -> int:
        digest = hashlib.md5((prefix + token).encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % self.dimension

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        s = (text or "").lower()
        if not s:
            return vec
        # unigram
        for ch in s:
            vec[self._hash_index("1:", ch)] += 1.0
        # bigram
        for i in range(len(s) - 1):
            vec[self._hash_index("2:", s[i:i + 2])] += 2.0
        # trigram
        for i in range(len(s) - 2):
            vec[self._hash_index("3:", s[i:i + 3])] += 3.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def __call__(self, input: Any) -> List[List[float]]:
        if isinstance(input, str):
            input = [input]
        return [self._embed_one(t) for t in input]


def _resolve_local_model_path() -> Optional[str]:
    """
    解析本地 sentence-transformers 模型路径，找不到返回 None。
    查找顺序：
      1. 项目内 models/embedding（推荐离线部署：把模型文件放这里）
      2. EMBEDDING_MODEL_PATH 指定的路径（模型目录或 HF hub 缓存根目录）
    """
    # 1. 项目内模型目录
    proj_model = BASE_DIR / "models" / "embedding"
    if proj_model.exists() and (proj_model / "config.json").exists():
        logger.info("找到项目内 embedding 模型: %s", proj_model)
        return str(proj_model)

    # 2. 环境变量/默认路径
    p = Path(EMBEDDING_MODEL_PATH)
    if not p.exists():
        return None
    if (p / "config.json").exists():
        logger.info("找到本地 embedding 模型: %s", p)
        return str(p)
    # HF hub 缓存结构: models--xxx/snapshots/<hash>/...
    if (p / "snapshots").exists():
        snaps = sorted(p.glob("snapshots/*"))
        for s in snaps:
            if (s / "config.json").exists():
                logger.info("找到 HF 缓存中的 embedding 模型快照: %s", s)
                return str(s)
    return None


def _load_sentence_transformer_encode(model_path: str):
    """加载本地 sentence-transformers 模型并返回 encode 函数（仅加载本地文件，绝不联网）。"""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_path, device="cpu")

    def encode(texts: List[str]) -> List[List[float]]:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=16,
        )
        return [v.tolist() for v in vectors]

    return encode


_embedding_func: Optional[Any] = None
_embedding_backend: str = "none"


def get_embedding_function() -> Any:
    """
    获取 embedding 函数（单例）：texts -> List[List[float]]。
    优先本地 sentence-transformers 模型，否则回退到内置离线 hash embedding。
    """
    global _embedding_func, _embedding_backend
    if _embedding_func is not None:
        return _embedding_func

    model_path = _resolve_local_model_path()
    if model_path:
        try:
            _embedding_func = _load_sentence_transformer_encode(model_path)
            _embedding_backend = f"sentence-transformers@{model_path}"
            logger.info("Embedding 后端: %s", _embedding_backend)
            # 预热
            _ = _embedding_func(["预热"])
            return _embedding_func
        except Exception as e:
            logger.warning("本地 sentence-transformers 加载失败(%s)，回退到内置 hash embedding: %s", model_path, e)
            _embedding_func = None

    _embedding_func = LocalHashEmbeddingFunction()
    _embedding_backend = "local-hash (内置离线, 零下载)"
    logger.warning("Embedding 后端: %s（未找到本地 transformer 模型，使用内置离线 embedding）", _embedding_backend)
    return _embedding_func


def get_embedding_backend() -> str:
    """获取当前 embedding 后端名称（供 /api/config 展示）。"""
    get_embedding_function()
    return _embedding_backend


def embed_texts(texts: List[str]) -> List[List[float]]:
    """计算文本 embedding（本地执行）。"""
    func = get_embedding_function()
    return func(texts)


# ── Collection 管理 ──

def _make_collection_name(kb_slug: str) -> str:
    """
    根据知识库 slug 生成稳定的 collection 名称。
    仅允许 ASCII [a-zA-Z0-9._-]，不符合要求时自动替换为下划线。
    """
    safe_chars = []
    for ch in kb_slug:
        if ch.isascii() and (ch.isalnum() or ch in "._-"):
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    return f"kb_{''.join(safe_chars)}"


def _validate_collection_metadata(collection) -> None:
    """
    校验 collection 的 embedding 元数据是否与当前配置匹配。
    不匹配时抛出 RuntimeError，防止维度不一致导致查询异常。
    """
    meta = collection.metadata or {}
    stored_model = meta.get("embedding_model", "")
    stored_dim = meta.get("embedding_dimension", 0)

    if stored_model and stored_model != EMBEDDING_MODEL_NAME:
        raise RuntimeError(
            f"Embedding 模型不匹配：collection 使用 {stored_model}，"
            f"当前配置为 {EMBEDDING_MODEL_NAME}。请重建知识库。"
        )

    if stored_dim and stored_dim != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Embedding 维度不匹配：collection 为 {stored_dim} 维，"
            f"当前配置为 {EMBEDDING_DIMENSION} 维。请重建知识库。"
        )


def get_or_create_collection(kb_slug: str) -> Any:
    """
    获取或创建指定知识库的 Chroma collection。
    - 使用稳定的 collection_name
    - 创建时写入 embedding 元数据，并显式使用 cosine 距离空间
    - 加载时校验元数据一致性
    """
    client = get_chroma_client()
    collection_name = _make_collection_name(kb_slug)

    try:
        collection = client.get_collection(name=collection_name)
        _validate_collection_metadata(collection)
        logger.info("已加载现有 collection: %s", collection_name)
    except NotFoundError:
        collection = client.create_collection(
            name=collection_name,
            metadata={
                "embedding_model": EMBEDDING_MODEL_NAME,
                "embedding_dimension": EMBEDDING_DIMENSION,
                "hnsw:space": "cosine",
            },
        )
        logger.info("已创建新 collection: %s（cosine 空间）", collection_name)

    return collection


def delete_collection(kb_slug: str) -> bool:
    """删除指定知识库的 collection，返回是否成功。"""
    client = get_chroma_client()
    collection_name = _make_collection_name(kb_slug)
    try:
        client.delete_collection(name=collection_name)
        logger.info("已删除 collection: %s", collection_name)
        return True
    except NotFoundError:
        logger.warning("collection 不存在，无法删除: %s", collection_name)
        return False


def list_collections() -> List[Dict[str, Any]]:
    """列出所有知识库 collection 及其元数据。"""
    client = get_chroma_client()
    collections = client.list_collections()
    result = []
    for col in collections:
        result.append({
            "name": col.name,
            "embedding_model": (col.metadata or {}).get("embedding_model", ""),
            "embedding_dimension": (col.metadata or {}).get("embedding_dimension", 0),
            "count": col.count(),
        })
    return result


# ── 向量操作 ──

def add_documents(
    kb_slug: str,
    ids: List[str],
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    向指定知识库添加文档（本地计算 embedding 后显式传入，不依赖任何联网组件）。
    """
    embeddings = embed_texts(documents)
    collection = get_or_create_collection(kb_slug)
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    logger.info("已添加 %d 个文档到 %s", len(ids), _make_collection_name(kb_slug))


def query_documents(
    kb_slug: str,
    query_texts: List[str],
    n_results: int = 5,
) -> Dict[str, Any]:
    """
    在指定知识库中检索相关文档（本地计算查询向量）。
    返回 Chroma 原始结果字典（documents / metadatas / distances / ids）。
    """
    query_embeddings = embed_texts(query_texts)
    collection = get_or_create_collection(kb_slug)
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    return results


def get_collection_count(kb_slug: str) -> int:
    """获取指定知识库的文档数量。"""
    collection = get_or_create_collection(kb_slug)
    return collection.count()


def delete_documents_by_filename(kb_slug: str, filename: str) -> int:
    """
    按文件名删除某个知识库里的向量分块（用于「修改 / 删除 RAG 文件」）。

    原理：入库时每条 chunk 的 metadata 里都写了 filename（见 knowledge_base.ingest_file），
    ChromaDB 支持 where 条件删除，因此按 metadata 过滤即可精确移除该文件的旧分块，
    避免修改文件后新旧内容同时被检索到。

    Args:
        kb_slug: 知识库标识
        filename: 原始文件名（与入库 metadata 中的 filename 一致）

    Returns:
        删除的分块数量（估算：删除前后 count 差值）
    """
    collection = get_or_create_collection(kb_slug)
    before = collection.count()
    try:
        collection.delete(where={"filename": filename})
    except Exception as e:
        logger.warning("按文件名删除向量失败(%s / %s): %s", kb_slug, filename, e)
        return 0
    return max(before - collection.count(), 0)
