"""
file_attachments.py - 聊天附件处理（文本 / 压缩包 / 代码 / Office 文档 / 可执行文件元信息）

v0.8.0 新增：支持把 zip/rar/7z 压缩包、exe、Python 源码、Word/PDF 等文件作为
会话附件上传，供模型**静态分析**（读文本、列压缩包内容、提取字符串与哈希）。
只做读取，绝不执行任何上传的文件。

存储位置：data/uploads/<日期>/<随机前缀>_<原文件名>（已随 data/ 一起 gitignore）
"""

import hashlib
import os
import re
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_data import BASE_DIR

# ── 常量 ──
UPLOAD_ROOT = BASE_DIR / "data" / "uploads"
MAX_FILE_SIZE = 100 * 1024 * 1024        # 单文件 100MB
MAX_TOTAL_SIZE = 300 * 1024 * 1024       # 单次请求 300MB
MAX_FILES_PER_REQUEST = 20
TEXT_PREVIEW_LIMIT = 6000                # 文本类文件进入上下文的字符上限
ARCHIVE_MEMBER_LIMIT = 100               # 压缩包内最多列出成员数
ARCHIVE_INNER_TEXT_LIMIT = 2000          # 压缩包内小文本文件预览上限
ARCHIVE_INNER_FILE_LIMIT = 3             # 压缩包内最多预览几个小文本文件
BINARY_STRINGS_LIMIT = 4000              # 二进制文件 strings 提取上限

# 文本 / 代码类：内容直接进上下文
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".xml", ".html", ".css", ".sql", ".java", ".cpp", ".c", ".h", ".hpp",
    ".go", ".rs", ".sh", ".bat", ".cmd", ".ps1", ".csv", ".ini", ".conf",
    ".log", ".toml", ".php", ".rb", ".pl", ".lua", ".asm", ".s", ".kt",
    ".swift", ".m", ".r", ".vbs", ".jsx", ".tsx", ".vue",
}
# 文档类
DOC_EXTS = {".docx", ".doc", ".pdf"}
# 压缩包类
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"}
# 二进制类：提取字符串 + 哈希
BINARY_EXTS = {
    ".exe", ".dll", ".bin", ".elf", ".so", ".o", ".a", ".apk", ".jar",
    ".class", ".pyc", ".dat", ".pcap", ".pcapng", ".woff", ".woff2",
}
# 图片类：只给元信息
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"}


# ── 工具函数 ──

def _safe_name(name: str) -> str:
    """去掉路径成分，只保留文件名。"""
    return Path(name or "unnamed").name.strip() or "unnamed"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _decode_text(data: bytes) -> str:
    """按 utf-8-sig → gbk → latin-1 顺序解码，尽量不乱码。"""
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def _extract_strings(data: bytes, limit: int = BINARY_STRINGS_LIMIT) -> str:
    """提取可打印 ASCII 字符串片段（对 exe/dll 第一步常做的 strings 探查）。"""
    pattern = re.compile(rb"[\x20-\x7e]{4,}")
    parts = []
    total = 0
    for m in pattern.finditer(data[: 4 * 1024 * 1024]):  # 只看前 4MB
        s = m.group().decode("ascii", errors="replace")
        parts.append(s)
        total += len(s) + 1
        if total >= limit:
            break
    return "\n".join(parts)[:limit]


def _ext_kind(ext: str) -> str:
    if ext in TEXT_EXTS:
        return "text"
    if ext in DOC_EXTS:
        return "doc"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in BINARY_EXTS:
        return "binary"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


# ── 保存 ──

