"""外观目录：供 SilverWolf API 读取随项目资产与本机同人预设。

本机 data/images 的有效分组优先展示，空分组退回静态目录。
所有文件先校验真实路径；URL 由服务生成，不能借 manifest 指向外部站点。
此模块只读资产，不改用户上传文件或头像选择。
"""
import json
import logging
import re
import hashlib
import threading

from config_data import BASE_DIR

ASSET_DIR = BASE_DIR / "static" / "presets"
LOCAL_ASSET_DIR = BASE_DIR / "data" / "images"
GROUPS = ("backgrounds", "bot", "user")
IDENTITY = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
logger = logging.getLogger(__name__)
_IMAGE_LOCK = threading.Lock()
_DIGEST_LOCK = threading.Lock()
_DIGEST_CACHE = {}


def _digest(path):
    """按 (mtime, size) 缓存文件摘要，目录被反复读取时不再重复全量读盘。"""
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    key = str(path)
    with _DIGEST_LOCK:
        cached = _DIGEST_CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    with _DIGEST_LOCK:
        _DIGEST_CACHE[key] = (stamp, digest)
    return digest


def _uploads(group):
    """照片文件本身就是收藏索引；按内容生成 ID，不再维护第二份队列文件。"""
    rows = []
    if not LOCAL_ASSET_DIR.exists():
        return rows
    files = sorted(LOCAL_ASSET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    seen = set()
    for path in files:
        if not path.resolve().is_relative_to(LOCAL_ASSET_DIR.resolve()):
            continue
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            continue
        if not (path.name.startswith(f"upload-{group}-") or (group in ("bot", "user") and path.stem == group)):
            continue
        digest = _digest(path)
        identity = f"upload-{group}-{digest}"
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({"id": identity, "name": f"我的上传 · {len(rows)+1}", "file": path.name, "origin": "upload"})
    return rows


def save_upload(group, filename, data):
    """追加上传图片，不替换旧图；同一图片重复上传复用收藏。"""
    if group not in GROUPS:
        raise ValueError("图片分类无效")
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    limits = 3 * 1024 * 1024 if group == "backgrounds" else 2 * 1024 * 1024
    valid = {"png": data.startswith(b"\x89PNG\r\n\x1a\n"), "jpg": data.startswith(b"\xff\xd8\xff"),
             "jpeg": data.startswith(b"\xff\xd8\xff"), "gif": data.startswith((b"GIF87a", b"GIF89a")),
             "webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
    if not data or len(data) > limits or not valid.get(ext, False):
        raise ValueError("图片格式不对，或文件太大。头像 2MB，背景 3MB。")
    digest = hashlib.sha256(data).hexdigest()[:24]
    identity = f"upload-{group}-{digest}"
    with _IMAGE_LOCK:
        for item in _uploads(group):
            if item["id"] == identity:
                return {"id": identity, "url": f"/api/appearance-presets/{group}/{identity}/image"}
        if len(_uploads(group)) >= 100:
            raise ValueError("收藏已到 100 张，先整理本机图片。")
        LOCAL_ASSET_DIR.mkdir(parents=True, exist_ok=True)
        (LOCAL_ASSET_DIR / f"{identity}.{ext}").write_bytes(data)
    return {"id": identity, "url": f"/api/appearance-presets/{group}/{identity}/image"}


def _load(root):
    """读取并验证目录；坏清单回退，原文件保持原样。"""
    result = {group: [] for group in GROUPS}
    manifest = root / "manifest.json"
    if not manifest.exists():
        return result
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("清单必须为对象")
        for group in GROUPS:
            rows = raw.get(group, [])
            if not isinstance(rows, list):
                continue
            seen = set()
            for item in rows[:50]:
                if not isinstance(item, dict):
                    continue
                identity, name, filename = (item.get(key) for key in ("id", "name", "file"))
                if not isinstance(identity, str) or not IDENTITY.fullmatch(identity) or identity in seen:
                    continue
                if not isinstance(name, str) or not name.strip() or len(name) > 80:
                    continue
                if not isinstance(filename, str):
                    continue
                path = (root / filename).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    continue
                if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    continue
                seen.add(identity)
                result[group].append({"id": identity, "name": name, "file": filename})
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("外观清单无效，回退可用预设：%s", type(exc).__name__)
    return result


def _sources():
    packaged, local = _load(ASSET_DIR), _load(LOCAL_ASSET_DIR)
    return {group: (LOCAL_ASSET_DIR, local[group]) if local[group] else (ASSET_DIR, packaged[group])
            for group in GROUPS}


def catalog():
    """返回 UI 可用目录；本机图片走已鉴权 API，不暴露磁盘路径。"""
    sources = _sources()
    result = {"kind": "fan_art" if any(root == LOCAL_ASSET_DIR for root, _ in sources.values()) else "original"}
    for group, (root, rows) in sources.items():
        result[group] = [dict(item, url=f"/api/appearance-presets/{group}/{item['id']}/image"
                             if root == LOCAL_ASSET_DIR else f"/static/presets/{item['file']}") for item in rows]
        result[group].extend(dict(item, url=f"/api/appearance-presets/{group}/{item['id']}/image") for item in _uploads(group))
    return result


def asset_path(group, identity):
    """按公开目录解析图片；非法分组、ID 或不存在的预设均拒绝。"""
    if group not in GROUPS or not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
        raise ValueError("外观预设无效")
    root, rows = _sources()[group]
    for item in rows:
        if item["id"] == identity:
            return (root / item["file"]).resolve()
    for item in _uploads(group):
        if item["id"] == identity:
            path = (LOCAL_ASSET_DIR / item["file"]).resolve()
            if path.is_relative_to(LOCAL_ASSET_DIR.resolve()):
                return path
    raise ValueError("外观预设不存在")


def avatar_path(role, identity):
    """供头像选择和读取接口复用；背景不能充当头像。"""
    if role not in ("bot", "user"):
        raise ValueError("头像角色无效")
    return asset_path(role, identity)
