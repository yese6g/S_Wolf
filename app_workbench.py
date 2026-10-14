"""专业与扩展工作台API：main挂载，复用工具仓库、Skill文件与MCP连接器。

配置只保存一份；前端显式管理与模型工具审批分开，试运行仍要求明确授权。
"""

import json
import os
import zipfile
import hashlib
import re
import asyncio
from typing import Dict
from fastapi import (
    APIRouter,
    HTTPException,
    File,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from config_data import BASE_DIR
from agent import tools, skills, mcp_client, persistence, developer, workspace_history
from agent.tools.agent import self_manage

router = APIRouter(prefix="/api", tags=["工作台"])
UI_FILE = BASE_DIR / "data" / "ui_state.json"


def settings():
    return persistence.load(UI_FILE.parent, UI_FILE.stem, {})


def update(values):
    with persistence.LOCK:
        data = settings()
        data.update(values)
        persistence.save(UI_FILE.parent, UI_FILE.stem, data)
    return {"ok": True}


def appearance_revision(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


STATIC_ASSET = re.compile(r"/static/[A-Za-z0-9_][A-Za-z0-9_./-]*\Z")


def static_asset_url(url):
    """只放行 /static 下的普通文件路径：禁 .. / . 段、禁百分号编码与隐藏名。"""
    if not isinstance(url, str) or not STATIC_ASSET.fullmatch(url):
        return False
    return all(part not in {".", ".."} for part in url.split("/"))


@router.get("/appearance-state")
def appearance_state():
    value = settings()
    modes = value.get("appearance_modes", {})
    return {
        "modes": modes,
        "uploads": value.get("appearance_upload_modes", {}),
        "revisions": {key: appearance_revision(row) for key, row in modes.items()},
    }


@router.put("/appearance-state/{mode}")
def appearance_save(mode: str, req: Dict):
    import appearance_presets

    if mode not in {"normal", "lv999"}:
        failure(ValueError("外观模式无效"))
    appearance = req.get("appearance")
    catalog = appearance_presets.catalog()
    if not isinstance(appearance, dict) or set(appearance) != {
        "theme",
        "avatars",
        "selections",
    }:
        failure(ValueError("外观格式无效"))
    theme = appearance["theme"]
    avatars = appearance["avatars"]
    selections = appearance["selections"]
    if not all(isinstance(x, dict) for x in (theme, avatars, selections)):
        failure(ValueError("外观字段需要对象"))
    for key in ("accent", "accentDeep", "cyan", "userBubble", "userDeep"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(theme.get(key, ""))):
            failure(ValueError("配色需使用六位HEX颜色"))
    if (
        not isinstance(theme.get("bgShade"), (int, float))
        or not 0 <= theme["bgShade"] <= 1
    ):
        failure(ValueError("背景遮罩需在0到1之间"))
    for group, url in [
        ("backgrounds", theme.get("bgImage", "")),
        ("bot", avatars.get("bot", "")),
        ("user", avatars.get("user", "")),
    ]:
        if (
            url
            and url not in {x["url"] for x in catalog[group]}
            and not (group == "backgrounds" and static_asset_url(url))
        ):
            failure(ValueError("先把图片加入收藏，再保存外观"))
    clean = {
        "theme": {
            key: theme.get(key)
            for key in (
                "accent",
                "accentDeep",
                "cyan",
                "userBubble",
                "userDeep",
                "bgShade",
                "bgImage",
            )
        },
        "avatars": {key: avatars.get(key, "") for key in ("bot", "user")},
        "selections": {
            key: str(selections.get(key, ""))[:100] for key in ("bot", "user")
        },
    }
    uploads = req.get("uploads", {})
    if not isinstance(uploads, dict) or len(uploads) > 500:
        failure(ValueError("图片归属格式无效"))
    allowed = {
        row["id"]
        for rows in catalog.values()
        if isinstance(rows, list)
        for row in rows
        if row.get("origin") == "upload"
    }
    if any(
        key not in allowed
        or not isinstance(v, list)
        or any(m not in {"normal", "lv999"} for m in v)
        or len(v) > 2
        for key, v in uploads.items()
    ):
        failure(ValueError("图片归属无效"))
    with persistence.LOCK:
        state = settings()
        modes = state.get("appearance_modes", {})
        old = modes.get(mode)
        revision = appearance_revision(old) if old else "absent"
        if req.get("revision") != revision:
            failure(ValueError("另一窗口已修改外观，刷新后再选择；本机选择仍保留"), 409)
        modes[mode] = clean
        owners = state.get("appearance_upload_modes", {})
        for key, value in uploads.items():
            owners[key] = sorted(set(owners.get(key, ["normal"])) | set(value))
        update({"appearance_modes": modes, "appearance_upload_modes": owners})
    return {"ok": True, "revision": appearance_revision(clean)}


def failure(exc, status=400):
    raise HTTPException(status_code=status, detail=str(exc)[:300])


@router.get("/tool-hooks")
def tool_hooks():
    return {"rules": settings().get("tool_hooks", [])}


@router.put("/tool-hooks")
def save_tool_hooks(req: Dict):
    rules = req.get("rules")
    if not isinstance(rules, list) or len(rules) > 32:
        failure(ValueError("最多32条守卫规则"))
    ids = set()
    clean = []
    for row in rules:
        if (
            not isinstance(row, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(row.get("id", "")))
            or row["id"] in ids
        ):
            failure(ValueError("规则标识无效或重复"))
        event = row.get("event")
        action = row.get("action")
        pattern = row.get("tool")
        if (
            event not in {"before_tool", "after_tool", "after_response"}
            or action not in {"deny", "log"}
            or (action == "deny" and event != "before_tool")
        ):
            failure(ValueError("规则只能在调用前禁止，或记录前后事件"))
        if not isinstance(pattern, str) or not re.fullmatch(
            r"[A-Za-z0-9_*?]{1,80}", pattern
        ):
            failure(ValueError("工具匹配只支持标识、*和?"))
        ids.add(row["id"])
        clean.append(
            {
                "id": row["id"],
                "event": event,
                "action": action,
                "tool": pattern,
                "enabled": bool(row.get("enabled", True)),
            }
        )
    result = update({"tool_hooks": clean})
    self_manage.log_change("tool_hooks_save", {"count": len(clean)})
    return result


@router.get("/capabilities")
def capabilities():
    state = settings()
    disabled = state.get("disabled_tools", [])
    return {
        "tools": [
            {**t, "enabled": t.get("enabled", True) and t["name"] not in disabled}
            for t in tools.list_tools()
        ],
        "skills": [
            {**s, "enabled": s["id"] in state.get("enabled_skills", [])}
            for s in skills.catalog()
        ],
        "mcp": mcp_client.catalog(),
        "categories": ["core", "agent", "python", "cyberSecurity"],
    }


@router.get("/tools/{name}")
def tool_definition(name: str):
    item = next((t for t in self_manage.load_custom_tools() if t["name"] == name), None)
    if not item:
        raise HTTPException(status_code=404, detail="自定义工具不存在")
    return self_manage.public_definition(item)


@router.put("/tools/{name}")
def save_tool(name: str, req: Dict):
    req = {**req, "name": name}
    prior = next((t for t in self_manage.load_custom_tools() if t["name"] == name), {})
    if "headers" not in req:
        req["headers"] = prior.get("headers", {})
    text, meta = self_manage._tool_tools_upsert(req)
    if not meta.get("ok"):
        failure(ValueError(text))
    return meta


@router.delete("/tools/{name}")
def delete_tool(name: str):
    text, meta = self_manage._tool_tools_delete({"name": name})
    if not meta.get("ok"):
        failure(ValueError(text), 404)
    return meta


@router.put("/tools/{name}/enabled")
def enable_tool(name: str, req: Dict):
    if not any(t["name"] == name for t in tools.list_tools()):
        failure(ValueError("工具不存在"), 404)
    with persistence.LOCK:
        state = settings()
        disabled = set(state.get("disabled_tools", []))
        if req.get("enabled"):
            disabled.discard(name)
        else:
            disabled.add(name)
        result = update({"disabled_tools": sorted(disabled)})
        # 自定义工具还有 tools.json 的 enabled 字段，两个来源一起改，否则开关永远无效
        self_manage.set_custom_tool_enabled(name, bool(req.get("enabled")))
        self_manage.log_change(
            "tool_enabled", {"name": name, "enabled": bool(req.get("enabled"))}
        )
        return result


@router.post("/tools/{name}/test")
def test_tool(name: str, req: Dict):
    if not any(t["name"] == name for t in tools.list_tools()):
        failure(ValueError("工具不存在"), 404)
    if (
        tools.tool_spec(name).get("approval_required")
        and req.get("approved") is not True
    ):
        failure(ValueError("此试运行需要用户明确批准"), 403)
    args = req.get("args", {})
    if not isinstance(args, dict):
        failure(ValueError("args必须是对象"))
    return tools.execute_tool(name, args)


@router.get("/skills")
def skill_list():
    return {"skills": skills.catalog(), "templates": skills.templates()}


@router.post("/skills/import")
async def skill_import(file: UploadFile = File(...)):
    try:
        data = await file.read(2 * 1024 * 1024 + 1)
        return await run_in_threadpool(skills.import_archive, data)
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        failure(exc)


@router.get("/skills/{slug}/export")
def skill_export(slug: str):
    try:
        return Response(
            skills.export_archive(slug),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
        )
    except (ValueError, OSError) as exc:
        failure(exc)


@router.get("/skills/{slug}")
def skill_get(slug: str):
    try:
        return skills.get(slug)
    except (ValueError, OSError) as exc:
        failure(exc, 404)


@router.put("/skills/{slug}")
def skill_save(slug: str, req: Dict):
    try:
        result = skills.save(slug, str(req.get("content", "")), req.get("revision"))
        self_manage.log_change(
            "skill_save", {"id": slug, "revision": result["revision"]}
        )
        return result
    except (ValueError, OSError) as exc:
        failure(exc, 409)


@router.put("/skills/{slug}/enabled")
def skill_enable(slug: str, req: Dict):
    try:
        skills.get(slug)
    except (ValueError, OSError) as exc:
        failure(exc, 404)
    with persistence.LOCK:
        active = set(settings().get("enabled_skills", []))
        if req.get("enabled"):
            active.add(slug)
        else:
            active.discard(slug)
        result = update({"enabled_skills": sorted(active)})
        self_manage.log_change(
            "skill_enabled", {"id": slug, "enabled": bool(req.get("enabled"))}
        )
        return result


@router.delete("/skills/{slug}")
def skill_delete(slug: str, req: Dict):
    try:
        result = skills.delete(slug, req.get("revision"))
        with persistence.LOCK:
            update(
                {
                    "enabled_skills": [
                        s for s in settings().get("enabled_skills", []) if s != slug
                    ]
                }
            )
        self_manage.log_change("skill_delete", {"id": slug})
        return result
    except (ValueError, OSError) as exc:
        failure(exc, 409)


@router.put("/mcp/{name}")
def mcp_save(name: str, req: Dict):
    try:
        result = mcp_client.save(name, req)
        self_manage.log_change(
            "mcp_save", {"name": name, "transport": req.get("transport", "stdio")}
        )
        return result
    except (ValueError, OSError) as exc:
        failure(exc, 409)


@router.delete("/mcp/{name}")
def mcp_delete(name: str):
    try:
        result = mcp_client.delete(name)
        self_manage.log_change("mcp_delete", {"name": name})
        return result
    except (ValueError, OSError) as exc:
        failure(exc, 404)


@router.get("/mcp/{name}/inspect")
def mcp_inspect(name: str, kind: str = "tools"):
    client = mcp_client.CLIENTS.get(name)
    if not client:
        failure(ValueError("先连接服务器"), 409)
    if kind not in {"tools", "resources", "prompts"}:
        failure(ValueError("能力类型无效"))
    try:
        return {**client.request(kind + "/list", {}), "diagnostic": client.diagnostic}
    except Exception as exc:
        failure(exc, 502)


@router.post("/mcp/{name}/query")
def mcp_query(name: str, req: Dict):
    client = mcp_client.CLIENTS.get(name)
    if not client:
        failure(ValueError("先连接服务器"), 409)
    method = req.get("method")
    if method not in {"resources/read", "prompts/get"}:
        failure(ValueError("只允许读取资源或提示词"))
    if req.get("approved") is not True:
        failure(ValueError("先确认这次服务器请求"), 403)
    params = req.get("params", {})
    if not isinstance(params, dict):
        failure(ValueError("params必须是对象"))
    try:
        return client.request(method, params)
    except Exception as exc:
        failure(exc, 502)


@router.post("/developer/grant")
def developer_grant(req: Dict):
    if req.get("approved") is not True:
        failure(ValueError("先确认工作区授权范围"), 403)
    for key in ("write", "commands", "terminal"):
        if type(req.get(key, False)) is not bool:
            failure(ValueError("授权选项必须是布尔值"))
    try:
        return developer.authorize(
            req.get("write", False), req.get("commands", False), req.get("terminal", False)
        )
    except ValueError as exc:
        failure(exc, 409)


@router.get("/developer/workspace")
def developer_workspace():
    return developer.workspace_info()


@router.post("/developer/workspace")
def developer_workspace_update(req: Dict):
    if req.get("approved") is not True:
        failure(ValueError("先确认切换工作区的影响"), 403)
    if type(req.get("default", False)) is not bool:
        failure(ValueError("default 必须是布尔值"))
    try:
        if req.get("default"):
            return developer.set_workspace(default=True)
        path = req.get("path", "")
        if not isinstance(path, str) or not path.strip():
            failure(ValueError("需要提供工作区路径"))
        return developer.set_workspace(path=path)
    except FileNotFoundError as exc:
        failure(exc, 404)
    except ValueError as exc:
        failure(exc, 400)
    except OSError as exc:
        failure(exc, 409)


@router.post("/developer/workspace/remove")
def developer_workspace_remove(req: Dict):
    """把工作区从侧栏常驻列表里移除（只动列表，不删目录）。"""
    if req.get("approved") is not True:
        failure(ValueError("先确认移除这个工作区"))
    try:
        return developer.remove_workspace(req.get("path", ""))
    except ValueError as exc:
        failure(exc, 400)
    except OSError as exc:
        failure(exc, 409)


@router.get("/developer/workspace/browse")
def developer_workspace_browse(path: str = ""):
    try:
        return developer.browse(path)
    except FileNotFoundError as exc:
        failure(exc, 404)
    except ValueError as exc:
        failure(exc, 400)
    except OSError as exc:
        failure(exc, 409)


@router.post("/chat/runs/{identity}/stop")
def stop_generation(identity: str):
    from chat_runtime import stop_run

    return stop_run(identity)


@router.post("/developer/action")
def developer_action(req: Dict):
    key = req.get("grant", "")
    op = req.get("operation")
    try:
        if op == "revoke":
            return developer.revoke(key)
        if op == "status":
            return {k: v for k, v in developer.require(key).items() if k != "principal"}
        if op == "files":
            return developer.files(key, req.get("path", ""))
        if op == "read":
            return developer.read(key, req.get("path", ""))
        if op == "preview":
            return developer.preview(key, req.get("path", ""), req.get("content", ""))
        if op == "write":
            return developer.write(
                key, req.get("path", ""), req.get("content", ""), req.get("revision")
            )
        if op == "git":
            return developer.git_status(key)
        if op == "changes":
            root = developer.require(key)["workspace"]
            return {
                "changes": [
                    r
                    for r in workspace_history.list_changes()
                    if r.get("scope") == "project"
                    and os.path.normcase(r.get("workspace") or str(BASE_DIR))
                    == os.path.normcase(root)
                ]
            }
        if op == "diff":
            developer.require(key)
            return workspace_history.diff(req.get("id"))
        if op == "rollback":
            return developer.rollback(key, req.get("id"))
        if op == "command":
            return developer.start_command(key, req.get("argv"), req.get("approved"))
        if op == "input":
            return developer.command_input(key, req.get("id"), req.get("text", ""))
        raise ValueError("操作无效")
    except PermissionError as exc:
        failure(exc, 403)
    except (ValueError, OSError, UnicodeError) as exc:
        failure(exc, 409)


@router.websocket("/terminal")
async def terminal_socket(socket: WebSocket):
    """Cookie/Bearer、Host/Origin由访问守卫验证；授权在首帧传递，不进入URL。"""
    from agent import terminal

    row = None
    sender = None
    grant = ""
    explicit_close = False
    await socket.accept()
    try:
        first = await asyncio.wait_for(socket.receive_text(), 10)
        if len(first) > 8192:
            raise ValueError("终端首帧过大")
        hello = json.loads(first)
        if not isinstance(hello, dict):
            raise ValueError("终端首帧必须是对象")
        grant = hello.get("grant", "")
        cursor = max(0, int(hello.get("after", 0)))
        terminal.require(grant)
        if hello.get("id"):
            row = terminal.attach(hello["id"], grant)
        else:
            row = await run_in_threadpool(terminal.open_session, grant)
            terminal.attach(row["id"], grant)
        await socket.send_json({"type": "ready", "id": row["id"]})

        async def output():
            nonlocal cursor
            while True:
                try:
                    terminal.require(grant)
                except PermissionError:
                    await run_in_threadpool(terminal.close, row["id"], grant)
                snapshot = terminal.events(row, cursor)
                if snapshot["reset"]:
                    await socket.send_json({"type": "reset"})
                    cursor = 0
                for event in snapshot["events"]:
                    await socket.send_json({"type": "output", **event})
                    cursor = event["id"]
                if snapshot["done"] and not snapshot["events"]:
                    await socket.send_json(
                        {"type": "exit", "code": snapshot["exit_code"]}
                    )
                    await socket.close()
                    return
                await asyncio.sleep(0.04)

        sender = asyncio.create_task(output())
        while not sender.done():
            data = await socket.receive_text()
            if len(data) > 8192:
                raise ValueError("终端消息过大")
            packet = json.loads(data)
            kind = packet.get("type")
            if kind == "input":
                await run_in_threadpool(
                    terminal.input_text, row["id"], grant, packet.get("text", "")
                )
            elif kind == "resize":
                await run_in_threadpool(
                    terminal.resize,
                    row["id"],
                    grant,
                    packet.get("cols", 80),
                    packet.get("rows", 24),
                )
            elif kind == "close":
                explicit_close = True
                await run_in_threadpool(terminal.close, row["id"], grant)
                await socket.close()
                break
            else:
                raise ValueError("终端事件无效")
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        try:
            await socket.send_json({"type": "error", "text": str(exc)[:200]})
            await socket.close(code=1008)
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if row:
            terminal.detach(row)
            if explicit_close or row["done"]:
                await run_in_threadpool(terminal.close, row["id"], grant)
            elif not row["done"]:
                disconnected_at = row["disconnected_at"]

                async def abandoned():
                    await asyncio.sleep(15)
                    # 旧断连计时不能提前结束一次重连后的新宽限期。
                    if not row["clients"] and row["disconnected_at"] == disconnected_at:
                        await run_in_threadpool(terminal.close, row["id"], grant)

                asyncio.create_task(abandoned())
