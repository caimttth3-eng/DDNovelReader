# -*- coding: utf-8 -*-
# 打包脚本（v2.2.9 起，用户 2026-11-18 指定）：
#   默认：只构建单文件 exe → 先出到工作区临时目录 → 用 PowerShell 复制到「桌面」，不进 dist\。
#   加 -Release 时才构建 onedir + 压缩包到 dist\（仅供发布一条龙第 5 步使用，需用户下令）。
# 用法（执行策略默认禁止跑脚本，必须显式 Bypass）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File _pack_v229.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File _pack_v229.ps1 -Release
# 注意：不能把 --distpath 直接指向桌面 —— Defender「受控文件夹访问」会拒绝 python 子进程在桌面建文件
#       （PowerShell 是受信进程，所以复制这一步用 Move-Item/Copy-Item 就行）。见开发指南坑 29。
param([switch]$Release)
$ErrorActionPreference = "Stop"
$root = "C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1"
Set-Location -LiteralPath $root
$env:TCL_LIBRARY = Join-Path $root ".venv\Lib\tcl8.6"
$env:TK_LIBRARY = Join-Path $root ".venv\Lib\tk8.6"
$ver = "v2.2.9"
$desktop = "C:\Desktop"
$stage = Join-Path $root "_exe_out"

Write-Output "== 构建单文件 exe（先落工作区临时目录）=="
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath "build\多多朗读_onefile") { Remove-Item -LiteralPath "build\多多朗读_onefile" -Recurse -Force }
& ".venv\Scripts\python.exe" -m PyInstaller --clean -y --distpath $stage "多多朗读_onefile.spec"
if ($LASTEXITCODE -ne 0) { Write-Output "ONEFILE FAIL"; exit 1 }

$dst = Join-Path $desktop ("多多朗读_{0}_Windows.exe" -f $ver)
# 桌面上删不动旧文件（受控文件夹访问 / 可能被占用），直接覆盖即可
Copy-Item -LiteralPath (Join-Path $stage "多多朗读.exe") -Destination $dst -Force
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
Get-Item -LiteralPath $dst | ForEach-Object {
    Write-Output ("桌面 exe：" + $_.FullName + "  " + [math]::Round($_.Length/1MB,2) + " MiB")
}

if ($Release) {
    Write-Output "== -Release：构建 onedir + 压缩包到 dist\（发布用）=="
    if (Test-Path -LiteralPath "dist\多多朗读") { Remove-Item -LiteralPath "dist\多多朗读" -Recurse -Force }
    if (Test-Path -LiteralPath "build\多多朗读") { Remove-Item -LiteralPath "build\多多朗读" -Recurse -Force }
    & ".venv\Scripts\python.exe" -m PyInstaller --clean -y "多多朗读.spec"
    if ($LASTEXITCODE -ne 0) { Write-Output "ONEDIR FAIL"; exit 1 }
    Compress-Archive -Path "dist\多多朗读" -DestinationPath ("多多朗读_{0}_Windows_onedir.zip" -f $ver) -Force
    Get-Item -LiteralPath ("多多朗读_{0}_Windows_onedir.zip" -f $ver) | ForEach-Object {
        Write-Output ("根目录 zip：" + $_.Name + "  " + [math]::Round($_.Length/1MB,2) + " MiB")
    }
}
Write-Output "PACK DONE"
