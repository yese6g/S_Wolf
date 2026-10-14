"""模型接入预设与 Ollama 本机发现，由设置页面读取；不自动修改现有 Provider。

云端候选来自官方文档（核对日期 2026-10-11），可编辑；本机发现走 /api/tags，
只访问回环地址，短超时，无需先保存提供商，更不会自动下载模型。
"""
from urllib.parse import urlsplit, urlunsplit
import httpx

PRESETS = [
    {"id":"ollama", "name":"本地 Ollama", "kind":"local", "base_url":"http://localhost:11434/v1", "models":[],
     "docs":"https://docs.ollama.com/api/tags", "note":"扫描已安装模型，不下载。"},
    {"id":"deepseek", "name":"DeepSeek", "kind":"cloud", "base_url":"https://api.deepseek.com/v1",
     "models":["deepseek-flash", "deepseek-v4-pro"], "docs":"https://api-docs.deepseek.com/", "note":"标准按量 API；填你自己的 Key。"},
    {"id":"mimo", "name":"小米 MiMo", "kind":"cloud", "base_url":"https://api.xiaomimimo.com/v1",
     "models":["mimo-v2.6-pro", "mimo-v2.6-flash"], "docs":"https://mimo.mi.com/docs/quick-start/model", "note":"标准 API，套餐地址可能不同，可手动改。"},
    {"id":"qwen", "name":"通义千问 · 北京", "kind":"cloud", "base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models":["qwen-plus", "qwen-turbo", "qwen-max"], "docs":"https://help.aliyun.com/en/model-studio/model-calling-in-sub-workspace", "note":"北京标准 API；其他地域或 Coding Plan 请改专用地址。"},
]


def discover_ollama(base_url="http://localhost:11434/v1"):
    parsed=urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
        raise ValueError("自动扫描只支持本机 Ollama；远程地址请手动添加。")
    root=urlunsplit((parsed.scheme,parsed.netloc,"","",""))
    try:
        with httpx.Client(timeout=3,trust_env=False) as client:
            response=client.get(root+"/api/tags")
            response.raise_for_status()
            payload=response.json()
            rows=payload.get("models",[]) if isinstance(payload,dict) else None
            if not isinstance(rows,list):
                raise ValueError("Ollama 模型列表格式无效")
        # 某些代理会把错误对象混进列表；只接受非空字符串，避免排序时类型冲突。
        names=(row.get("name") or row.get("model") for row in rows if isinstance(row,dict))
        models=sorted({name for name in names if isinstance(name,str) and name.strip()})
        return {"ok":True,"base_url":root+"/v1","models":models,"message":"扫描到了，选一个就能接入。" if models else "Ollama 在线，但还没有安装模型。"}
    except (httpx.HTTPError,ValueError) as exc:
        return {"ok":False,"base_url":root+"/v1","models":[],"message":"没连上 Ollama。启动服务后再扫，也可以手动填模型名。"}
