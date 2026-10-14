"""Windows交互终端：工作台WebSocket调用ConPTY，授权、回放和进程生命周期在此收口。

会话及最近输出只留内存（2MiB/项），最多4个活跃终端；断开15秒可重连，
授权到期/撤销/关闭时只清理本模块创建的进程。不写runtime、PID或控制台日志副本。
"""

import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from agent import developer
from middleware.logging_system import redact

SESSIONS = {}
LOCK = threading.RLock()
logger = logging.getLogger("audit.tools")


def require(grant):
    row = developer.require(grant, "commands")
    if not row.get("terminal"):
        raise PermissionError("交互终端需要独立授权，不能用普通命令授权代替")
    return row


def get(identity, grant):
    require(grant)
    row = SESSIONS.get(identity)
    if not row or row["grant"] != grant:
        raise PermissionError("终端不存在或不属于当前授权")
    return row


def open_session(grant, cols=80, rows=24):
    workspace = require(grant)["workspace"]
    if os.name != "nt":
        raise ValueError("当前交互终端使用Windows ConPTY；其他平台仍可用命令控制台")
    from winpty import PTY, Backend

    with LOCK:
        for identity, row in list(SESSIONS.items()):
            if row["done"] and not row["clients"]:
                SESSIONS.pop(identity)
        if sum(not row["done"] for row in SESSIONS.values()) >= 4:
            raise ValueError("最多同时打开4个交互终端")
        shell = shutil.which("pwsh.exe") or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        environment = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # 直接使用非阻塞ConPTY读写，避免高层包装的阻塞读取把输入锁到命令结束。
        proc = PTY(cols, rows, backend=Backend.ConPTY)
        initializer = "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class SWConsoleNative { [DllImport(\"kernel32.dll\")] public static extern bool SetConsoleCtrlHandler(IntPtr h, bool add); }'; $null = [SWConsoleNative]::SetConsoleCtrlHandler([IntPtr]::Zero,$false); [Console]::TreatControlCAsInput=$false"
        arguments = ["-NoLogo", "-NoProfile", "-NoExit", "-Command", initializer]
        proc.spawn(
            shell,
            cmdline=" " + subprocess.list2cmdline(arguments),
            cwd=workspace,
            env="\0".join(f"{key}={value}" for key, value in environment.items())
            + "\0",
        )
        identity = secrets.token_hex(16)
        row = {
            "id": identity,
            "grant": grant,
            "proc": proc,
            "events": [],
            "sequence": 0,
            "bytes": 0,
            "done": False,
            "clients": 0,
            "disconnected_at": None,
            "exit_code": None,
            "closed": False,
        }
        SESSIONS[identity] = row
        row["reader"] = threading.Thread(
            target=_read, args=(row,), daemon=True, name="sw-conpty-read"
        )
        row["reader"].start()
        logger.info(
            "ConPTY启动 terminal=%s shell=%s", identity, os.path.basename(shell)
        )
        return row


def _append(row, text):
    text = redact(text)
    with LOCK:
        row["sequence"] += 1
        row["events"].append((row["sequence"], text))
        row["bytes"] += len(text.encode("utf-8"))
        while row["bytes"] > 2 * 1024 * 1024 and len(row["events"]) > 1:
            _, old = row["events"].pop(0)
            row["bytes"] -= len(old.encode("utf-8"))


def _read(row):
    try:
        while not row["closed"]:
            text = row["proc"].read(8192, blocking=False)
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            if text:
                _append(row, text)
            if not row["proc"].isalive():
                break
            time.sleep(0.02)
    except (EOFError, OSError):
        pass
    except Exception:
        logger.exception("ConPTY读取失败 terminal=%s", row["id"])
    finally:
        with LOCK:
            row["done"] = True
            try:
                row["exit_code"] = row["proc"].get_exitstatus()
            except Exception:
                pass
        logger.info("ConPTY退出 terminal=%s exit=%s", row["id"], row["exit_code"])


def attach(identity, grant):
    row = get(identity, grant)
    with LOCK:
        if row["clients"]:
            raise ValueError("此终端已有控制窗口；先断开原窗口")
        row["clients"] = 1
        row["disconnected_at"] = None
    return row


def detach(row):
    with LOCK:
        row["clients"] = 0
        row["disconnected_at"] = time.monotonic()


def events(row, after):
    with LOCK:
        earliest = row["events"][0][0] if row["events"] else 1
        return {
            "events": [
                {"id": n, "text": text} for n, text in row["events"] if n > after
            ][:32],
            "reset": after < earliest - 1,
            "done": row["done"],
            "exit_code": row["exit_code"],
        }


def input_text(identity, grant, text):
    row = get(identity, grant)
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("单次终端输入最多4096字符")
    # proc 与 close() 的置空在同一把锁里取走：先拿局部引用，close 竞态下也不会踩到 None
    with LOCK:
        if row["done"] or row["closed"] or row["proc"] is None:
            raise ValueError("终端已退出")
        proc = row["proc"]
    if text == "\x03":
        # ConPTY的字符3不总能中断正在执行的PowerShell命令。独立无窗口助手
        # 只附着本模块创建的伪控制台，发送系统CTRL_C_EVENT；不改服务自身控制台。
        program = """import ctypes,sys,time
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):sys.exit(2)
k.SetConsoleCtrlHandler(None,True)
ok=k.GenerateConsoleCtrlEvent(0,0)
time.sleep(.05)
k.FreeConsole()
sys.exit(0 if ok else 3)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", program, str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=3,
        )
        if result.returncode:
            raise ValueError("控制台中断未通过，可关闭此终端停止进程")
        logger.info("ConPTY中断信号 terminal=%s", identity)
    else:
        proc.write(text)


def resize(identity, grant, cols, rows):
    row = get(identity, grant)
    cols = max(20, min(240, int(cols)))
    rows = max(8, min(100, int(rows)))
    with LOCK:
        if row["done"] or row["proc"] is None:
            return
        proc = row["proc"]
    proc.set_size(cols, rows)


def close(identity, grant=None):
    row = SESSIONS.get(identity)
    if not row:
        return
    if grant is not None and row["grant"] != grant:
        raise PermissionError("不能关闭其他授权的终端")
    with LOCK:
        if row["closed"]:
            return
        row["closed"] = True
    proc = row["proc"]
    try:
        if proc.isalive():
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5,
            )
    finally:
        row["reader"].join(timeout=2)
        try:
            row["exit_code"] = proc.get_exitstatus()
        except Exception:
            pass
        with LOCK:
            row["done"] = True
            row["proc"] = None
    logger.info("ConPTY清理 terminal=%s", identity)


def close_all():
    """工作区切换时确定性收尾：不校验授权，关闭本模块创建的全部终端。"""
    closed = 0
    for identity in list(SESSIONS):
        row = SESSIONS.get(identity)
        if row and not row["closed"]:
            closed += 1
        close(identity)
    return closed


def shutdown():
    close_all()
    SESSIONS.clear()
