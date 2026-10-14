"""本地访问守卫：main 挂载，校验 Host/Origin 与本机访问令牌。

同源本机页面通过 bootstrap 领取 HttpOnly 严格 Cookie；其他客户端使用 Bearer。
令牌只存 data/auth_token，不嵌入前端，不输出日志。局域网部署须显式配置允许域名。
"""

import ipaddress
import logging
import os
import secrets
import threading
import uuid
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from config_data import BASE_DIR
from middleware.logging_system import register_secret

TOKEN_FILE = BASE_DIR / "data" / "auth_token"
COOKIE_NAME = "sw_access"
_LOCK = threading.Lock()


def access_token():
    """读取或原子创建本机令牌；环境变量 SW_ACCESS_TOKEN 可用于容器。"""
    with _LOCK:
        token = os.getenv("SW_ACCESS_TOKEN", "").strip()
        if not token:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            if not TOKEN_FILE.exists():
                try:
                    with TOKEN_FILE.open("x", encoding="utf-8") as f:
                        f.write(secrets.token_urlsafe(32))
                    if os.name != "nt":
                        TOKEN_FILE.chmod(0o600)
                except FileExistsError:
                    pass
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) < 24:
            raise RuntimeError("访问令牌太短，请配置至少 24 字符的 SW_ACCESS_TOKEN")
        register_secret(token)
        return token


