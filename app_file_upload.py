"""
知识库更新主程序 - SilverWolf
提供文件上传、知识库管理等 HTTP API。
作为独立的 FastAPI 路由模块，可挂载到主应用或独立运行。
"""

import os
import time
import tempfile
import logging
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from pydantic import BaseModel

from config_data import (
    KNOWLEDGE_BASES_DIR,
    SUPPORTED_FILE_EXTENSIONS,
    DEFAULT_KB_SLUG,
)
from knowledge_base import (
    ingest_file,
    create_knowledge_base,
    delete_knowledge_base,
    list_knowledge_bases,
    rebuild_knowledge_base,
)
from vector_stores import get_collection_count
from rag import init_knowledge_base_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["知识库管理"])

# ── 请求/响应模型 ──

class CreateKBRequest(BaseModel):
    kb_slug: str


class DeleteKBRequest(BaseModel):
    kb_slug: str


class RebuildKBRequest(BaseModel):
    kb_slug: str


class IngestResponse(BaseModel):
    success: bool
    filename: str = ""
    chunks: int = 0
    error: str = ""
    total_documents: int = 0


# ── 文件上传接口 ──

@router.post("/upload", response_model=List[IngestResponse])
async def upload_files(
    files: List[UploadFile] = File(...),
    kb_slug: str = Form(DEFAULT_KB_SLUG),
):
    """
    上传文件到指定知识库。支持多文件批量上传。

    Args:
        files: 上传的文件列表
        kb_slug: 目标知识库标识，默认为 'default_knowledge_base'

    Returns:
        每个文件的处理结果列表
    """
    # 初始化 embedding
    try:
        init_knowledge_base_embedding(kb_slug)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding 初始化失败: {e}")

    results = []

    for upload_file in files:
        # 检查文件类型
        ext = os.path.splitext(upload_file.filename or "")[1].lower()
        if ext not in SUPPORTED_FILE_EXTENSIONS:
            results.append(IngestResponse(
                success=False,
                filename=upload_file.filename,
                error=f"不支持的文件类型: {ext}，支持的类型: {', '.join(sorted(SUPPORTED_FILE_EXTENSIONS))}",
            ))
            continue

        # 保存到临时目录
        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=ext,
                dir=KNOWLEDGE_BASES_DIR,
            ) as tmp:
                content = await upload_file.read()
                tmp.write(content)
                tmp_path = tmp.name

            # 入库
            result = ingest_file(
                filepath=tmp_path,
                kb_slug=kb_slug,
                custom_filename=upload_file.filename,
            )
            results.append(IngestResponse(**result))

        except Exception as e:
            logger.error("文件上传处理失败 %s: %s", upload_file.filename, e)
            results.append(IngestResponse(
                success=False,
                filename=upload_file.filename,
                error=str(e),
            ))
        finally:
            # 清理临时文件
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # 确保知识库元数据文件存在并同步最新文档数（首次上传后可显示，后续保持同步）
    if any(r.success for r in results):
        try:
            import json as _json
            os.makedirs(KNOWLEDGE_BASES_DIR, exist_ok=True)
            meta_path = os.path.join(KNOWLEDGE_BASES_DIR, f"{kb_slug}.json")
            count = get_collection_count(kb_slug)
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = _json.load(f)
                meta["document_count"] = count
                meta["updated_at"] = int(time.time())
            else:
                meta = {
                    "kb_slug": kb_slug,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "document_count": count,
                }
            with open(meta_path, "w", encoding="utf-8") as f:
                _json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("知识库元数据同步失败: %s", e)

    return results


# ── 知识库管理接口 ──

@router.post("/create")
async def create_kb(req: CreateKBRequest):
    """创建新的空知识库。"""
    result = create_knowledge_base(req.kb_slug)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "创建失败"))
    return result


@router.post("/delete")
async def delete_kb(req: DeleteKBRequest):
    """删除指定知识库。"""
    result = delete_knowledge_base(req.kb_slug)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "知识库不存在"))
    return result


@router.post("/rebuild")
async def rebuild_kb(req: RebuildKBRequest):
    """重建指定知识库（清空后重新初始化）。"""
    result = rebuild_knowledge_base(req.kb_slug)
    return result


@router.get("/list")
async def list_kb():
    """列出所有知识库。"""
    return list_knowledge_bases()


@router.get("/supported-extensions")
async def get_supported_extensions():
    """获取支持的文件扩展名列表。"""
    return {"extensions": sorted(SUPPORTED_FILE_EXTENSIONS)}


def setup_kb_routes(app):
    """将知识库路由挂载到 FastAPI 应用。"""
    app.include_router(router)
    return app