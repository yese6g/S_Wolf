#!/usr/bin/env bash
# Silver Wolf - 一键安装（Linux / macOS）
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Silver Wolf v1.0.0 - install"
echo "============================================"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] python3/python not found. Install Python 3.10+ first."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    "$PY" -m venv .venv
fi

".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt

if [ ! -f "frontend/vendor/vue.global.prod.js" ]; then
    echo "[INFO] Vue runtime missing, downloading (jsdelivr -> unpkg -> npmmirror)..."
    mkdir -p frontend/vendor
    curl -fSL --connect-timeout 15 -o frontend/vendor/vue.global.prod.js \
        "https://cdn.jsdelivr.net/npm/vue@3.4.27/dist/vue.global.prod.js" || \
    curl -fSL --connect-timeout 15 -o frontend/vendor/vue.global.prod.js \
        "https://unpkg.com/vue@3.4.27/dist/vue.global.prod.js" || \
    curl -fSL --connect-timeout 15 -o frontend/vendor/vue.global.prod.js \
        "https://registry.npmmirror.com/vue/3.4.27/files/dist/vue.global.prod.js"
    if [ ! -f "frontend/vendor/vue.global.prod.js" ]; then
        echo "[ERROR] Vue runtime download failed. Check network and rerun."
        exit 1
    fi
fi

if command -v ollama >/dev/null 2>&1; then
    ollama --version
else
    echo "[INFO] ollama not found (optional if cloud-only)."
fi

echo
echo "Done. Run: .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000"
