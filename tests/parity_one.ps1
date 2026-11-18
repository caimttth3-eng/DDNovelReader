param([string]$Src, [string]$Title, [string]$Ch = '6')
$pcRoot = 'C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1'
$anRoot = 'C:\Users\lyuga\Doubao\chats\2026-09-01\new-chat\duoduo_langdu'
$py = "$pcRoot\.venv\Scripts\python.exe"
$json = "$anRoot\build\parity_one.json"
Push-Location $anRoot
& flutter test test\cache_parity_test.dart `
  "--dart-define=PARITY_SRC=$Src" "--dart-define=PARITY_TITLE=$Title" `
  "--dart-define=PARITY_OUT=$json" "--dart-define=PARITY_CH=$Ch" 2>&1 | Out-Null
Pop-Location
Push-Location $pcRoot
& $py tests\parity_dump.py $Src $Title $json $Ch 2>&1 | Out-File -LiteralPath "$pcRoot\build\parity_one.txt" -Encoding utf8
Pop-Location
