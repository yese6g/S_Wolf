"""
知识库更新服务 - SilverWolf
负责文档解析、文本分块、向量化入库等知识库管理功能。
支持 TXT / MD / PDF / DOCX 等常见格式，以及代码文件。
"""

import os
import shutil
import time
import uuid
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

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
STANDARD_KBS = {"agent": "Agent 开发", "python": "Python", "cyberSecurity": "网络安全"}


def validate_kb_slug(slug: str) -> str:
    """知识库名同时用作磁盘路径和向量集合名，因此只接受安全标识。"""
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,61}[A-Za-z0-9]", slug):
        raise ValueError("知识库名用 3–63 位字母、数字、下划线或连字符")
    root = Path(KNOWLEDGE_BASES_DIR).resolve()
    if not (root / slug).resolve().is_relative_to(root):
        raise ValueError("知识库路径无效")
    return slug

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
        kb_slug = validate_kb_slug(kb_slug)
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
    """删除指定知识库：向量、元数据与磁盘真源一并清除。"""
    try:
        kb_slug = validate_kb_slug(kb_slug)
        deleted = delete_collection(kb_slug)
        # 删除元数据文件
        meta_path = os.path.join(KNOWLEDGE_BASES_DIR, f"{kb_slug}.json")
        if os.path.exists(meta_path):
            os.remove(meta_path)
        # 磁盘真源同删：否则同 slug 重建后 rebuild 会把「已删资料」重新入库复活
        files_root = Path(KNOWLEDGE_BASES_DIR) / kb_slug
        if files_root.is_dir():
            shutil.rmtree(files_root, ignore_errors=True)
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
    existing = {item.get("kb_slug") for item in result}
    for slug, name in STANDARD_KBS.items():
        if slug not in existing:
            result.append({"kb_slug": slug, "name": name, "document_count": 0})
    for item in result:
        item["name"] = STANDARD_KBS.get(item.get("kb_slug"), item.get("name", item.get("kb_slug")))
    return result


def rebuild_knowledge_base(kb_slug: str) -> Dict[str, Any]:
    """从保存的资料重建；旧版只有向量没有原文件时拒绝清空。"""
    validate_kb_slug(kb_slug)
    directory = Path(KNOWLEDGE_BASES_DIR) / kb_slug / "files"
    collection = get_or_create_collection(kb_slug)
    old = collection.get(include=["metadatas"])
    names = {m.get("filename") for m in old.get("metadatas", []) if m}
    if any(not name or not (directory / Path(name).name).is_file() for name in names):
        return {"success": False, "error": "部分旧资料没有原文件，先导出资料再重建，避免清空知识。"}
    delete_collection(kb_slug)
    failures = []
    if directory.exists():
        for path in directory.iterdir():
            if path.is_file() and not ingest_file(str(path), kb_slug).get("success"):
                failures.append(path.name)
    # 入库本身会按需建 collection；这里最后再写一次元数据，document_count 取重建后的真实值
    result = create_knowledge_base(kb_slug)
    if failures:
        result.update(success=False, error="部分原文件未能重新索引", files=failures)
    return result


# ── RAG 文件持久化（v2.0.0：供 Agent 自我维护知识库） ──
# 说明：v1.0.0 之前上传的文件解析入库后就删掉了，磁盘上不留原文件；
# 为了让 Agent 能「新增 / 修改 / 删除 RAG 文件」，这里把文本文件落盘到
# knowledge_bases/<kb_slug>/files/ 下，作为可再次编辑的“真源”，
# 向量库只是它的检索副本（修改文件时会先删旧分块再重新入库）。

def kb_files_dir(kb_slug: str = DEFAULT_KB_SLUG) -> str:
    """返回某个知识库的文本文件目录（不存在则自动创建）。"""
    path = Path(KNOWLEDGE_BASES_DIR) / validate_kb_slug(kb_slug) / "files"
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
    directory = Path(KNOWLEDGE_BASES_DIR) / validate_kb_slug(kb_slug) / "files"
    if not directory.exists():
        return []
    files = []
    for p in sorted(directory.glob("*")):
        if p.is_file():
            stat = p.stat()
            item = {
                "filename": p.name,
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
            if re.fullmatch(r"note-[0-9a-f]{24}\.md", p.name):
                with p.open(encoding="utf-8") as stream:
                    preview = stream.read(500)
                item["title"] = preview.splitlines()[0].lstrip("# ")
                item["status"] = "已验证" if "状态：已验证" in preview else "待核验"
            files.append(item)
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
    previous = None
    if target.exists():
        try:
            previous = target.read_text(encoding="utf-8")
        except Exception:
            previous = None  # 旧内容读不出来时只能尽力
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}

    removed = delete_documents_by_filename(kb_slug, name)
    result = ingest_file(str(target), kb_slug=kb_slug, custom_filename=name)
    if not result.get("success") and previous is not None:
        # 新内容入库失败：磁盘与索引一起还原成旧版，避免这份资料从检索里凭空消失
        try:
            target.write_text(previous, encoding="utf-8")
            restore = ingest_file(str(target), kb_slug=kb_slug, custom_filename=name)
            if restore.get("success"):
                result["restored"] = True
            else:
                result["error"] = f"{result.get('error')}；旧内容恢复索引也失败：{restore.get('error')}"
        except Exception as e:
            result["error"] = f"{result.get('error')}；旧内容恢复失败：{e}"
    if result.get("success"):
        create_knowledge_base(kb_slug)
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
    create_knowledge_base(kb_slug)
    return {"success": True, "filename": name, "removed_chunks": removed, "file_existed": existed}
