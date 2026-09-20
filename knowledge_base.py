"""
知识库更新服务 - SilverWolf
负责文档解析、文本分块、向量化入库等知识库管理功能。
支持 TXT / MD / PDF / DOCX 等常见格式，以及代码文件。
"""

import os
import time
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from config_data import (
    KNOWLEDGE_BASES_DIR,
    SUPPORTED_FILE_EXTENSIONS,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    DEFAULT_KB_SLUG,
)
from vector_stores import (
    get_or_create_collection,
    add_documents,
    delete_collection,
    get_collection_count,
    delete_documents_by_filename,
)

logger = logging.getLogger(__name__)

# ── 文档解析器 ──

def _parse_txt(content: bytes, filename: str) -> List[str]:
    """解析纯文本 / Markdown 文件（自动去除 BOM，按 utf-8 → gbk 顺序解码）。"""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = content.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = content.decode("utf-8", errors="ignore")
    return [text]


def _parse_pdf(filepath: str) -> List[str]:
    """解析 PDF 文件。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return ["\n".join(pages)]
    except ImportError:
        logger.warning("pypdf 未安装，无法解析 PDF 文件")
        return []
    except Exception as e:
        logger.error("PDF 解析失败: %s", e)
        return []


def _parse_docx(filepath: str) -> List[str]:
    """解析 DOCX 文件。"""
    try:
        from docx import Document
        doc = Document(filepath)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return ["\n".join(paragraphs)]
    except ImportError:
        logger.warning("python-docx 未安装，无法解析 DOCX 文件")
        return []
    except Exception as e:
        logger.error("DOCX 解析失败: %s", e)
        return []


def parse_file(filepath: str) -> List[str]:
    """
    根据文件扩展名选择解析器，提取文本内容。

    Args:
        filepath: 文件的绝对路径

    Returns:
        解析出的文本段落列表
    """
    ext = Path(filepath).suffix.lower()
    filename = Path(filepath).name

    if ext not in SUPPORTED_FILE_EXTENSIONS:
        logger.warning("不支持的文件类型: %s", ext)
        return []

    # 读取文件内容
    try:
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        logger.error("文件读取失败 %s: %s", filepath, e)
        return []

    # 根据扩展名选择解析器
    if ext == ".pdf":
        return _parse_pdf(filepath)
    elif ext in (".docx", ".doc"):
        return _parse_docx(filepath)
    else:
        # 默认：纯文本解析（txt, md, py, js, json 等）
        return _parse_txt(content, filename)


# ── 文本分块 ──

def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """
    将长文本按字符数分块，相邻块有 overlap 字符的重叠。

    Args:
        text: 待分块的完整文本
        chunk_size: 每块字符数
        overlap: 块间重叠字符数

    Returns:
        分块后的文本列表
    """
    if not text.strip():
        return []

    chunks = []
    # 按句子/段落边界切分，避免生硬截断
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 <= chunk_size:
            current_chunk = (current_chunk + "\n" + line).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            # 如果单个行超过 chunk_size，直接按字符切
            if len(line) > chunk_size:
                for i in range(0, len(line), chunk_size - overlap):
                    chunk = line[i:i + chunk_size]
                    if chunk:
                        chunks.append(chunk)
                current_chunk = ""
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    # 过滤空块
    chunks = [c.strip() for c in chunks if c.strip()]
    return chunks


# ── 文件上传与入库 ──

def ingest_file(
    filepath: str,
    kb_slug: str = DEFAULT_KB_SLUG,
    custom_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    将单个文件解析、分块并入库到指定知识库。

    Args:
        filepath: 文件的绝对路径
        kb_slug: 目标知识库标识
        custom_filename: 自定义文件名（用于元数据）

    Returns:
        处理结果字典
    """
    filename = custom_filename or Path(filepath).name
    ext = Path(filepath).suffix.lower()

    # 解析文件
    texts = parse_file(filepath)
    if not texts:
        return {"success": False, "error": f"文件解析失败: {filename}", "chunks": 0}

    # 分块
    all_chunks = []
    for text in texts:
        chunks = chunk_text(text)
        all_chunks.extend(chunks)

    if not all_chunks:
        return {"success": False, "error": f"文件为空: {filename}", "chunks": 0}

    # 生成唯一 ID 并入库
    ids = [f"{filename}_chunk_{i}_{uuid.uuid4().hex[:8]}" for i in range(len(all_chunks))]
    metadatas = [
        {
            "filename": filename,
            "filetype": ext,
            "chunk_index": i,
            "total_chunks": len(all_chunks),
            "uploaded_at": int(time.time()),
        }
        for i in range(len(all_chunks))
    ]

    try:
        add_documents(
            kb_slug=kb_slug,
            ids=ids,
            documents=all_chunks,
            metadatas=metadatas,
        )
        logger.info("文件入库成功: %s (%d 分块) → %s", filename, len(all_chunks), kb_slug)
        return {
            "success": True,
            "filename": filename,
            "chunks": len(all_chunks),
            "total_documents": get_collection_count(kb_slug),
        }
    except Exception as e:
        logger.error("入库失败 %s: %s", filename, e)
        return {"success": False, "error": str(e), "chunks": 0}


# ── 知识库管理 ──

