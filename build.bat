@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到虚拟环境，请先运行 install.bat。
  pause
  exit /b 1
)

rem 让 PyInstaller 的隔离子进程能探测到 tkinter（Tcl/Tk 库路径）
set "TCL_LIBRARY=%~dp0.venv\Lib\tcl8.6"
set "TK_LIBRARY=%~dp0.venv\Lib\tk8.6"

echo 正在打包独立 exe（约需 1-3 分钟）...
".venv\Scripts\python.exe" -m PyInstaller --clean "多多朗读.spec"
if errorlevel 1 goto :err

echo.
echo 打包完成：dist\多多朗读.exe
pause
exit /b 0

:err
echo.
echo [错误] 打包失败，请检查上方日志。
pause
exit /b 1
