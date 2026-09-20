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
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 导入配置
from config_data import STATIC_DIR, BASE_DIR, APP_NAME, APP_VERSION

# 导入路由模块（从 SilverWolf 导入 router，而非 app）
from SilverWolf import router

# ── 生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子。"""
    logger = logging.getLogger("main")
    logger.info("%s v%s 启动中...", APP_NAME, APP_VERSION)
    # 数据库表初始化（幂等；SilverWolf 模块导入时也会执行一次，这里兜底）
    from file_history_store import init_db
    init_db()
    logger.info("SQLite 数据库就绪 (chat_history.db)")
    yield
    logger.info("应用已关闭。")


# ── 在此文件中显式创建 FastAPI 实例（PyCharm FastAPI 插件需要） ──
app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

# ── 添加 CORS 中间件 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 前端静态资源缓存策略（v0.9.3） ──
# 让浏览器每次都向后端校验，杜绝旧 JS/CSS 被长期缓存导致的“改代码不生效”。
# 资源未变化时仍会返回 304（正常缓存命中），变化后一定返回新内容。
@app.middleware("http")
async def frontend_no_cache(request, call_next):
    response = await call_next(request)
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

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")
logger.info("SilverWolf 应用已启动，路由已挂载。")