def create_knowledge_base(kb_slug: str) -> Dict[str, Any]:
    """
    创建新的知识库（初始化 collection）。

    Args:
        kb_slug: 知识库稳定标识

    Returns:
        创建结果
    """
    try:
        collection = get_or_create_collection(kb_slug)
        os.makedirs(KNOWLEDGE_BASES_DIR, exist_ok=True)
        # 创建知识库元数据文件
        meta_path = os.path.join(KNOWLEDGE_BASES_DIR, f"{kb_slug}.json")
        import json
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "kb_slug": kb_slug,
                "created_at": int(time.time()),
                "document_count": collection.count(),
            }, f, ensure_ascii=False, indent=2)
        return {"success": True, "kb_slug": kb_slug, "count": collection.count()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_knowledge_base(kb_slug: str) -> Dict[str, Any]:
    """删除指定知识库。"""
    try:
        deleted = delete_collection(kb_slug)
        # 删除元数据文件
        meta_path = os.path.join(KNOWLEDGE_BASES_DIR, f"{kb_slug}.json")
        if os.path.exists(meta_path):
            os.remove(meta_path)
        return {"success": deleted, "kb_slug": kb_slug}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_knowledge_bases() -> List[Dict[str, Any]]:
    """列出所有知识库及其元数据。"""
    import json
    result = []
    if os.path.exists(KNOWLEDGE_BASES_DIR):
        for fname in os.listdir(KNOWLEDGE_BASES_DIR):
            if fname.endswith(".json"):
                meta_path = os.path.join(KNOWLEDGE_BASES_DIR, fname)
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    result.append(meta)
                except Exception:
                    pass
    return result


def rebuild_knowledge_base(kb_slug: str) -> Dict[str, Any]:
    """重建知识库（删除旧 collection 并重新创建）。"""
    delete_collection(kb_slug)
    return create_knowledge_base(kb_slug)


# ── RAG 文件持久化（v2.0.0：供 Agent 自我维护知识库） ──
# 说明：v1.0.0 之前上传的文件解析入库后就删掉了，磁盘上不留原文件；
# 为了让 Agent 能「新增 / 修改 / 删除 RAG 文件」，这里把文本文件落盘到
# knowledge_bases/<kb_slug>/files/ 下，作为可再次编辑的“真源”，
# 向量库只是它的检索副本（修改文件时会先删旧分块再重新入库）。

def kb_files_dir(kb_slug: str = DEFAULT_KB_SLUG) -> str:
    """返回某个知识库的文本文件目录（不存在则自动创建）。"""
    path = Path(KNOWLEDGE_BASES_DIR) / kb_slug / "files"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _safe_kb_filename(filename: str) -> str:
    """只保留文件名本身，防止路径穿越（../../ 之类）。"""
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise ValueError("文件名不能为空")
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_FILE_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {ext or '(无扩展名)'}")
    return name


def list_kb_files(kb_slug: str = DEFAULT_KB_SLUG) -> List[Dict[str, Any]]:
    """列出某个知识库里已持久化的文件（名称 / 字节数 / 修改时间）。"""
    directory = Path(kb_files_dir(kb_slug))
    files = []
    for p in sorted(directory.glob("*")):
        if p.is_file():
            stat = p.stat()
            files.append({
                "filename": p.name,
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime),
            })
    return files


def save_kb_file(
    kb_slug: str,
    filename: str,
    content: str,
) -> Dict[str, Any]:
    """
    新增或修改一个 RAG 文本文件，并同步更新向量库。

    流程（重要）：
      1. 校验并规范化文件名（防路径穿越、限制扩展名）；
      2. 写入知识库文件目录（磁盘真源）；
      3. 删除该文件名在向量库中的旧分块（避免新旧内容共存）；
      4. 重新解析入库，返回分块数。

    仅支持文本类扩展名（.txt/.md/.py/.json 等，见 SUPPORTED_FILE_EXTENSIONS 里的文本类型）；
    PDF/DOCX 这类二进制格式需要先转换，因此不在这里处理。
    """
    name = _safe_kb_filename(filename)
    ext = Path(name).suffix.lower()
    text_exts = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".json", ".yaml",
                 ".yml", ".html", ".css", ".sql", ".sh", ".bat", ".csv", ".ini",
                 ".conf", ".log", ".toml", ".xml", ".java", ".c", ".cpp", ".go", ".rs"}
    if ext not in text_exts:
        return {"success": False, "error": f"{ext} 不是可编辑的文本类型，请改用 .txt/.md 等"}

    directory = Path(kb_files_dir(kb_slug))
    target = directory / name
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}

    removed = delete_documents_by_filename(kb_slug, name)
    result = ingest_file(str(target), kb_slug=kb_slug, custom_filename=name)
    result["replaced_chunks"] = removed
    result["path"] = str(target)
    return result


def delete_kb_file(kb_slug: str, filename: str) -> Dict[str, Any]:
    """删除一个 RAG 文件：同时移除磁盘文件与向量库中的分块。"""
    name = _safe_kb_filename(filename)
    path = Path(kb_files_dir(kb_slug)) / name
    existed = path.exists()
    if existed:
        try:
            path.unlink()
        except Exception as e:
            return {"success": False, "error": f"删除文件失败: {e}"}
    removed = delete_documents_by_filename(kb_slug, name)
    return {"success": True, "filename": name, "removed_chunks": removed, "file_existed": existed}
