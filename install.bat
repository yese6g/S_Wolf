@echo off
title Silver Wolf - 一键安装（Python + Vue + 依赖）
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Silver Wolf v2.2.1  一键安装
echo    自动完成：Python（缺失时）→ 虚拟环境 → 依赖 → Vue 前端
echo  ============================================================
echo.

echo  [1/5] 检查 Python ...
set "PYCMD="
where python >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    where py >nul 2>&1 && set "PYCMD=py -3"
)
if not defined PYCMD (
    echo   未找到 Python，尝试通过 winget 自动安装 ...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo   [错误] 未找到 winget。
        echo   请手动下载安装 Python 3.10 以上版本，并勾选“加入 PATH”：
        echo     https://www.python.org/downloads/
        echo   安装完成后重新运行本脚本。
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    where python >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo   [错误] Python 安装未生效，请手动安装后重试：
    echo     https://www.python.org/downloads/
    pause
    exit /b 1
)
%PYCMD% --version

echo  [2/5] 创建虚拟环境（已存在则跳过）...
if not exist ".venv\Scripts\python.exe" (
    %PYCMD% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo   [错误] 虚拟环境创建失败。
    pause
    exit /b 1
)

echo  [3/5] 安装 Python 依赖 ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo   [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo  [4/5] 检查并下载前端运行时（Vue 3，已存在则跳过）...
if exist "frontend\vendor\vue.global.prod.js" (
    echo   前端运行时已就绪（frontend\vendor\vue.global.prod.js）
) else (
    if not exist "frontend\vendor" mkdir "frontend\vendor"
    echo   未找到前端运行时，开始下载（jsdelivr → unpkg → npmmirror）...
    curl -fSL --connect-timeout 15 -o "frontend\vendor\vue.global.prod.js" "https://cdn.jsdelivr.net/npm/vue@3.4.27/dist/vue.global.prod.js"
    if errorlevel 1 curl -fSL --connect-timeout 15 -o "frontend\vendor\vue.global.prod.js" "https://unpkg.com/vue@3.4.27/dist/vue.global.prod.js"
    if errorlevel 1 curl -fSL --connect-timeout 15 -o "frontend\vendor\vue.global.prod.js" "https://registry.npmmirror.com/vue/3.4.27/files/dist/vue.global.prod.js"
    if not exist "frontend\vendor\vue.global.prod.js" (
        echo   [错误] 前端运行时下载失败。请检查网络后重试本脚本。
        pause
        exit /b 1
    )
    echo   前端运行时下载完成
)

echo  [5/5] 检查 Ollama（本地模型，可选）...
where ollama >nul 2>&1
if errorlevel 1 (
    echo   [提示] 未检测到 Ollama。只用云端模型可忽略；
    echo          需要本地模型请安装 https://ollama.com 后执行：ollama pull deepseek-r1:8b
) else (
    ollama --version
    echo   [提示] 如需本地模型，请确认已执行：ollama pull deepseek-r1:8b
)

echo.
echo  安装完成！运行 start.bat 启动，浏览器访问 http://127.0.0.1:8000
echo  首次使用请在左下角设置中添加模型（默认不预置任何模型）。
pause
