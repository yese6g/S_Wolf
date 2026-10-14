"""
logging_system.py - 本地日志总管，由 main 在导入业务模块前初始化。

主日志收集全站记录，分类日志按 logger 名称/等级分流；使用标准库轮转，不加联网依赖。
runner、工具审计和 pytest 使用同一入口。格式化后统一脱敏，连异常堆栈也覆盖；
只记录运行元数据，不主动写入对话正文、工具参数、文件内容或请求体。
"""

from __future__ import annotations

import logging
import json
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config_data import BASE_DIR

LOG_DIR = BASE_DIR / "data" / "logs"
_LOCK = threading.RLock()
_SECRETS: set[str] = set()
_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([\"']?(?:api[_-]?key|access_token|refresh_token|token|password|secret|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'&}]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]+"), "[REDACTED]"),
    (re.compile(r"(https?://)[^/\s@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
)


def register_secret(value: str) -> None:
    """登记运行时密钥，仅保存在内存，供日志全文替换；不读取用户配置文件。"""
    if value:
        with _LOCK:
            _SECRETS.add(value)


def redact(text: str) -> str:
    """返回脱敏文本，覆盖已登记密钥及常见认证字段。"""
    with _LOCK:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _RedactedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class _ChangeFormatter(logging.Formatter):
    """唯一修改账本为 JSONL，页面直接读取；总管仍保存简短运行摘要。"""
    def format(self, record):
        entry = getattr(record, 'change', None) or {
            'action': 'maintenance', 'detail': {'summary': record.getMessage()}}
        return redact(json.dumps({'ts': int(record.created), **entry}, ensure_ascii=False, default=str))


class _CategoryFilter(logging.Filter):
    def __init__(self, category: str):
        super().__init__()
        self.category = category

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        # 请求总管已记录路由模板；uvicorn.access 原始URI会泄露查询和会话ID。
        if name == 'uvicorn.access' or (name in {'httpx','httpcore'} and record.levelno<logging.WARNING):
            return False
        return {
            "main": True,
            "tools": name.startswith("agent.tools") or name in {'audit.tools','agent.jobs','agent.mcp_client'},
            "middleware": name.startswith("middleware."),
            "errors": record.levelno >= logging.ERROR,
            "changes": name == "audit.changes",
            "tests": name == "audit.tests",
            "requests": name == "audit.requests",
        }[self.category]


def configure_logging(log_dir: Path | None = None) -> Path:
    """幂等配置主/分类日志，返回目录；单文件 5 MiB，保留 5 个备份。

    log_dir 供隔离测试使用。只替换本模块创建的 handler，不拆掉 pytest 的捕获器。
    """
    directory = Path(log_dir or LOG_DIR).resolve()
    with _LOCK:
        root = logging.getLogger()
        owned = [h for h in root.handlers if getattr(h, "_sw_owned", False)]
        if owned and all(getattr(h, "_sw_directory", None) == directory for h in owned):
            return directory
        directory.mkdir(parents=True, exist_ok=True)
        formatter = _RedactedFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        # 先完整创建新 handler，避免某个文件打不开时拆掉原日志链。
        handlers = []
        try:
            for category in ("main", "tools", "middleware", "errors", "changes", "tests", "requests"):
                handler = RotatingFileHandler(directory / f"{category}.log", maxBytes=5 * 1024 * 1024,
                                              backupCount=5, encoding="utf-8", delay=True)
                handler.setFormatter(_ChangeFormatter() if category == 'changes' else formatter)
                handler.addFilter(_CategoryFilter(category))
                handlers.append(handler)
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.addFilter(_CategoryFilter('main'))
            handlers.append(console)
        except Exception:
            for handler in handlers:
                handler.close()
            raise
        for handler in owned:
            root.removeHandler(handler)
            handler.close()
        for handler in handlers:
            handler._sw_owned = True
            handler._sw_directory = directory
            root.addHandler(handler)
        root.setLevel(logging.INFO)
        # Uvicorn 的专用 handler 会绕开根 logger；统一交给总管，避免重复/漏记。
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            target = logging.getLogger(name)
            target.handlers.clear()
            target.propagate = True
        logging.captureWarnings(True)
        return directory