async def save_upload(files: List[Any]) -> List[Dict[str, Any]]:
    """
    保存上传文件到 data/uploads/<日期>/<uuid>_<文件名>。
    返回前端可用的记录列表（含相对 path，供聊天时引用）。
    """
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    today_dir = UPLOAD_ROOT / date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)

    if not files:
        raise ValueError("没有收到文件")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")

    records: List[Dict[str, Any]] = []
    total = 0
    for f in files:
        name = _safe_name(f.filename)
        try:
            data = await f.read()
        except Exception:
            data = b""
        size = len(data)
        total += size
        if size == 0:
            records.append({"name": name, "size": 0, "error": "文件为空"})
            continue
        if size > MAX_FILE_SIZE:
            records.append({"name": name, "size": size, "error": "文件超过 100MB 上限"})
            continue

        ext = os.path.splitext(name)[1].lower()
        kind = _ext_kind(ext)
        rel = f"{date.today().isoformat()}/{uuid.uuid4().hex[:8]}_{name}"
        dest = UPLOAD_ROOT / rel
        with open(dest, "wb") as w:
            w.write(data)

        records.append({
            "name": name,
            "size": size,
            "size_label": _human_size(size),
            "path": rel,
            "sha256": _sha256(data),
            "kind": kind,
            "note": _kind_note(kind, ext),
        })

    if total > MAX_TOTAL_SIZE:
        raise ValueError("单次上传总量超过 300MB 上限")
    return records


def _kind_note(kind: str, ext: str) -> str:
    notes = {
        "text": "文本/代码",
        "doc": "文档",
        "archive": "压缩包",
        "binary": "二进制/可执行",
        "image": "图片",
        "other": "其他",
    }
    return notes.get(kind, "其他")


# ── 上下文提取 ──

