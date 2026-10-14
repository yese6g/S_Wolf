"""Python 环境与静态分析：模型和专业面板共用，只解析 AST、不运行用户代码。

调用链：agent/tools/__init__.py 递归发现本模块的 TOOLS 并注册 →
模型经 function calling 调用，或工作台「分析」页用 /tools/{name}/test 直接调用。
执行代码交给已授权的项目终端；这里只做「看得见但不会动手」的检查。
"""

import ast
import importlib.metadata
import json
import platform
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = REPO_ROOT / "requirements.txt"

# 有风险的调用：只报线索，不下"有漏洞"的结论
RISKY_CALLS = {"eval", "exec", "compile", "__import__"}
RISKY_ATTRS = {("os", "system"), ("os", "popen"), ("pickle", "loads"), ("marshal", "loads")}
LONG_FUNCTION_LINES = 60


def _read_requirements():
    """读取 requirements.txt；返回 [(包名, 版本或 None, 环境标记)]，注释与空行跳过。"""
    if not REQUIREMENTS.exists():
        return [], "requirements.txt 不存在"
    raw = REQUIREMENTS.read_bytes()
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    items = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        marker = None
        if ";" in line:
            line, marker = (part.strip() for part in line.split(";", 1))
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(?:==\s*([^\s]+))?$", line)
        if match:
            items.append((match.group(1), match.group(2), marker))
    return items, None


