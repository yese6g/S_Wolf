"""可重复冒烟入口：开发/发布时执行，不提供远程运行任意测试的API。

只在tmp隔离数据中验证关键调用链；每次结束清理，结果写现有tests.log。
完整临时回归仍可从Git历史提取，这个入口不恢复tests目录。
"""

import logging
import sys

sys.dont_write_bytecode = True
from pathlib import Path
import tempfile
from unittest.mock import patch


def run():
    from middleware.logging_system import configure_logging

    configure_logging()
    import file_history_store

    # 导入路由时的幂等建表也隔离，避免冒烟触碰真实数据库。
    with patch.object(file_history_store, "init_db"):
        import SilverWolf as routes
    import provider_config
    from agent import (
        tools,
        artifacts,
        workspace_history,
        persistence,
        skills,
        developer,
    )
    from middleware.token_usage import UsageTracker
    from middleware.outbound import public_addresses
    from config_data import BASE_DIR
    from fastapi.testclient import TestClient
    from middleware import security
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(routes.router)
    app.add_middleware(security.LocalAccessMiddleware)
    checks = 0

    def check(value):
        nonlocal checks
        if not value:
            raise AssertionError("冒烟断言失败")
        checks += 1

    (BASE_DIR / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="selfcheck-", dir=BASE_DIR / "tmp"
    ) as directory:
        root = Path(directory)
        import os, time

        if os.name == "nt":
            from agent import terminal

            with patch.object(developer, "BASE_DIR", root), patch.object(
                security, "TOKEN_FILE", root / "token"
            ):
                key = developer.authorize(commands=True, terminal=True)["grant"]
                row = terminal.open_session(key)
                try:
                    terminal.resize(row["id"], key, 90, 25)
                    terminal.input_text(
                        row["id"], key, "Write-Output ('SELF'+'CHECK')\r"
                    )
                    deadline = time.monotonic() + 5
                    text = ""
                    sequence = 0
                    while time.monotonic() < deadline:
                        for event in terminal.events(row, sequence)["events"]:
                            sequence = event["id"]
                            text += event["text"]
                        if "SELFCHECK" in text:
                            break
                        time.sleep(0.04)
                    check("SELFCHECK" in text)
                    terminal.input_text(row["id"], key, "\x03")
                finally:
                    terminal.close(row["id"], key)
                    developer.revoke(key)
                check(row["done"] and row["proc"] is None)
        with patch("config_data.BASE_DIR", root):
            persistence.save(
                root / "data",
                "ui_state",
                {
                    "tool_hooks": [
                        {
                            "id": "smoke",
                            "event": "before_tool",
                            "action": "deny",
                            "tool": "calculate",
                        }
                    ]
                },
            )
            result = tools.execute_tool("calculate", {"expression": "1+1"})
            check(not result["ok"] and result["meta"]["denied"])
            persistence.save(root / "data", "ui_state", {})
        with patch.object(skills, "SKILL_DIR", root / "skills"):
            sample = skills.templates()[0]
            item = skills.save(sample["id"], sample["content"])
            check(item["revision"] == skills.get(sample["id"])["revision"])
            try:
                skills.save(sample["id"], sample["content"])
            except ValueError:
                checks += 1
            else:
                raise AssertionError("技能冲突未拦截")
            package = skills.export_archive(sample["id"])
            skills.delete(sample["id"], item["revision"])
            check(skills.import_archive(package)["id"] == sample["id"])
            import io, zipfile

            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as archive:
                archive.writestr("../SKILL.md", sample["content"])
            try:
                skills.import_archive(out.getvalue())
            except ValueError:
                checks += 1
            else:
                raise AssertionError("ZIP越界未拦截")
        with patch.object(developer, "BASE_DIR", root), patch.object(
            workspace_history, "HISTORY_DIR", root / "history"
        ), patch.object(security, "TOKEN_FILE", root / "token"):
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            grant = developer.authorize(write=True)["grant"]
            item = developer.read(grant, "sample.py")
            check(
                "value = 2"
                in developer.preview(grant, "sample.py", "value = 2\n")["diff"]
            )
            change = developer.write(
                grant, "sample.py", "value = 2\n", item["revision"]
            )
            try:
                developer.write(grant, "sample.py", "value = 3\n", item["revision"])
            except ValueError:
                checks += 1
            else:
                raise AssertionError("项目编辑冲突未拦截")
            developer.rollback(grant, change["change_id"])
            check((root / "sample.py").read_text() == "value = 1\n")
            for path in ("../escape.py", "data/providers.json", ".git/config"):
                try:
                    developer.location(path)
                except ValueError:
                    checks += 1
                else:
                    raise AssertionError("项目路径未拦截")
            developer.revoke(grant)
            try:
                developer.read(grant, "sample.py")
            except PermissionError:
                checks += 1
            else:
                raise AssertionError("撤销授权未生效")
        with patch.object(developer, "BASE_DIR", root), patch.object(
            workspace_history, "HISTORY_DIR", root / "history"
        ), patch.object(security, "TOKEN_FILE", root / "token"):
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_bytes(b"name = 'wolf'\n")
            check(developer.workspace_info()["is_default"])
            moved = developer.set_workspace(path=str(project))
            check(
                moved["is_default"] is False
                and not moved["revoked"]
                and developer.current_root() == project.resolve()
            )
            grant = developer.authorize(write=True)["grant"]
            check(developer.read(grant, "app.py")["content"] == "name = 'wolf'\n")
            developer.write(grant, "note.txt", "hello\n", "absent")
            check(
                (project / "note.txt").read_text(encoding="utf-8") == "hello\n"
                and not (root / "note.txt").exists()
            )
            back = developer.set_workspace(default=True)
            check(back["revoked"] == 1 and developer.current_root() == root)
            try:
                developer.read(grant, "app.py")
            except PermissionError:
                checks += 1
            else:
                raise AssertionError("切换工作区后旧授权未失效")
            for name in ("data", "chroma_db", "knowledge_bases"):
                (root / name).mkdir(exist_ok=True)
                try:
                    developer.set_workspace(path=str(root / name))
                except ValueError:
                    checks += 1
                else:
                    raise AssertionError("服务数据目录被选为工作区")
        check(
            tools.execute_tool("python_check", {"code": "def x():\n  return 1"})["ok"]
        )
        check(not tools.execute_tool("python_check", {"code": "def :\n"})["ok"])
        check(
            provider_config.local_base_url("http://localhost:11434/v1")
            == "http://127.0.0.1:11434/v1"
        )
        check(
            provider_config.local_base_url("https://example.com/v1")
            == "https://example.com/v1"
        )
        tracker = UsageTracker()
        tracker.add_provider_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 0},
            }
        )
        check(tracker.summary()["reasoning_tokens"] == 0)
        tracker.add_estimate("hello", "world")
        check(
            tracker.summary()["source"] == "mixed"
            and tracker.summary()["reasoning_tokens"] is None
        )
        check(
            len(tools.list_tools()) >= 30
            and all(
                t.get("category") for t in tools.list_tools() if t["kind"] == "builtin"
            )
        )
        for address in ("127.0.0.1", "169.254.169.254", "192.168.1.1", "::1"):
            try:
                public_addresses(address, 80)
            except ValueError:
                checks += 1
            else:
                raise AssertionError("非公网地址未拦截")
        with patch.object(artifacts, "ARTIFACT_DIR", root / "outputs"), patch.object(
            artifacts, "MAX_TOTAL_BYTES", 1024
        ):
            for _ in range(4):
                artifacts.preserve("x" * 700, 100)
            check(
                sum(p.stat().st_size for p in (root / "outputs").glob("*.txt")) <= 1024
            )
        with patch.object(
            workspace_history, "HISTORY_DIR", root / "history"
        ), patch.object(workspace_history, "MAX_HISTORY_BYTES", 6000):
            for _ in range(5):
                workspace_history.record(
                    "sample.py",
                    {"kind": "absent", "data": "", "sha": "absent"},
                    {"kind": "file", "data": "x" * 1000, "sha": "x"},
                )
            check(
                len(workspace_history.list_changes()) > 0
                and sum(p.stat().st_size for p in (root / "history").glob("*.json"))
                <= 6000
            )
        # API兼容检查只替换本检查进程的存储函数；不会修改正在运行的服务或用户数据库。
        with patch.object(security, "TOKEN_FILE", root / "token"), patch.object(
            routes, "api_create_session", return_value={"id": "smoke"}
        ), patch.object(routes, "api_list_sessions", return_value=[]), patch.object(
            routes, "api_get_session", return_value={"id": "smoke"}
        ), patch.object(
            routes, "api_rename_session", return_value={"ok": True}
        ), patch.object(
            routes, "api_delete_session", return_value={"ok": True}
        ):
            token = security.access_token()
            with TestClient(app, base_url="http://127.0.0.1") as client:
                check(client.get("/api/health").status_code == 401)
                headers = {"Authorization": "Bearer " + token}
                for method, path, data in [
                    ("POST", "/sessions", {}),
                    ("GET", "/sessions", None),
                    ("GET", "/sessions/smoke", None),
                    ("PUT", "/sessions/smoke", {"title": "smoke"}),
                    ("DELETE", "/sessions/smoke", None),
                ]:
                    check(
                        client.request(
                            method, path, headers=headers, json=data
                        ).status_code
                        == 200
                    )
                check(
                    client.get(
                        "/api/config", headers={**headers, "Host": "evil.invalid"}
                    ).status_code
                    == 400
                )
                check(
                    client.get(
                        "/api/config",
                        headers={**headers, "Origin": "https://evil.invalid"},
                    ).status_code
                    == 403
                )
    logging.getLogger("audit.tests").info("selfcheck passed checks=%d", checks)
    return checks


if __name__ == "__main__":
    try:
        print("自检通过：", run(), "项")
    except Exception:
        logging.getLogger("audit.tests").exception("selfcheck failed")
        raise
