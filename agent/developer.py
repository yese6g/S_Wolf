"""Lv.999项目操作：工作台显式授权后读改源码、查看差异和运行命令。

授权只在内存保留30分钟，令牌轮换/重启失效。变更复用workspace_history，
命令复用jobs记录及线程池；无额外数据库、常驻助手或系统管理员权限。
"""

import base64
import codecs
import difflib
import hashlib
import logging
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import uuid
from config_data import BASE_DIR
from agent import jobs, persistence, workspace_history
from middleware import security
from middleware.logging_system import redact, register_secret

GRANTS = {}
PROCESSES = {}
LOCK = threading.RLock()
logger = logging.getLogger("audit.tools")
# 切换工作区会吊销全部授权；代次号让 authorize 能发现「读根之后、建授权之前」的切换。
_WORKSPACE_GEN = 0
_STATE_LOCK = threading.RLock()
_STATE = {"key": None, "value": {"root": "", "recent": []}}
PROTECTED = {
    ".git",
    ".venv",
    ".agents",
    ".codex",
    "data",
    "knowledge_bases",
    "chroma_db",
    "models",
    "tmp",
    "node_modules",
    "__pycache__",
}
EXTENSIONS = {
    ".py",
    ".js",
    ".css",
    ".html",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".bat",
    ".sh",
    ".ini",
}


def authorize(write=False, commands=False, terminal=False):
    # 先取代次再读根：两者之间发生切换时，下面的校验会拒绝这次授权。
    with LOCK:
        generation = _WORKSPACE_GEN
    workspace = str(current_root())
    with LOCK:
        for key, row in list(GRANTS.items()):
            if row["expires"] < time.time():
                GRANTS.pop(key)
        if generation != _WORKSPACE_GEN:
            raise ValueError("工作区刚切换过，请重新确认后授权")
        if len(GRANTS) >= 16:
            raise ValueError("授权数量已满，先撤销旧授权")
        key = secrets.token_urlsafe(32)
        register_secret(key)
        GRANTS[key] = {
            "expires": time.time() + 1800,
            "write": bool(write),
            "commands": bool(commands),
            "terminal": bool(terminal and commands),
            "workspace": workspace,
            "principal": hashlib.sha256(security.access_token().encode()).hexdigest(),
        }
        return {
            "grant": key,
            **{k: v for k, v in GRANTS[key].items() if k != "principal"},
        }


def require(key, permission=None):
    with LOCK:
        row = GRANTS.get(key or "")
        if (
            not row
            or row["expires"] <= time.time()
            or row["principal"]
            != hashlib.sha256(security.access_token().encode()).hexdigest()
        ):
            raise PermissionError("工作区授权已失效，请重新开启")
        if permission and not row.get(permission):
            raise PermissionError("本次没有授予这项权限")
        return row


def revoke(key):
    with LOCK:
        GRANTS.pop(key, None)
    return {"ok": True}


def _state():
    directory = Path(BASE_DIR) / "data"
    key = str(directory)
    with _STATE_LOCK:
        if _STATE["key"] != key:
            raw = persistence.load(directory, "workspace")
            if not isinstance(raw, dict):
                raw = {}
            root = raw.get("root")
            recent = raw.get("recent")
            stored_root = root if isinstance(root, str) else ""
            recent_list = [
                item
                for item in (recent if isinstance(recent, list) else [])
                if isinstance(item, str) and item
            ][:5]
            # workspaces 是 DSH 那样的常驻列表：加过的都留着，随时点回去。
            # 旧数据没有这个字段，用「当前根 + recent」播种，保证升级后列表不空。
            workspaces = raw.get("workspaces")
            if not isinstance(workspaces, list) or not workspaces:
                workspaces = [
                    item for item in ([stored_root] if stored_root else []) + recent_list if item
                ]
            row = {
                "root": stored_root,
                "recent": recent_list,
                "workspaces": _dedupe_paths(workspaces),
                "updated_at": raw.get("updated_at", 0),
            }
            _STATE.update(key=key, value=row)
            if row["root"] and not Path(row["root"]).is_dir():
                logger.warning("工作区目录不存在，已回退默认：%s", row["root"])
        return _STATE["value"]


