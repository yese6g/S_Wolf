"""MCP客户端：API显式连接stdio或Streamable HTTP，工具注册到现有仓库。

只启动用户预先写入 data/mcp_servers.json 的固定命令，不执行模型提交的命令。
支持initialize、工具、资源与提示词读取；调用工具仍需审批。本机HTTP须单独授权，
出站请求固定解析地址、禁重定向并限1MiB，不使用旧版独立SSE传输。
有超时、消息大小上限、stderr 排空与进程清理，不声称支持所有 MCP 扩展。
"""

import atexit
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit
from config_data import BASE_DIR, APP_VERSION
from agent import persistence
from middleware.logging_system import redact, register_secret

logger = logging.getLogger(__name__)

CONFIG_FILE = BASE_DIR / "data" / "mcp_servers.json"
CLIENTS = {}
_LOCK = threading.RLock()


def configured():
    if not CONFIG_FILE.exists():
        return {}
    value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return value.get("servers", {})


def catalog():
    return [
        {
            "name": name,
            "connected": name in CLIENTS,
            **{k: v for k, v in spec.items() if k not in {"env", "headers"}},
            "environment_keys": list((spec.get("env") or {}).keys()),
        }
        for name, spec in configured().items()
    ]


def save(name, spec):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", name):
        raise ValueError("MCP标识用字母数字下划线，最多24字符")
    transport = spec.get("transport", "stdio")
    if transport not in {"stdio", "http"}:
        raise ValueError("使用stdio或Streamable HTTP")
    if transport == "stdio" and (
        not isinstance(spec.get("command"), str) or not spec["command"].strip()
    ):
        raise ValueError("stdio需要固定可执行命令")
    if transport == "http":
        endpoint = urlsplit(str(spec.get("url", "")))
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.fragment
        ):
            raise ValueError("MCP地址无效")
        if spec.get("allow_loopback") and endpoint.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("本机授权只允许回环地址")
    if (
        not isinstance(spec.get("args", []), list)
        or len(spec.get("args", [])) > 64
        or any(not isinstance(a, str) or len(a) > 8192 for a in spec.get("args", []))
    ):
        raise ValueError("args需要最多64项字符串数组，不是shell命令串")
    if spec.get("category", "core") not in {"core", "agent", "python", "cyberSecurity"}:
        raise ValueError("分类无效")
    if not isinstance(spec.get("env_refs", {}), dict) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(v))
        for v in spec.get("env_refs", {}).values()
    ):
        raise ValueError("环境凭据填写变量名")
    with _LOCK, persistence.LOCK:
        rows = configured()
        old = rows.get(name, {})
        if name not in rows and len(rows) >= 20:
            raise ValueError("最多20个MCP服务器")
        if name in CLIENTS:
            raise ValueError("先断开服务器，再改配置")
        rows[name] = {
            **old,
            "transport": transport,
            "command": str(spec.get("command", "")).strip(),
            "args": spec.get("args", []),
            "cwd": spec.get("cwd") or str(BASE_DIR),
            "url": spec.get("url", ""),
            "allow_loopback": bool(spec.get("allow_loopback", False)),
            "env_refs": spec.get("env_refs", {}),
            "category": spec.get("category", "core"),
        }
        persistence.save(CONFIG_FILE.parent, CONFIG_FILE.stem, {"servers": rows})
    return {"ok": True}


def delete(name):
    with _LOCK, persistence.LOCK:
        rows = configured()
        if name not in rows:
            raise ValueError("服务器不存在")
        disconnect(name)
        rows.pop(name)
        persistence.save(CONFIG_FILE.parent, CONFIG_FILE.stem, {"servers": rows})
    return {"ok": True}


