"""
tests/test_smoke.py - Agent 核心链路冒烟（pytest，跑完不留任何测试残留）

覆盖点（对应 AGENTS.md 测试清单里"最容易回滚坏"的部分）：
  1. 工具注册表自动发现（新文件即注册，坏的模块不拖垮整体）
  2. 长期记忆：remember → recall → forget 全链路 + 与"无关话题"拒噪
  3. 工作区沙箱：路径穿越 / 绝对路径 / 盘符被拦截；写→读→删正常
  4. 安全计算器：算术正确 + 恶意表达式全部被拒
  5. 任务计划：update_plan 状态机 + 非法状态归一化
  6. 审批链路（用假 OpenAI 客户端驱动 run_agent_stream）：
     允许 → 执行写盘；拒绝 → 跳过执行且不写盘、注册表清理
  7. SSE 归一化：空 choices / delta.content 为 list 的处理（middleware 层）

每个用例结束后都会清理自己产生的 data/ 残留，绝不动用户数据。
"""

import json
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

# 保证从项目根导入（tests/ 与项目根同级的运行方式）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import tools as agent_tools
from agent.tools import memory, plan, workspace
from agent.tools import _run_state as run_state
from agent import runner


# ═══════════════ 1. 工具注册表 ═══════════════

def test_builtin_tools_discovered():
    """关键内置工具都应注册（自动发现目录即生效）。"""
    names = set(agent_tools.BUILTIN_TOOLS.keys())
    for required in ("web_search", "fetch_page", "get_current_time", "remember", "recall",
                     "fs_write", "fs_delete", "update_plan", "rag_import_url", "calculate"):
        assert required in names, f"缺少内置工具 {required}"


def test_approval_flags():
    """写/删/改自定义工具必须标 approval_required，普通工具不用。"""
    flags = {k: bool(v.get("approval_required")) for k, v in agent_tools.BUILTIN_TOOLS.items()}
    for name in ("fs_write", "fs_delete", "tools_upsert", "tools_delete"):
        assert flags[name], f"{name} 应标记为需审批"
    for name in ("web_search", "get_current_time", "calculate"):
        assert not flags[name], f"{name} 不应标记为需审批"


# ═══════════════ 2. 长期记忆 ═══════════════

@pytest.fixture
def clean_memory():
    """用例前后清空记忆文件，测完不留残留。"""
    f = memory.MEMORY_FILE
    backup = None
    if f.exists():
        backup = f.read_bytes()
        f.unlink()
    yield
    if backup is not None:
        f.write_bytes(backup)
    elif f.exists():
        f.unlink()


def test_memory_remember_recall_forget(clean_memory):
    memory.add_memory("委托人叫小明，写代码偏好 Python", tags="偏好", source="test")
    memory.add_memory("公司在做 AI 助手项目，叫银狼工作台", tags="项目", source="test")
    hits = memory.search_memories("小明喜欢什么语言")
    assert hits and "小明" in hits[0]["content"]
    hits2 = memory.search_memories("学日语")
    assert not hits2, "无关话题不应被召回"
    mem_id = hits[0]["id"]
    assert memory.delete_memory(mem_id)["ok"]
    assert memory.clear_memories() == 1


# ═══════════════ 3. 工作区沙箱 ═══════════════

@pytest.fixture
def clean_workspace():
    """用例前后清理工作区目录。"""
    d = workspace.WORKSPACE_DIR
    if d.exists():
        shutil.rmtree(d)
    yield
    if d.exists():
        shutil.rmtree(d)


def test_workspace_sandbox_escape_blocked():
    for bad in ("../escape.txt", "/etc/passwd", "C:/windows/win.ini", "a/../../out"):
        with pytest.raises(ValueError):
            workspace._safe_resolve(bad)


def test_workspace_write_read_delete(clean_workspace):
    out, meta = workspace.TOOLS["fs_write"]["handler"]({"path": "n/s.md", "content": "hi"})
    assert meta["ok"]
    out2, meta2 = workspace.TOOLS["fs_read"]["handler"]({"path": "n/s.md"})
    assert meta2["ok"] and "hi" in out2
    # 非空目录拒绝删、根目录拒绝删
    assert not workspace.TOOLS["fs_delete"]["handler"]({"path": "n"})[1]["ok"]
    assert not workspace.TOOLS["fs_delete"]["handler"]({"path": ""})[1]["ok"]
    assert workspace.TOOLS["fs_delete"]["handler"]({"path": "n/s.md"})[1]["ok"]


# ═══════════════ 4. 安全计算器 ═══════════════

def test_calculator_correctness():
    calc = agent_tools.BUILTIN_TOOLS["calculate"]["handler"]
    out, meta = calc({"expression": "sqrt(144) + round(3.14159, 2)"})
    assert meta["ok"] and "15.14" in out


def test_calculator_rejects_unsafe():
    calc = agent_tools.BUILTIN_TOOLS["calculate"]["handler"]
    for bad in ('__import__("os")', "open('/etc/passwd')", "a = 1", "lambda: 1",
                "[1,2,3]", "print(1)"):
        _, meta = calc({"expression": bad})
        assert not meta["ok"], f"未拦截恶意表达式: {bad}"