def _read_text_context(path: Path, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = _decode_text(data)
    return text[:limit]


def _docx_context(path: Path, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    try:
        from docx import Document
        doc = Document(str(path))
        parts = []
        for p in doc.paragraphs:
            if p.text.strip():
                parts.append(p.text)
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)[:limit]
    except Exception:
        return ""


def _pdf_context(path: Path, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages[:20]:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        return "\n".join(parts)[:limit]
    except Exception:
        return ""


def _docx_xml_fallback(path: Path, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    """python-docx 失败时，docx 本质是 zip，直接扒 word/document.xml 里的文字。"""
    import re as _re
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        text = _re.sub(r"<w:p[ >]", "\n", xml)
        text = _re.sub(r"<[^>]+>", "", text)
        text = _re.sub(r"\n{2,}", "\n", text).strip()
        return text[:limit]
    except Exception:
        return ""


def _zip_context(path: Path) -> str:
    """列出压缩包内容，并预览其中少量小文本文件（压缩包常见做法）。"""
    lines: List[str] = []
    previewed = 0
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()[:ARCHIVE_MEMBER_LIMIT]
            for info in infos:
                if info.is_dir():
                    lines.append(f"  [目录] {info.filename}")
                else:
                    lines.append(f"  {info.filename} ({_human_size(info.file_size)})")
            if len(zf.infolist()) > ARCHIVE_MEMBER_LIMIT:
                lines.append(f"  … 共 {len(zf.infolist())} 个条目")
            for info in zf.infolist():
                if info.is_dir() or info.file_size > ARCHIVE_INNER_TEXT_LIMIT:
                    continue
                ext = os.path.splitext(info.filename)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                try:
                    inner = _decode_text(zf.read(info, pwd=None))[:ARCHIVE_INNER_TEXT_LIMIT]
                except Exception:
                    continue
                lines.append(f"  [预览 {info.filename}]\n{inner}")
                previewed += 1
                if previewed >= ARCHIVE_INNER_FILE_LIMIT:
                    break
    except zipfile.BadZipFile:
        lines.append("  不是有效的 zip 文件（可能是 rar/7z，请手动解压后上传内部文件）")
    except Exception as e:
        lines.append(f"  解压失败: {str(e)[:100]}")
    return "\n".join(lines)


def extract_file_context(record: Dict[str, Any]) -> str:
    """根据文件类型生成给模型看的上下文文本。"""
    rel = record.get("path", "")
    path = (UPLOAD_ROOT / rel).resolve()
    name = record.get("name", "?")
    size = record.get("size_label") or _human_size(record.get("size", 0))
    sha = record.get("sha256", "")
    kind = record.get("kind", "other")
    ext = os.path.splitext(name)[1].lower()

    header = f"- {name}（{size}，SHA256: {sha}）[{record.get('note') or '文件'}]"
    body = ""
    if kind == "text":
        body = _read_text_context(path)
        if body:
            body = f"  --- 文件内容 ---\n{body}"
        else:
            body = "  （内容像是被加密或编码异常，读不出来；SHA256 已核对）"
    elif kind == "doc":
        if ext == ".pdf":
            body = _pdf_context(path)
        elif ext == ".docx":
            body = _docx_context(path) or _docx_xml_fallback(path)
        else:
            # .doc 老格式等：捞可打印字符串兜底
            try:
                body = _extract_strings(path.read_bytes(), limit=TEXT_PREVIEW_LIMIT)
            except OSError:
                body = ""
        body = f"  --- 文档内容 ---\n{body}" if body else "  （文档内容像是被加密/加壳了，读不出来；SHA256 已核对）"
    elif kind == "archive":
        inner = _zip_context(path)
        body = f"  --- 压缩包内容 ---\n{inner}" if inner else "  （无法读取压缩包）"
    elif kind == "binary":
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        strings = _extract_strings(data)
        body = f"  --- 可打印字符串片段（前 {len(strings)} 字符）---\n{strings}" if strings else "  （可打印字符串没捞到几条，像是加壳/加密了；SHA256 已核对）"
    else:
        body = "  （这类型开不了箱，SHA256 已核对；建议转成文本或解压后再来）"

    return header + "\n" + body


def build_attachments_context(refs: List[Dict[str, Any]]) -> str:
    """把附件上下文拼成一段，插到用户消息前面。"""
    if not refs:
        return ""
    lines = [
        "【聊天附件】（以下文件已作为静态材料提供，可分析其内容/结构/哈希；不要执行它们。"
        "每个附件的 SHA256 都已核对。若某个附件内容无法读取，请用银狼口吻说明，"
        "例如「哈希我签到了，内容像是被加密/加壳了，啧，读不出来」，不要生硬地说『无法提取文字』。）"
    ]
    for ref in refs:
        try:
            lines.append(extract_file_context(ref))
        except Exception as e:
            lines.append(f"- {ref.get('name', '?')}（读取失败: {str(e)[:80]}）")
    return "\n".join(lines)


def resolve_attachment_refs(refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """校验附件引用路径在 uploads 目录内，防止路径穿越。"""
    root = UPLOAD_ROOT.resolve()
    out: List[Dict[str, Any]] = []
    for ref in refs:
        name = _safe_name(ref.get("name", ""))
        rel = str(ref.get("path", ""))
        p = (UPLOAD_ROOT / rel).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            raise ValueError(f"附件路径无效: {name}")
        out.append({
            "name": name,
            "path": rel,
            "size": p.stat().st_size,
            "sha256": _sha256(p.read_bytes()),
            "kind": _ext_kind(os.path.splitext(name)[1].lower()),
            "note": _kind_note(_ext_kind(os.path.splitext(name)[1].lower()), os.path.splitext(name)[1].lower()),
        })
    return out


def sweep_orphan_attachments(max_age_hours: int = 24) -> int:
    """
    清理「未被任何会话引用」且超过保留时长的附件文件。
    覆盖场景：上传了但没发送、发送失败、或旧版本遗留的孤儿文件。
    返回删除的文件数。
    """
    from file_history_store import all_attachment_paths

    referenced = all_attachment_paths()
    root = UPLOAD_ROOT.resolve()
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in referenced:
            continue
        try:
            age_hours = (now - p.stat().st_mtime) / 3600.0
        except OSError:
            continue
        if age_hours >= max_age_hours:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def cleanup_session_attachments(
    session_id: str, paths: Optional[List[str]] = None
) -> int:
    """
    删除某会话引用过的附件物理文件（仍被其它会话引用的保留）。
    返回实际删除的文件数。只作用于 data/uploads 内，防路径穿越。
    """
    from file_history_store import get_attachment_paths, referenced_attachment_paths

    if paths is None:
        # 注意：会话行删除后 attachments 记录会级联消失，
        # 因此调用方应在删除会话前先取得 paths 再传进来。
        paths = get_attachment_paths(session_id)
    if not paths:
        return 0
    remaining = referenced_attachment_paths(paths)
    root = UPLOAD_ROOT.resolve()
    removed = 0
    for rel in paths:
        if rel in remaining:
            continue
        p = (UPLOAD_ROOT / rel).resolve()
        if p.is_relative_to(root) and p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