class HttpClient:
    """Streamable HTTP按请求接收JSON或SSE结果，不连接旧版独立SSE传输。"""

    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        self.session = ""
        self.counter = 0
        self.lock = threading.Lock()
        self.diagnostic = ""
        self.closed = False
        self.version = "2025-11-25"
        hello = self.request(
            "initialize",
            {
                "protocolVersion": self.version,
                "capabilities": {},
                "clientInfo": {"name": "SilverWolf", "version": APP_VERSION},
            },
        )
        if hello.get("protocolVersion") not in {
            "2024-11-05",
            "2025-03-26",
            "2025-06-18",
            "2025-11-25",
        }:
            raise ValueError("MCP版本不兼容")
        self.version = hello["protocolVersion"]
        self.capabilities = hello.get("capabilities", {})
        self.notify("notifications/initialized", {})

    def send(self, packet):
        from middleware.outbound import request_public

        if self.closed:
            raise ValueError("MCP连接已关闭")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
            "MCP-Protocol-Version": self.version,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        for header, reference in self.spec.get("env_refs", {}).items():
            if reference not in os.environ:
                raise ValueError("MCP请求头环境凭据未配置")
            value = os.environ[reference]
            register_secret(value)
            headers[header] = value
        result = request_public(
            self.spec["url"],
            "POST",
            headers,
            packet,
            [urlsplit(self.spec["url"]).hostname],
            max_bytes=1024 * 1024,
            max_chars=None,
            response_info=True,
            allow_loopback=self.spec.get("allow_loopback", False),
            rpc_id=packet.get("id"),
        )
        if result["session_id"]:
            if len(result["session_id"]) > 512:
                raise ValueError("MCP会话标识过长")
            self.session = result["session_id"]
            register_secret(self.session)
        if "id" not in packet:
            return {}
        answer = json.loads(result["text"])
        if answer.get("id") != packet["id"]:
            raise ValueError("MCP响应标识不匹配")
        if answer.get("error"):
            raise ValueError(redact(str(answer["error"]))[:300])
        return answer.get("result", {})

    def request(self, method, params):
        with self.lock:
            self.counter += 1
            return self.send(
                {
                    "jsonrpc": "2.0",
                    "id": self.counter,
                    "method": method,
                    "params": params,
                }
            )

    def notify(self, method, params):
        return self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self):
        self.closed = True