def _dedupe_paths(items, limit=12):
    """按 Windows 口径去重（大小写不敏感），保序，限制条数。"""
    seen, out = set(), []
    for item in items:
        if not isinstance(item, str) or not item:
            continue
        mark = os.path.normcase(str(Path(item)))
        if mark in seen:
            continue
        seen.add(mark)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def current_root():
    root = _state()["root"]
    if root and Path(root).is_dir():
        return Path(root)
    return Path(BASE_DIR)


def workspace_info():
    state = _state()
    stored = state["root"]
    stale = bool(stored) and not Path(stored).is_dir()
    active = Path(stored) if stored and not stale else Path(BASE_DIR)
    default_path = str(Path(BASE_DIR))
    # 常驻列表：**当前工作区永远排第一行**（哪怕是"默认工作区"），后面跟着加过的与最近用过的；
    # 缺目录的仍然列出（标 missing）让人能看见并移除。
    # 这样把当前项移出列表、回退默认之后，侧栏仍有一行高亮，用户不会"看不出自己在哪"。
    listed = _dedupe_paths([state["root"], *state["workspaces"], *state["recent"]])
    if os.path.normcase(str(active)) not in {os.path.normcase(str(Path(i))) for i in listed}:
        listed.insert(0, str(active))
    workspaces = [
        {
            "path": item,
            "name": Path(item).name or item,
            "active": os.path.normcase(str(Path(item))) == os.path.normcase(str(active)),
            # 默认工作区是服务端项目目录，不在常驻列表里，因此不可移除（前端据此隐藏 ×）
            "default": os.path.normcase(str(Path(item))) == os.path.normcase(default_path),
            "missing": not Path(item).is_dir(),
        }
        for item in listed
    ]
    return {
        "root": str(active),
        "default": str(Path(BASE_DIR)),
        "is_default": not stored or stale,
        "stale": stale,
        "workspaces": workspaces,
        # 兼容旧前端：旧字段仍返回"除当前项以外"的列表
        "recent": [w["path"] for w in workspaces if not w["active"]],
    }


def set_workspace(path=None, default=False):
    global _WORKSPACE_GEN
    if default:
        resolved = Path(BASE_DIR).resolve()
        stored = ""
    else:
        raw = str(path or "").strip()
        if not raw or not Path(raw).is_absolute():
            raise ValueError("工作区必须是绝对路径")
        resolved = Path(raw).resolve()
        if not resolved.exists():
            raise FileNotFoundError("工作区目录不存在")
        if not resolved.is_dir():
            raise ValueError("工作区必须是一个目录")
        server = Path(BASE_DIR).resolve()
        for name in ("data", "chroma_db", "knowledge_bases"):
            guard = server / name
            if resolved == guard or resolved.is_relative_to(guard):
                raise ValueError("这个目录保存服务数据，不能作为工作区")
        stored = "" if resolved == server else str(resolved)
    directory = Path(BASE_DIR) / "data"
    with _STATE_LOCK:
        row = dict(_state())
        changed = os.path.normcase(row["root"]) != os.path.normcase(stored)
        row["root"] = stored
        row["updated_at"] = time.time()
        if stored:
            # 选中即入常驻列表，之后随时能点回去；recent 只作兼容保留
            row["workspaces"] = _dedupe_paths([stored, *row.get("workspaces", [])])
            if changed:
                row["recent"] = [
                    stored,
                    *[
                        item
                        for item in row["recent"]
                        if os.path.normcase(item) != os.path.normcase(stored)
                    ],
                ][:5]
        persistence.save(directory, "workspace", row)
        _STATE.update(key=str(directory), value=row)
    revoked = 0
    terminals_closed = 0
    if changed:
        with LOCK:
            revoked = len(GRANTS)
            GRANTS.clear()
            _WORKSPACE_GEN += 1
        # 懒导入避免与 terminal 的循环依赖；授予已吊销，旧工作区的终端必须收干净。
        from agent import terminal

        terminals_closed = terminal.close_all()
    return {
        "ok": True,
        "root": str(Path(BASE_DIR)) if not stored else stored,
        "is_default": not stored,
        "changed": changed,
        "revoked": revoked,
        "terminals_closed": terminals_closed,
        "recent": list(row["recent"]),
        "workspaces": list(row.get("workspaces", [])),
    }


