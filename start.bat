@echo off
cd /d "%~dp0"
title Silver Wolf - 星核猎手黑客 AI

echo.
echo  ═══════════════════════════════════════════════
echo   Silver Wolf v3.1.0  正在启动...
echo   本地模型依赖 Ollama (http://localhost:11434)
echo   请确保已执行: ollama pull deepseek-r1:8b
echo   浏览器访问: http://127.0.0.1:8000
echo  ═══════════════════════════════════════════════
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv，请先执行:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

netstat -ano | findstr /C:":8000 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo  [警告] 端口 8000 已被占用，可能已经有 Silver Wolf 在运行。
    echo  请先关闭旧实例（任务管理器结束 python.exe），或改 start.bat 里的端口号。
    echo.
    pause
    exit /b 1
)

echo.
echo  正在启动，稍候浏览器访问 http://127.0.0.1:8000
echo  按 Ctrl+C 停止服务。
echo.
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
