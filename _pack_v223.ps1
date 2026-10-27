# -*- coding: utf-8 -*-
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1"
$env:TCL_LIBRARY = "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1\.venv\Lib\tcl8.6"
$env:TK_LIBRARY = "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1\.venv\Lib\tk8.6"
Write-Output "== clean old =="
if (Test-Path -LiteralPath "dist\多多朗读") { Remove-Item -LiteralPath "dist\多多朗读" -Recurse -Force }
if (Test-Path -LiteralPath "build\多多朗读") { Remove-Item -LiteralPath "build\多多朗读" -Recurse -Force }
Write-Output "== build onedir =="
& ".venv\Scripts\python.exe" -m PyInstaller --clean -y "多多朗读.spec"
if ($LASTEXITCODE -ne 0) { Write-Output "ONEDIR FAIL"; exit 1 }
Write-Output "== build onefile =="
& ".venv\Scripts\python.exe" -m PyInstaller --clean -y "多多朗读_onefile.spec"
if ($LASTEXITCODE -ne 0) { Write-Output "ONEFILE FAIL"; exit 1 }
Write-Output "== zip onedir =="
Compress-Archive -Path "dist\多多朗读\*" -DestinationPath "多多朗读_v2.2.3_Windows_onedir.zip" -Force
Write-Output "== artifacts =="
Get-ChildItem -LiteralPath "dist" | ForEach-Object { Write-Output ("dist/" + $_.Name) }
Write-Output "PACK DONE"