class StdioClient:
    def __init__(self, name, spec):
        self.name = name
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.responses = queue.Queue(maxsize=256)
        self.counter = 0
        self.diagnostic = ""
        environment = {**os.environ, **spec.get("env", {})}
        for key, reference in spec.get("env_refs", {}).items():
            if reference not in os.environ:
                raise ValueError("MCP环境凭据未配置")
            environment[key] = os.environ[reference]
            register_secret(environment[key])
        self.proc = subprocess.Popen(
            [spec["command"], *spec.get("args", [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            cwd=spec.get("cwd") or BASE_DIR,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.stderr_reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        self.stderr_reader.start()
        try:
            hello = self.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "SilverWolf", "version": "3.0.0"},
                },
            )
            if hello.get("protocolVersion") not in {
                "2024-11-05",
                "2025-03-26",
                "2025-06-18",
                "2025-11-25",
            }:
                raise ValueError("MCP 协议版本不兼容，本客户端支持 initialize 系列")
            self.capabilities = hello.get("capabilities", {})
            self.notify("notifications/initialized", {})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.proc.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("MCP 消息超过 1 MiB")
                packet = json.loads(line)
                if "id" in packet and "method" in packet:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": packet["id"],
                            "error": {
                                "code": -32601,
                                "message": "Client capability not supported",
                            },
                        }
                    )
                elif "id" in packet:
                    self.responses.put(packet, timeout=1)
        except Exception as exc:
            try:
                self.responses.put({"_error": str(exc)}, timeout=1)
            except queue.Full:
                pass
        finally:
            try:
                self.responses.put({"_error": "MCP 进程输出已关闭"}, timeout=1)
            except queue.Full:
                pass

    def _drain(self):
        try:
            while True:
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    break
                self.diagnostic = (self.diagnostic + redact(chunk))[-4000:]
        except (OSError, ValueError):
            pass

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, packet):
        content = json.dumps(packet, allow_nan=False)
        if len(content.encode()) > 1024 * 1024:
            raise ValueError("MCP请求超过1MiB")
        with self.write_lock:
            self.proc.stdin.write(content + "\n")
            self.proc.stdin.flush()

    def request(self, method, params):
        with self.lock:
            self.counter += 1
            identity = self.counter
            self._send(
                {"jsonrpc": "2.0", "id": identity, "method": method, "params": params}
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    packet = self.responses.get(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                except queue.Empty:
                    self.notify(
                        "notifications/cancelled",
                        {"requestId": identity, "reason": "Client timeout"},
                    )
                    raise TimeoutError("MCP请求超过20秒，已发送取消通知") from None
                if "_error" in packet:
                    raise ValueError(packet["_error"])
                if packet.get("id") != identity:
                    continue
                if packet.get("error"):
                    raise ValueError(str(packet["error"])[:300])
                return packet.get("result", {})
            raise TimeoutError("MCP 请求超时")

    def close(self):
        if self.proc.poll() is None:
            # Windows 只 terminate 直接子进程会留下孙进程（npx→node 等）在后台跑，
            # 与 developer/terminal 同口径：taskkill /T 杀整棵进程树
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=5,
                    )
                except Exception:
                    logger.warning("MCP taskkill 失败或超时 pid=%s，改直接 kill", self.proc.pid)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.reader.join(timeout=1)
        self.stderr_reader.join(timeout=1)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()


def connect(name):
    """连接已配置服务器并注册命名空间工具，返回发现的工具名列表。"""
    from agent import tools

    if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", name):
        raise ValueError("MCP 服务器名无效")
    spec = configured().get(name)
    if not spec:
        raise ValueError("先配置这个 MCP 服务器")
    with _LOCK:
        disconnect(name)
        client = (
            HttpClient(name, spec)
            if spec.get("transport") == "http"
            else StdioClient(name, spec)
        )
        names = []
        try:
            cursor = None
            for _ in range(10 if "tools" in client.capabilities else 0):
                result = client.request(
                    "tools/list", {"cursor": cursor} if cursor else {}
                )
                for item in result.get("tools", []):
                    original = item["name"]
                    safe = re.sub(r"[^A-Za-z0-9_]", "_", original)[:32]
                    tool_name = f"mcp__{name}__{safe}"
                    if tool_name in names or any(
                        t["name"] == tool_name for t in tools.list_tools()
                    ):
                        raise ValueError("MCP工具名归一化后冲突，不能覆盖已有工具")

                    def handler(args, original=original, client=client):
                        data = client.request(
                            "tools/call", {"name": original, "arguments": args}
                        )
                        text = "\n".join(
                            x.get("text", "")
                            for x in data.get("content", [])
                            if x.get("type") == "text"
                        )
                        return text or json.dumps(data, ensure_ascii=False), {
                            "ok": not data.get("isError", False),
                            "mcp_server": name,
                        }

                    with tools.EXTERNAL_LOCK:
                        tools.EXTERNAL_TOOLS[tool_name] = {
                            "description": item.get("description", original),
                            "parameters": item.get("inputSchema", {"type": "object"}),
                            "handler": handler,
                            "approval_required": True,
                            "risks": ["external", "process", "network"],
                            "category": spec.get("category", "core"),
                        }
                    names.append(tool_name)
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            CLIENTS[name] = client
            return names
        except BaseException:
            client.close()
            with tools.EXTERNAL_LOCK:
                for tool in names:
                    tools.EXTERNAL_TOOLS.pop(tool, None)
            raise


def disconnect(name):
    from agent import tools

    with _LOCK:
        client = CLIENTS.pop(name, None)
        if client:
            client.close()
        with tools.EXTERNAL_LOCK:
            for tool in list(tools.EXTERNAL_TOOLS):
                if tool.startswith(f"mcp__{name}__"):
                    tools.EXTERNAL_TOOLS.pop(tool, None)


def shutdown():
    for name in list(CLIENTS):
        disconnect(name)


atexit.register(shutdown)