def remove_workspace(path):
    """从常驻列表里移除一个工作区；移除的正好是当前项时回退默认工作区。

    只动列表，不删磁盘上的任何东西——这是"别再列出来"，不是"删除目录"。
    """
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("需要提供要移除的工作区路径")
    mark = os.path.normcase(str(Path(raw)))
    state = _state()
    target = next((item for item in state["workspaces"] if os.path.normcase(str(Path(item))) == mark), None)
    if target is None:
        raise ValueError("这个工作区不在列表里")
    active = os.path.normcase(state["root"]) == mark
    # 先无条件从列表与历史里摘掉；摘完再决定要不要回退默认。
    # （不能先 set_workspace(default) 再删——那时 root 已空，列表里的旧项会被留下）
    directory = Path(BASE_DIR) / "data"
    with _STATE_LOCK:
        row = dict(_state())
        row["workspaces"] = [
            item for item in row["workspaces"] if os.path.normcase(str(Path(item))) != mark
        ]
        row["recent"] = [item for item in row["recent"] if os.path.normcase(str(Path(item))) != mark]
        row["updated_at"] = time.time()
        persistence.save(directory, "workspace", row)
        _STATE.update(key=str(directory), value=row)
    if active:
        # 复用 set_workspace 的收尾：吊销授权、关终端、切回默认
        result = set_workspace(default=True)
        result["removed"] = target
        return result
    return {"ok": True, "removed": target, "changed": False, "root": str(current_root())}


def browse(raw=None):
    value = str(raw or "").strip()
    if not value:
        info = workspace_info()
        return {
            "drives": _drives(),
            "shortcuts": {
                "home": str(Path.home()),
                "default": str(Path(BASE_DIR)),
            },
            # 常驻列表（含当前项）：选择器里直接点着切换
            "workspaces": info["workspaces"],
            "root": info["root"],
            "recent": info["recent"],
        }
    target = Path(value)
    if not target.is_absolute():
        raise ValueError("浏览需要绝对路径")
    resolved = target.resolve()
    if not resolved.exists():
        raise FileNotFoundError("目录不存在")
    if not resolved.is_dir():
        raise ValueError("不是目录")
    entries = []
    files = []
    with os.scandir(resolved) as scan:
        for item in scan:
            try:
                if item.is_dir():
                    entries.append({"name": item.name, "path": item.path})
                elif item.is_file():
                    # 只报名字与大小：选择工作区时要能看出"这是不是我要的项目"（像 Codex 的文件树），
                    # 但不读内容——内容预览仍然只走已授权的项目接口。
                    try:
                        size = item.stat().st_size
                    except OSError:
                        size = 0
                    files.append({"name": item.name, "size": size})
            except OSError:
                continue
    entries.sort(key=lambda row: row["name"].lower())
    files.sort(key=lambda row: row["name"].lower())
    hidden_dirs = sum(1 for d in entries if d["name"].startswith("."))
    return {
        "path": str(resolved),
        "parent": str(resolved.parent) if resolved != resolved.parent else "",
        "entries": entries[:500],
        "files": files[:500],
        "file_count": len(files),
        "hidden_dir_count": hidden_dirs,
        "truncated": len(entries) > 500 or len(files) > 500,
    }


def _drives():
    if os.name != "nt":
        return ["/"]
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + index)}:\\" for index in range(26) if mask & (1 << index)]


def location(raw="", root=None):
    base = Path(root) if root is not None else current_root()
    value = str(raw).replace("\\", "/")
    relative = Path(value)
    if relative.is_absolute() or ":" in value or ".." in relative.parts:
        raise ValueError("使用工作区内相对路径")
    if any(p in PROTECTED or p.startswith(".") for p in relative.parts):
        raise ValueError("这个目录或隐藏文件受保护")
    boundary = base.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(boundary):
        raise ValueError("路径或链接越界")
    resolved = target.relative_to(boundary)
    if any(p in PROTECTED or p.startswith(".") for p in resolved.parts):
        raise ValueError("链接指向受保护目录")
    server = Path(BASE_DIR).resolve()
    for name in ("data", "chroma_db", "knowledge_bases"):
        guard = server / name
        if target == guard or target.is_relative_to(guard):
            raise ValueError("这个目录保存服务数据，受保护")
    if (
        not target.is_dir()
        and target.suffix not in EXTENSIONS
        and target.name != "Dockerfile"
    ):
        raise ValueError("只处理项目源码和说明文本")
    return target


