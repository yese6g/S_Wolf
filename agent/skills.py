"""项目Skills：工作台编辑、模型按需读取，SKILL.md是唯一正文来源。

用户技能持久放data/skills；只加载元信息或指定资源，不安装即执行脚本。
"""

import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
import tempfile
import os
import yaml
from config_data import BASE_DIR
from agent import persistence

SKILL_DIR = BASE_DIR / "data" / "skills"
CATEGORIES = {"agent", "python", "cyberSecurity", "core"}


def location(slug, resource="SKILL.md"):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(slug)) or len(slug) > 64:
        raise ValueError("技能标识用小写字母、数字和短横线，最多64字符")
    root = (SKILL_DIR / slug).resolve()
    if not root.is_relative_to(SKILL_DIR.resolve()):
        raise ValueError("技能目录越界")
    target = (root / resource).resolve()
    if not target.is_relative_to(root) or any(
        p.startswith(".") for p in Path(resource).parts
    ):
        raise ValueError("技能资源路径越界")
    return target


def parse(text, slug):
    if len(text.encode("utf-8")) > 65536 or not text.startswith("---\n"):
        raise ValueError("需要不超过64KiB的SKILL.md和YAML元信息")
    fields = re.fullmatch(r"---\n(.*?)\n---(?:\n|$)(.*)", text, re.S)
    if not fields or len(fields[1]) > 8192:
        raise ValueError("技能元信息格式或长度不对")
    try:
        if any(
            isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(fields[1])
        ):
            raise ValueError("技能元信息不支持YAML别名")
        metadata = yaml.safe_load(fields[1])
    except yaml.YAMLError as exc:
        raise ValueError("技能YAML元信息无效") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != slug:
        raise ValueError("name必须与技能目录标识一致")
    description = metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        raise ValueError("description需要1到1024字符")
    category = (
        (metadata.get("metadata") or {}).get("category", "agent")
        if isinstance(metadata.get("metadata", {}), dict)
        else "agent"
    )
    if category not in CATEGORIES:
        raise ValueError("技能分类无效")
    return {
        "id": slug,
        "name": slug,
        "description": description,
        "category": category,
        "body": fields[2].strip(),
        "revision": hashlib.sha256(text.encode()).hexdigest(),
        "content": text,
    }


def get(slug):
    target = location(slug)
    if not target.is_file():
        raise ValueError("技能不存在")
    if target.stat().st_size > 65536:
        raise ValueError("技能正文过大")
    result = parse(target.read_text(encoding="utf-8-sig").replace("\r\n", "\n"), slug)
    result["files"] = [
        p.relative_to(target.parent).as_posix()
        for p in target.parent.rglob("*")
        if p.is_file() and p.resolve().is_relative_to(target.parent.resolve())
    ][:100]
    return result


def catalog():
    rows = []
    if not SKILL_DIR.exists():
        return rows
    for root in sorted(SKILL_DIR.iterdir()):
        if not root.is_dir():
            continue
        try:
            item = get(root.name)
            rows.append(
                {k: v for k, v in item.items() if k not in {"body", "content", "files"}}
            )
        except (ValueError, OSError, yaml.YAMLError) as exc:
            rows.append({"id": root.name, "invalid": True, "error": str(exc)[:200]})
    return rows


