"""
main.py - SilverWolf 项目入口
PyCharm FastAPI 插件需要在此文件中直接看到 FastAPI 实例。
启动方式: python -m uvicorn main:app --reload

v0.4.0：
- 使用 lifespan 在启动时初始化数据库与日志
- 继续由 main.py 显式创建 FastAPI 实例并挂载路由/静态资源
"""

import os
import sys
sys.dont_write_bytecode = True  # 启动与工具导入不在项目散落可重建的缓存目录。
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 导入配置
from config_data import STATIC_DIR, BASE_DIR, APP_NAME, APP_VERSION
from middleware.logging_system import configure_logging
from middleware.security import LocalAccessMiddleware

configure_logging()

# 导入路由模块（从 SilverWolf 导入 router，而非 app）
from SilverWolf import router
from app_workbench import router as workbench_router

# ── 生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子。"""
    logger = logging.getLogger("main")
    logger.info("%s v%s 启动中...", APP_NAME, APP_VERSION)
    # 容器/远程客户端不能领取本机 Cookie；启动时就创建令牌，避免首次登录无文件可读。
    from middleware.security import access_token
    access_token()
    # 数据库表初始化（幂等；SilverWolf 模块导入时也会执行一次，这里兜底）
    from file_history_store import init_db
    init_db()
    from agent import jobs
    jobs.recover()
    from file_attachments import sweep_orphan_attachments
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(sweep_orphan_attachments)
    logger.info("SQLite 数据库就绪 (chat_history.db)")
    yield
    from chat_runtime import shutdown_runs
    await shutdown_runs()
    from agent import terminal
    await run_in_threadpool(terminal.shutdown)
    from agent import mcp_client
    jobs.shutdown()
    mcp_client.shutdown()
    import provider_config
    provider_config.close_clients()
    logger.info("应用已关闭。")


# ── 在此文件中显式创建 FastAPI 实例（PyCharm FastAPI 插件需要） ──
app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

# ── 添加 CORS 中间件 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("SW_ALLOWED_ORIGINS", "").split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(LocalAccessMiddleware)

# ── 前端静态资源缓存策略（v0.9.3） ──
# 让浏览器每次都向后端校验，杜绝旧 JS/CSS 被长期缓存导致的“改代码不生效”。
# 资源未变化时仍会返回 304（正常缓存命中），变化后一定返回新内容。
@app.middleware("http")
async def frontend_no_cache(request, call_next):
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:12]
    request_logger = logging.getLogger("audit.requests")
    try:
        response = await call_next(request)
    except Exception:
        request_logger.exception("request=%s method=%s 请求失败", request_id, request.method)
        raise
    # 不记录 query、请求体与鉴权头；路径优先用路由模板，避免写入会话标识。
    route = getattr(request.scope.get("route"), "path", "unmatched")
    request_logger.info("request=%s method=%s route=%s status=%s elapsed_ms=%.1f",
                        request_id, request.method, route, response.status_code,
                        (time.monotonic() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith(("/frontend/", "/static/")):
        response.headers["Cache-Control"] = "no-cache"
    return response

# ── 挂载静态文件 ──
# static 目录：背景图、头像等
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# frontend 目录：Vue 前端源码（index.html 引用 /frontend/src/* 与 /frontend/vendor/*）
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

# ── 挂载 SilverWolf 路由 ──
app.include_router(router)
app.include_router(workbench_router)

# ── 日志 ──
logger = logging.getLogger("main")
logger.info("SilverWolf 应用已启动，路由已挂载。")