def files(key, path=""):
    root = str(require(key)["workspace"])
    current = location(path, root)
    if not current.is_dir():
        raise ValueError("不是目录")
    rows = []
    for p in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            target = location(p.relative_to(root).as_posix(), root)
        except ValueError:
            continue
        rows.append(
            {
                "path": p.relative_to(root).as_posix(),
                "name": p.name,
                "directory": target.is_dir(),
            }
        )
    return {"files": rows[:300], "path": path}


def read(key, path):
    target = location(path, require(key)["workspace"])
    if not target.is_file() or target.stat().st_size > 1024 * 1024:
        raise ValueError("文件不存在或超过1MiB")
    raw = target.read_bytes()
    text = raw.decode("utf-8-sig")
    return {"path": path, "content": text, "revision": hashlib.sha256(raw).hexdigest()}


def preview(key, path, content):
    target = location(path, require(key)["workspace"])
    prior = (
        read(key, path) if target.exists() else {"content": "", "revision": "absent"}
    )
    if len(content.encode()) > 1024 * 1024:
        raise ValueError("文件超过1MiB")
    return {
        "diff": "".join(
            difflib.unified_diff(
                prior["content"].splitlines(True),
                content.splitlines(True),
                fromfile="原文件/" + path,
                tofile="新文件/" + path,
            )
        )[:200000],
        "revision": prior["revision"],
    }


def write(key, path, content, revision):
    root = require(key, "write")["workspace"]
    with persistence.LOCK:
        target = location(path, root)
        prior = read(key, path) if target.exists() else {"revision": "absent"}
        if prior["revision"] != revision:
            raise ValueError("文件已变化，重新读取并核对差异")
        raw = content.encode("utf-8")
        if len(raw) > 1024 * 1024:
            raise ValueError("文件超过1MiB")
        before = workspace_history.snapshot(target)
        workspace_history.reserve(len(before["data"]) + len(raw) * 2 + 2048)
        temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(raw)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        change = workspace_history.record(
            path,
            before,
            workspace_history.snapshot(target),
            scope="project",
            workspace=root,
        )
        audit("project_write", {"path": path, "change_id": change})
        return {**read(key, path), "change_id": change}


def rollback(key, identity):
    root = require(key, "write")["workspace"]
    with persistence.LOCK:
        row = persistence.load(workspace_history.HISTORY_DIR, identity)
        if not row or row.get("scope") != "project":
            raise ValueError("不是项目变更")
        recorded = row.get("workspace") or str(BASE_DIR)
        if os.path.normcase(recorded) != os.path.normcase(root):
            raise ValueError("这条变更来自另一个工作区，不能在这里回滚")
        target = location(row["path"], recorded)
        if workspace_history.snapshot(target)["sha"] != row["after"]["sha"]:
            raise ValueError("文件后来被改过，先核对差异")
        if row["before"]["kind"] == "absent":
            workspace_history.reserve(
                len(row["after"]["data"]) + 2048, protected=(identity,)
            )
            before = workspace_history.snapshot(target)
            target.unlink()
            change = workspace_history.record(
                row["path"],
                before,
                workspace_history.snapshot(target),
                scope="project",
                workspace=recorded,
            )
            audit("project_rollback", {"path": row["path"], "change_id": change})
            return {"ok": True, "change_id": change}
        restored = base64.b64decode(row["before"]["data"]).decode("utf-8-sig")
        return write(key, row["path"], restored, row["after"]["sha"])


def audit(action, detail):
    import logging

    logging.getLogger("audit.changes").info(
        action, extra={"change": {"action": action, "detail": detail}}
    )


