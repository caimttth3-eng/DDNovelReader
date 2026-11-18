# -*- coding: utf-8 -*-
# 打包脚本（当前版本 v2.2.9）。用法（执行策略默认禁止跑脚本，必须显式 Bypass）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File _pack_v229.ps1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1"
$env:TCL_LIBRARY = "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1\.venv\Lib\tcl8.6"
$env:TK_LIBRARY = "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1\.venv\Lib\tk8.6"
Write-Output "== clean old =="
if (Test-Path -LiteralPath "dist\多多朗读") { Remove-Item -LiteralPath "dist\多多朗读" -Recurse -Force }
if (Test-Path -LiteralPath "build\多多朗读") { Remove-Item -LiteralPath "build\多多朗读" -Recurse -Force }
if (Test-Path -LiteralPath "build\多多朗读_onefile") { Remove-Item -LiteralPath "build\多多朗读_onefile" -Recurse -Force }
Write-Output "== build onedir =="
& ".venv\Scripts\python.exe" -m PyInstaller --clean -y "多多朗读.spec"
if ($LASTEXITCODE -ne 0) { Write-Output "ONEDIR FAIL"; exit 1 }
Write-Output "== build onefile =="
& ".venv\Scripts\python.exe" -m PyInstaller --clean -y "多多朗读_onefile.spec"
if ($LASTEXITCODE -ne 0) { Write-Output "ONEFILE FAIL"; exit 1 }
Write-Output "== zip onedir（注意：压缩整个文件夹，产物内保留 多多朗读\ 目录，与 2.2.7/2.2.8 发布件一致）=="
Compress-Archive -Path "dist\多多朗读" -DestinationPath "多多朗读_v2.2.9_Windows_onedir.zip" -Force
Write-Output "== artifacts =="
Get-ChildItem -LiteralPath "dist" | ForEach-Object { Write-Output ("dist/" + $_.Name) }
Get-Item -LiteralPath "多多朗读_v2.2.9_Windows_onedir.zip" | ForEach-Object { Write-Output ("root/" + $_.Name + " " + $_.Length) }
Write-Output "PACK DONE"