def rotate_token():
    """只轮换本机文件令牌；环境令牌交给部署配置管理，不回传明文。"""
    with _LOCK:
        if os.getenv("SW_ACCESS_TOKEN", "").strip():
            raise ValueError("当前使用环境令牌，请修改部署配置后重启")
        token = secrets.token_urlsafe(32)
        temp = TOKEN_FILE.with_name(TOKEN_FILE.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(token, encoding="utf-8")
            if os.name != "nt":
                temp.chmod(0o600)
            temp.replace(TOKEN_FILE)
        finally:
            temp.unlink(missing_ok=True)
        register_secret(token)
        return token


class LocalAccessMiddleware:
    """纯 ASGI 守卫，不消费响应流，断连仍可传到聊天持久化 finally。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            from starlette.requests import HTTPConnection

            request = HTTPConnection(scope)
            hosts = {"localhost", "127.0.0.1", "::1"} | {
                x.strip().lower()
                for x in os.getenv("SW_ALLOWED_HOSTS", "").split(",")
                if x.strip()
            }
            expected = (
                os.getenv("SW_PUBLIC_ORIGIN", "").strip().rstrip("/")
                or ("https" if request.url.scheme == "wss" else "http")
                + "://"
                + request.url.netloc
            )
            origins = {expected} | {
                x.strip()
                for x in os.getenv("SW_ALLOWED_ORIGINS", "").split(",")
                if x.strip()
            }
            origin = request.headers.get("origin")
            auth = request.headers.get("authorization", "")
            supplied = (
                auth[7:]
                if auth.lower().startswith("bearer ")
                else request.cookies.get(COOKIE_NAME, "")
            )
            if (
                request.url.hostname not in hosts
                or (origin and origin not in origins)
                or not supplied
                or not secrets.compare_digest(supplied, access_token())
            ):
                return await send({"type": "websocket.close", "code": 1008})
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request

        request = Request(scope)
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        allowed_hosts.update(
            x.strip().lower()
            for x in os.getenv("SW_ALLOWED_HOSTS", "").split(",")
            if x.strip()
        )
        host = request.url.hostname or ""
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        # 反代部署显式声明公网Origin；不信任客户端任意伪造的Forwarded头。
        public_origin = os.getenv("SW_PUBLIC_ORIGIN", "").strip().rstrip("/")
        if public_origin:
            public = urlsplit(public_origin)
            if (
                public.scheme not in {"http", "https"}
                or not public.netloc
                or public.path
                or public.query
                or public.fragment
            ):
                raise RuntimeError("SW_PUBLIC_ORIGIN 必须是完整的 http(s) Origin")
            expected_origin = public_origin
        allowed_origins = {expected_origin}
        allowed_origins.update(
            x.strip()
            for x in os.getenv("SW_ALLOWED_ORIGINS", "").split(",")
            if x.strip()
        )
        if host.lower() not in allowed_hosts:
            return await JSONResponse(
                {"detail": "啧，Host 不在白名单。"}, status_code=400
            )(scope, receive, send)
        if origin and origin not in allowed_origins:
            return await JSONResponse({"detail": "跨站请求已拦截。"}, status_code=403)(
                scope, receive, send
            )
        if request.method == "OPTIONS" and request.headers.get(
            "access-control-request-method"
        ):
            # 预检无凭据，只允许已通过Host/Origin校验的CORS层响应。
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/api/auth/bootstrap":
            try:
                local_client = ipaddress.ip_address(request.client.host).is_loopback
            except (ValueError, AttributeError):
                local_client = False
            if (
                not local_client
                or host not in {"localhost", "127.0.0.1", "::1"}
                or request.headers.get("sec-fetch-site", "none")
                not in {"same-origin", "none"}
                or (origin and origin != expected_origin)
            ):
                return await JSONResponse(
                    {"detail": "需要本机同源访问或访问令牌。"}, status_code=403
                )(scope, receive, send)
            response = JSONResponse({"ok": True})
            response.set_cookie(
                COOKIE_NAME,
                access_token(),
                httponly=True,
                samesite="strict",
                secure=expected_origin.startswith("https://"),
                max_age=86400,
            )
            return await response(scope, receive, send)
        # 默认拒绝：只放行登录闸门自身依赖的前端壳（首页 / 前端源码 / 静态素材），
        # 其余路径一律要令牌 —— 新加路由不必再回头补受保护前缀
        public_shell = path in {"/", "/frontend", "/static"} or path.startswith(
            ("/frontend/", "/static/")
        )
        protected = not public_shell
        if protected:
            authorization = request.headers.get("authorization", "")
            bearer = (
                authorization[7:] if authorization.lower().startswith("bearer ") else ""
            )
            provided = bearer or request.cookies.get(COOKIE_NAME, "")
            if not provided or not secrets.compare_digest(provided, access_token()):
                return await JSONResponse(
                    {"detail": "访问令牌没对上。先从本机页面进来。"}, status_code=401
                )(scope, receive, send)
            if path == "/api/auth/logout" and request.method == "POST":
                response = JSONResponse({"ok": True})
                response.delete_cookie(COOKIE_NAME)
                return await response(scope, receive, send)
            if path == "/api/auth/rotate" and request.method == "POST":
                try:
                    token = rotate_token()
                except ValueError as exc:
                    return await JSONResponse({"detail": str(exc)}, status_code=409)(
                        scope, receive, send
                    )
                response = JSONResponse({"ok": True, "token_file": "data/auth_token"})
                response.set_cookie(
                    COOKIE_NAME,
                    token,
                    httponly=True,
                    samesite="strict",
                    secure=expected_origin.startswith("https://"),
                    max_age=86400,
                )
                logging.getLogger("audit.changes").info(
                    "轮换本机访问令牌",
                    extra={
                        "change": {
                            "action": "auth_rotate",
                            "detail": {"scope": "local"},
                        }
                    },
                )
                return await response(scope, receive, send)
            if path == "/api/auth/login" and request.method == "POST":
                response = JSONResponse({"ok": True})
                response.set_cookie(
                    COOKIE_NAME,
                    access_token(),
                    httponly=True,
                    samesite="strict",
                    secure=expected_origin.startswith("https://"),
                    max_age=86400,
                )
                return await response(scope, receive, send)
        # 在multipart解析前封顶；分块请求同样逐包计数，不只相信Content-Length。
        limit = (
            4 * 1024 * 1024
            if path.startswith(("/api/avatar/", "/api/appearance-presets/backgrounds"))
            else (
                310 * 1024 * 1024
                if path in {"/api/chat/attachments", "/api/knowledge/upload"}
                else (
                    3 * 1024 * 1024 if path == "/api/skills/import" else 2 * 1024 * 1024
                )
            )
        )
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            return await JSONResponse(
                {"detail": "请求体超过允许大小"}, status_code=413
            )(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            packet = await receive()
            received += len(packet.get("body", b""))
            if received > limit:
                from starlette.exceptions import HTTPException

                raise HTTPException(status_code=413, detail="请求体超过允许大小")
            return packet

        await self.app(scope, bounded_receive, send)