@persistence.transaction
def save(slug, text, expected=None):
    result = parse(text.replace("\r\n", "\n"), slug)
    target = location(slug)
    if target.exists() and expected != get(slug)["revision"]:
        raise ValueError("技能已变化，先重新读取再保存")
    if not target.exists() and len(catalog()) >= 100:
        raise ValueError("最多100个技能，先整理")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".md.tmp")
    try:
        temp.write_text(result["content"], encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return get(slug)


@persistence.transaction
def delete(slug, expected):
    item = get(slug)
    if item["revision"] != expected:
        raise ValueError("技能已变化，不能删除旧版本")
    root = location(slug).parent
    # 只清理此技能内经根路径核验的文件；不跟随外部链接。
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.resolve().is_relative_to(root):
            raise ValueError("技能含外部链接，先手工整理")
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    root.rmdir()
    return {"ok": True}


def read_resource(slug, resource):
    path = location(slug, resource)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("资源不存在或超过64KiB")
    return path.read_text(encoding="utf-8")


def templates():
    methods = {
        "agent": (
            "agent-review",
            "流式Agent审查",
            "沿调用链检查messages配对、流式分片、用量、降级、取消和上下文。先给结论，再列文件行证据；只改最小范围，真实验证。",
        ),
        "python": (
            "python-repair",
            "Python开发与验证",
            "先确认解释器与依赖；静态分析定位问题，最小修改后使用tmp隔离数据验证。不要执行来源不明的代码，给出结果和限制。",
        ),
        "cyberSecurity": (
            "security-audit",
            "授权安全审计",
            "先明确授权目标、范围和时段。被动分析优先；主动请求只访问授权目标，未经验证的线索不写成确定漏洞。记录证据和修复建议。",
        ),
    }
    return [
        {
            "id": slug,
            "category": cat,
            "description": desc,
            "content": "---\nname: "
            + slug
            + "\ndescription: "
            + json.dumps(desc, ensure_ascii=False)
            + "\nmetadata:\n  category: "
            + cat
            + "\n---\n\n"
            + body
            + "\n",
        }
        for cat, (slug, desc, body) in methods.items()
    ]


@persistence.transaction
def import_archive(data):
    """ZIP先在tmp验证，完整通过后原子发布新技能；不能覆盖已有技能。"""
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("技能包最多2MiB")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if (
            not entries
            or len(entries) > 25
            or sum(e.file_size for e in entries) > 2 * 1024 * 1024
        ):
            raise ValueError("技能包为空、超过25文件或解压后超过2MiB")
        names = [Path(e.filename.replace("\\", "/")) for e in entries]
        if len({str(n).lower() for n in names}) != len(names):
            raise ValueError("技能包存在重复或大小写冲突路径")
        for entry, path in zip(entries, names):
            if (
                path.is_absolute()
                or ".." in path.parts
                or ":" in str(path)
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("技能包含越界路径或链接")
        markdown = [n for n in names if n.name == "SKILL.md"]
        if len(markdown) != 1:
            raise ValueError("需要唯一的SKILL.md")
        prefix = markdown[0].parent
        if any(not n.is_relative_to(prefix) for n in names):
            raise ValueError("资源必须在SKILL.md同一目录内")
        text = (
            archive.read(entries[names.index(markdown[0])])
            .decode("utf-8-sig")
            .replace("\r\n", "\n")
        )
        match = re.fullmatch(r"---\n(.*?)\n---(?:\n|$)(.*)", text, re.S)
        if not match or len(text.encode()) > 65536 or len(match[1]) > 8192:
            raise ValueError("技能元信息缺失或超限")
        if any(isinstance(t, yaml.tokens.AliasToken) for t in yaml.scan(match[1])):
            raise ValueError("元信息不支持别名")
        try:
            slug = yaml.safe_load(match[1]).get("name")
        except (yaml.YAMLError, AttributeError):
            raise ValueError("技能元信息无效") from None
        parse(text, slug)
        destination = location(slug).parent
        if destination.exists():
            raise ValueError("技能已存在，先用编辑器核对版本")
        if len(catalog()) >= 100:
            raise ValueError("最多100个技能，先整理")
        (BASE_DIR / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="skill-import-", dir=BASE_DIR / "tmp"
        ) as temp:
            stage = Path(temp) / slug
            stage.mkdir()
            for entry, path in zip(entries, names):
                rel = path.relative_to(prefix)
                if any(part.startswith(".") for part in rel.parts):
                    raise ValueError("技能不接受隐藏凭据文件")
                target = (stage / rel).resolve()
                if not target.is_relative_to(stage.resolve()):
                    raise ValueError("资源越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
            SKILL_DIR.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
    return get(slug)


def export_archive(slug):
    get(slug)
    root = location(slug).parent
    out = io.BytesIO()
    total = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError("技能资源越界")
            total += path.stat().st_size
            if total > 2 * 1024 * 1024:
                raise ValueError("技能包超过2MiB")
            archive.write(path, arcname=slug + "/" + path.relative_to(root).as_posix())
    return out.getvalue()