def _installed(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # 元数据损坏时不拖垮整次检查
        return "读取失败"


def environment(args):
    """报告解释器信息与依赖清单核对结果（以 requirements.txt 为准）。"""
    required, read_error = _read_requirements()
    packages, missing, mismatch, platform_skipped = {}, [], [], []
    for name, want, marker in required:
        got = _installed(name)
        packages[name] = {"required": want, "installed": got, "marker": marker}
        if got is None:
            # 带平台标记的依赖在别的平台不装是正常的，单独列出不当作缺失
            if marker:
                platform_skipped.append(name)
            else:
                missing.append(name)
        elif want and got != want:
            mismatch.append({"name": name, "required": want, "installed": got})
    result = {
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": sys.platform,
        "requirements_file": REQUIREMENTS.name,
        "declared": len(required),
        "installed": len(required) - len(missing) - len(platform_skipped),
        "missing": missing,
        "platform_skipped": platform_skipped,
        "version_mismatch": mismatch,
        "packages": packages,
    }
    if read_error:
        result["note"] = read_error
    lines = [
        f"Python {result['python']}（{result['platform']}）",
        f"依赖：声明 {result['declared']} 项，本平台应装 {result['declared'] - len(platform_skipped)} 项，"
        f"已装 {result['installed']} 项。",
    ]
    if missing:
        lines.append("未安装或读不到版本：" + "、".join(missing))
    if platform_skipped:
        lines.append("按平台标记跳过：" + "、".join(platform_skipped))
    if mismatch:
        lines.append(
            "版本与锁定不一致："
            + "、".join(f"{m['name']} 需要 {m['required']} 实为 {m['installed']}" for m in mismatch)
        )
    if not missing and not mismatch:
        lines.append("全部依赖与 requirements.txt 一致。")
    if read_error:
        lines.append(read_error)
    return "\n".join(lines) + "\n" + json.dumps(result, ensure_ascii=False), {"ok": True, **result}


def _names_used(tree):
    """收集源码里出现过的名字（含属性访问的根名与注解）。"""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
    return used


def _scan(tree, code):
    """静态体检：只报告可核实的事实，不下结论。"""
    used = _names_used(tree)
    unused_imports, bare_except, swallowed, mutable_defaults = [], [], [], []
    risky, long_functions, todos = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = (alias.asname or alias.name).split(".")[0]
                if bound not in used:
                    unused_imports.append({"line": node.lineno, "name": bound})
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                bare_except.append(node.lineno)          # except: 会把 Ctrl+C 一起吞掉
            elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                swallowed.append(node.lineno)            # except X: pass 静默失败
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    mutable_defaults.append({"line": node.lineno, "function": node.name})
            end = getattr(node, "end_lineno", node.lineno)
            if end - node.lineno + 1 > LONG_FUNCTION_LINES:
                long_functions.append({"line": node.lineno, "function": node.name, "lines": end - node.lineno + 1})
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in RISKY_CALLS:
                risky.append({"line": node.lineno, "call": func.id})
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if (func.value.id, func.attr) in RISKY_ATTRS:
                    risky.append({"line": node.lineno, "call": f"{func.value.id}.{func.attr}"})
    for index, line in enumerate(code.splitlines(), 1):
        if "TODO" in line or "FIXME" in line:
            todos.append(index)
    return {
        "unused_imports": unused_imports,
        "bare_except_lines": bare_except,
        "swallowed_except_lines": swallowed,
        "mutable_defaults": mutable_defaults,
        "risky_calls": risky,
        "long_functions": long_functions,
        "todo_lines": todos,
    }


def check(args):
    """静态解析 Python 源码：语法诊断 + 可核实的结构与风险线索，不执行代码。"""
    code = str(args.get("code", ""))
    if not code:
        # 没有传代码时，允许对仓库内某个文件做检查（相对仓库根目录）
        target = str(args.get("path", "")).strip()
        if not target:
            return "请提供 code（源码文本）或 path（仓库内文件路径）。", {"ok": False}
        candidate = (REPO_ROOT / target).resolve()
        if REPO_ROOT not in candidate.parents or not candidate.is_file():
            return "path 必须是仓库内的文件。", {"ok": False}
        if candidate.stat().st_size > 512 * 1024:
            return "文件过大（上限 512KiB）。", {"ok": False}
        code = candidate.read_text(encoding="utf-8", errors="replace")
    if len(code.encode("utf-8")) > 65536:
        return "请提供不超过64KiB的Python源码。", {"ok": False}
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        return str(exc)[:400], {
            "ok": False,
            "line": getattr(exc, "lineno", None),
            "column": getattr(exc, "offset", None),
        }
    except (RecursionError, MemoryError):
        return "解析失败：源码嵌套过深或占用内存过多。", {"ok": False}

    functions = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    imports = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    findings = _scan(tree, code)
    total_issue = (
        len(findings["unused_imports"])
        + len(findings["bare_except_lines"])
        + len(findings["swallowed_except_lines"])
        + len(findings["mutable_defaults"])
        + len(findings["risky_calls"])
    )
    data = {
        "ok": True,
        "lines": len(code.splitlines()),
        "executed": False,
        "function_count": len(functions),
        "import_count": len(imports),
        "functions": functions[:100],
        "imports": imports[:100],
        "truncated": len(functions) > 100 or len(imports) > 100,
        "findings": findings,
    }
    summary = [f"语法通过；{data['lines']} 行，{len(functions)} 个函数，{len(imports)} 条导入；只做静态解析，没有执行代码。"]
    if data["truncated"]:
        summary.append("函数或导入超过 100 项，列表已截断（计数仍为全量）。")
    if total_issue:
        summary.append(
            f"待核查 {total_issue} 处：未使用导入 {len(findings['unused_imports'])}、"
            f"裸 except {len(findings['bare_except_lines'])}、静默吞异常 {len(findings['swallowed_except_lines'])}、"
            f"可变默认参数 {len(findings['mutable_defaults'])}、高风险调用 {len(findings['risky_calls'])}。"
            "这些是线索，需结合上下文判断。"
        )
    else:
        summary.append("未发现上述几类线索。")
    return "\n".join(summary) + "\n" + json.dumps(data, ensure_ascii=False), data


TOOLS = {
    "python_environment": {
        "description": "读取Python解释器信息，并按 requirements.txt 核对依赖的安装与版本一致性；不读取环境变量或凭据。",
        "parameters": {"type": "object", "properties": {}},
        "handler": environment,
    },
    "python_check": {
        "description": "静态解析Python源码（code 文本或仓库内 path）：语法诊断、函数与导入清单，以及未使用导入/宽泛捕获/可变默认参数/高风险调用等线索；不执行代码。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "源码文本；与 path 二选一"},
                "path": {"type": "string", "description": "仓库内相对路径；与 code 二选一"},
            },
        },
        "handler": check,
    },
}
