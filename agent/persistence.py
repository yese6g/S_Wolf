"""Agent 本地状态存储：计划与任务共享原子 JSON 写入，调用方传自己的目录与标识。

线程锁覆盖读改写，随机临时文件避免固定 .tmp 互相覆盖；不向量化、不联网。
"""
import json
from pathlib import Path
import re
import threading
import uuid
from functools import wraps

LOCK = threading.RLock()


def transaction(handler):
    """让调用方的完整读改写共用原子存储锁，不只锁最后一次写文件。"""
    @wraps(handler)
    def run(*args, **kwargs):
        with LOCK:
            return handler(*args, **kwargs)
    return run


def location(directory, identity):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(identity)):
        raise ValueError("状态标识无效")
    return Path(directory) / f"{identity}.json"


def load(directory, identity, default=None):
    with LOCK:
        path = location(directory, identity)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(directory, identity, value):
    with LOCK:
        path = location(directory, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