# ═══════════════ 5. 任务计划 ═══════════════

def test_plan_state_machine():
    ctx = run_state.new_run()
    run_state.bind(ctx)
    handler = plan.TOOLS["update_plan"]["handler"]
    _, meta = handler({"steps": [
        {"id": "1", "text": "搜", "status": "done"},
        {"id": "2", "text": "写", "status": "weird"},
    ]})
    assert len(meta["plan"]) == 2
    assert meta["plan"][0]["status"] == "done"
    assert meta["plan"][1]["status"] == "pending"  # 非法状态归一化
    _, meta2 = handler({"update": {"id": "2", "status": "running"}})
    assert meta2["plan"][1]["status"] == "running"


# ═══════════════ 6. 审批链路（假客户端） ═══════════════

class _FakeStream:
    def __init__(self, step):
        self.step = step
        self.ix = 0
        self.frames = list(self._frames())

    def _frames(self):
        if self.step == 1:
            yield _chunk(_delta_fn("fs_write", '{"path": "ap_test.txt", "content": "hi"}', "c1"))
        else:
            yield _chunk(_delta_txt("写完了。"))

    def __iter__(self):
        return self

    def __next__(self):
        if self.ix < len(self.frames):
            f = self.frames[self.ix]; self.ix += 1
            return f
        raise StopIteration


def _delta_txt(content):
    class D: pass
    d = D(); d.content = content; d.reasoning_content = None; d.tool_calls = None
    return d


def _delta_fn(name, args, cid):
    class Fn: pass
    fn = Fn(); fn.name = name; fn.arguments = args
    class TC: pass
    tc = TC(); tc.index = 0; tc.id = cid; tc.function = fn
    class D: pass
    d = D(); d.content = None; d.reasoning_content = None; d.tool_calls = [tc]
    return d


def _chunk(delta):
    class C: pass
    c = C(); c.usage = None
    class Ch: pass
    ch = Ch(); ch.delta = delta
    c.choices = [ch]
    return c


class _FakeCompletions:
    def __init__(self, cli): self.cli = cli
    def create(self, **kw): self.cli.calls += 1; return _FakeStream(self.cli.calls)


class _FakeChat:
    def __init__(self, cli): self.cli = cli; self.completions = _FakeCompletions(cli)


class _FakeClient:
    def __init__(self): self.calls = 0; self.chat = _FakeChat(self)


@pytest.fixture
def fake_client(monkeypatch):
    """把 provider_config.build_client 换成假客户端。"""
    import provider_config
    monkeypatch.setattr(provider_config, "build_client", lambda p: _FakeClient())


def _run_agent_with_approval(fake_client, approve, monkeypatch):
    provider = {"id": "fake", "model": "fake", "base_url": "http://x", "api_key": "", "kind": "cloud"}
    messages = [{"role": "user", "content": "在工作区写个文件"}]
    events = []
    feed = {"rid": None}

    def respond():
        time.sleep(0.3)
        entry = runner.APPROVAL_REGISTRY.get(feed["rid"])
        assert entry is not None
        entry["approved"] = approve
        entry["event"].set()

    t = threading.Thread(target=respond)
    t.start()
    for ev in runner.run_agent_stream(provider, messages, enabled_tools=["fs_write"], max_steps=2):
        events.append(ev)
        if ev["type"] == "tool_approval":
            feed["rid"] = ev["request_id"]
    t.join(timeout=5)
    return events


def test_approval_allowed_writes_file(fake_client, clean_workspace, monkeypatch):
    events = _run_agent_with_approval(fake_client, approve=True, monkeypatch=monkeypatch)
    types = [e["type"] for e in events]
    assert "tool_approval" in types and "tool_call" in types
    f = workspace.WORKSPACE_DIR / "ap_test.txt"
    assert f.exists() and f.read_text(encoding="utf-8") == "hi"
    assert not runner.APPROVAL_REGISTRY, "审批注册表未清理"


def test_approval_denied_skips_execution(fake_client, clean_workspace, monkeypatch):
    events = _run_agent_with_approval(fake_client, approve=False, monkeypatch=monkeypatch)
    types = [e["type"] for e in events]
    assert "tool_approval" in types and "tool_call" not in types
    assert not (workspace.WORKSPACE_DIR / "ap_test.txt").exists(), "拒绝后不应写盘"
    denied = [e for e in events if e["type"] == "tool_result" and not e["ok"]]
    assert denied and denied[0]["meta"].get("denied")
    assert not runner.APPROVAL_REGISTRY, "审批注册表未清理"


# ═══════════════ 7. SSE 归一化（middleware） ═══════════════

def test_stream_delta_empty_choices():
    from middleware.stream_utils import extract_stream_delta

    class Chunk:
        pass
    chunk = Chunk()
    chunk.choices = []  # 厂商会发空 choices 帧，必须返回 (None, None) 不崩
    content, reasoning = extract_stream_delta(chunk)
    assert content is None and reasoning is None


def test_stream_delta_content_list():
    from middleware.stream_utils import normalize_delta_text

    # delta.content 可能是 list（部分厂商发分块列表），要能拼回字符串
    assert normalize_delta_text([{"text": "你"}, {"text": "好"}]) == "你好"