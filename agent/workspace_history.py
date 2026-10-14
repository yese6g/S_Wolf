"""工作区变更账本：写删后保存前后版本，API/工具可查看 diff 与受保护回滚。

回滚前检查当前内容仍等于那次修改后的版本，避免覆盖用户后来手工编辑的文件。
只支持工作区 UTF-8 小文件和空目录，历史总配额 50 MiB；不修改项目 Git 仓库。
"""
import base64
import difflib
import hashlib
import time
import uuid
from config_data import BASE_DIR
from agent import persistence

HISTORY_DIR = BASE_DIR / "data" / "workspace_history"
MAX_HISTORY_BYTES = 50*1024*1024


def reserve(needed, protected=()):
    """为新记录滚动腾出空间，回滚正在读取的记录受保护。"""
    if needed > MAX_HISTORY_BYTES:
        raise ValueError('单次变更历史超过目录配额')
    with persistence.LOCK:
        paths = sorted(HISTORY_DIR.glob('*.json'),key=lambda p:p.stat().st_mtime)
        used = sum(p.stat().st_size for p in paths)
        removed = 0
        for path in paths:
            if used + needed <= MAX_HISTORY_BYTES: break
            if path.stem in protected or not path.resolve().is_relative_to(HISTORY_DIR.resolve()): continue
            used -= path.stat().st_size
            path.unlink(); removed += 1
        if used + needed > MAX_HISTORY_BYTES:
            raise ValueError('受保护历史占满配额，先完成当前回滚')
        return removed


def snapshot(path):
    if not path.exists():return {"kind":"absent", "data":"", "sha":"absent"}
    if path.is_dir():return {"kind":"dir", "data":"", "sha":"dir"}
    raw=path.read_bytes()
    if len(raw)>1024*1024:raise ValueError("文件过大，无法安全存档")
    return {"kind":"file", "data":base64.b64encode(raw).decode(), "sha":hashlib.sha256(raw).hexdigest()}


def record(rel, before, after, scope='workspace', workspace=None):
    with persistence.LOCK:
        reserve(len(before['data'])+len(after['data'])+2048)
        row={"id":uuid.uuid4().hex,"path":rel,"scope":scope,"created_at":time.time(),"before":before,"after":after}
        if workspace is not None:row["workspace"]=str(workspace)
        persistence.save(HISTORY_DIR,row['id'],row)
        return row['id']


def list_changes():
    if not HISTORY_DIR.exists():return []
    rows=[persistence.load(HISTORY_DIR,p.stem) for p in HISTORY_DIR.glob('*.json')]
    return sorted([{k:v for k,v in row.items() if k not in {'before','after'}} for row in rows],key=lambda row:row['created_at'],reverse=True)


def diff(identity):
    row=persistence.load(HISTORY_DIR,identity)
    if not row:raise ValueError('变更不存在')
    before=base64.b64decode(row['before']['data']).decode('utf-8',errors='replace').splitlines(True)
    after=base64.b64decode(row['after']['data']).decode('utf-8',errors='replace').splitlines(True)
    return {"id":identity,"path":row['path'],"diff":''.join(difflib.unified_diff(before,after,fromfile='修改前/'+row['path'],tofile='修改后/'+row['path']))[:200000]}


def rollback(identity):
    from agent.tools.core import workspace
    from agent.tools.core.workspace import _safe_resolve, _IO_LOCK
    with _IO_LOCK:
        row=persistence.load(HISTORY_DIR,identity)
        if not row:raise ValueError('变更不存在')
        if row.get('scope','workspace')!='workspace':raise ValueError('项目变更须从Lv.999授权入口回滚')
        target=_safe_resolve(row['path'])
        if snapshot(target)['sha'] != row['after']['sha']:
            raise ValueError('文件后来又被改过，不能直接回滚；先核对 diff。')
        reserve(len(row['before']['data'])+len(row['after']['data'])+2048,protected=(identity,))
        prior=row['before']
        if prior['kind']=='file':
            restored=base64.b64decode(prior['data'])
            current=sum(p.stat().st_size for p in workspace.WORKSPACE_DIR.rglob('*') if p.is_file())
            previous=target.stat().st_size if target.is_file() else 0
            if len(restored)>workspace.MAX_WRITE_BYTES or current-previous+len(restored)>workspace.MAX_WORKSPACE_BYTES:
                raise ValueError('回滚后会超过工作区配额，先清理空间。')
        if prior['kind']=='absent':
            if target.is_dir():target.rmdir()
            else:target.unlink(missing_ok=True)
        elif prior['kind']=='dir':target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(restored)
        reverse=record(row['path'],row['after'],snapshot(target))
        return {"ok":True,"path":row['path'],"change_id":reverse}
