@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo 正在创建 Python 虚拟环境...
python -m venv .venv
if errorlevel 1 goto :err

echo 正在安装依赖（TTS / PDF / Word / mobi / 编码检测）...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo 依赖安装完成！请双击「启动小说阅读器.bat」开始使用。
pause
exit /b 0

:err
echo.
echo [错误] 安装失败，请检查网络后重试。
pause
exit /b 1