def git_status(key):
    response = subprocess.run(
        ["git", "status", "--short"],
        cwd=require(key)["workspace"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return {
        "text": redact(response.stdout + response.stderr),
        "ok": response.returncode == 0,
    }


def start_command(key, argv, approved=False):
    grant = require(key, "commands")
    if approved is not True:
        raise PermissionError("确认完整命令后才能运行")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or any(not isinstance(a, str) or "\x00" in a or len(a) > 8192 for a in argv)
    ):
        raise ValueError("命令必须是可执行文件与参数组成的JSON数组")
    # 命令不在操作系统沙箱运行，授权说明必须明确其以当前用户身份执行。
    with jobs._LOCK:
        if len(jobs._ACTIVE) >= 8:
            raise ValueError("任务槽位已满")
        history = jobs.list_jobs()
        for old in reversed(history):
            if len(history) < 100:
                break
            if old["id"] not in jobs._ACTIVE and old.get("status") not in {
                "queued",
                "running",
            }:
                persistence.location(jobs.JOB_DIR, old["id"]).unlink(missing_ok=True)
                history.remove(old)
        if len(history) >= 100:
            raise ValueError("任务记录已满")
        if jobs._POOL is None:
            from concurrent.futures import ThreadPoolExecutor

            jobs._POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sw-job")
        identity = "job_" + uuid.uuid4().hex
        cancel = threading.Event()
        row = {
            "id": identity,
            "kind": "command",
            "status": "queued",
            "created_at": time.time(),
            "message": redact(" ".join(argv)),
            "text": "",
            "events": [],
        }
        persistence.save(jobs.JOB_DIR, identity, row)
        jobs._ACTIVE[identity] = cancel
        jobs._FUTURES[identity] = jobs._POOL.submit(
            _command, row, argv, cancel, key, grant["workspace"]
        )

        def completed(_):
            with jobs._LOCK:
                jobs._FUTURES.pop(identity, None)

        jobs._FUTURES[identity].add_done_callback(completed)
    audit("project_command", {"job_id": identity, "executable": Path(argv[0]).name})
    return row


def _terminate(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
        except Exception:
            # taskkill 超时/失败不能往上抛：finally 里的落盘与槽位释放还要跑
            logger.warning("taskkill 失败或超时 pid=%s，改直接 kill", proc.pid)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            logger.warning("进程 kill 失败 pid=%s", proc.pid)


def _command(row, argv, cancel, key, workspace):
    proc = None
    reader = None
    deadline = time.monotonic() + 300
    try:
        require(key, "commands")
        proc = subprocess.Popen(
            argv,
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with LOCK:
            PROCESSES[row["id"]] = (proc, key)
        row["status"] = "running"

        def collect():
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                raw = proc.stdout.read1(4096)
                text = decoder.decode(raw, final=not raw)
                with jobs._LOCK:
                    row["text"] = (row["text"] + redact(text))[-200000:]
                    persistence.save(jobs.JOB_DIR, row["id"], row)
                if not raw:
                    break

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        persistence.save(jobs.JOB_DIR, row["id"], row)
        while proc.poll() is None:
            if cancel.wait(0.1):
                break
            if time.monotonic() > deadline:
                row["error"] = "命令超过5分钟"
                cancel.set()
                break
            try:
                require(key, "commands")
            except PermissionError:
                row["error"] = "授权失效"
                cancel.set()
                break
        if cancel.is_set():
            _terminate(proc)
        proc.wait(timeout=5)
        reader.join(timeout=2)
        row.update(
            status=(
                "cancelled"
                if cancel.is_set()
                else "done" if proc.returncode == 0 else "failed"
            ),
            exit_code=proc.returncode,
        )
    except Exception as exc:
        row.update(status="failed", error=redact(str(exc))[:300])
    finally:
        if proc:
            # 清理再失败也不能拦住落盘与槽位释放，否则命令槽位泄漏、磁盘状态永远 running
            try:
                _terminate(proc)
                if reader:
                    reader.join(timeout=2)
                for stream in (proc.stdin, proc.stdout):
                    if stream:
                        stream.close()
            except Exception:
                logger.exception("命令进程清理失败 job=%s", row["id"])
        row["finished_at"] = time.time()
        persistence.save(jobs.JOB_DIR, row["id"], row)
        with jobs._LOCK:
            jobs._ACTIVE.pop(row["id"], None)
            jobs._FUTURES.pop(row["id"], None)
        with LOCK:
            PROCESSES.pop(row["id"], None)


def command_input(key, identity, text):
    require(key, "commands")
    with LOCK:
        proc, owner = PROCESSES.get(identity, (None, None))
        if not proc or owner != key or proc.poll() is not None:
            raise ValueError("这个命令已结束或不属于当前授权")
        if len(text) > 4096:
            raise ValueError("输入过长")
    # 不能在管道写入期间持有授权锁；不读stdin的进程仍必须可取消。
    proc.stdin.write((text + "\n").encode())
    proc.stdin.flush()
    return {"ok": True}
