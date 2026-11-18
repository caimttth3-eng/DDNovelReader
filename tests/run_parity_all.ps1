$ErrorActionPreference = 'Continue'
$pcRoot = 'C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1'
$anRoot = 'C:\Users\lyuga\Doubao\chats\2026-09-01\new-chat\duoduo_langdu'
$py = "$pcRoot\.venv\Scripts\python.exe"
$log = "$pcRoot\build\parity_all.log"
New-Item -ItemType Directory -Force -Path "$pcRoot\build" | Out-Null

$books = @(
  @{ id = '12a4f061aa826430'; title = '《半岛：她们怎么都说是我老婆啊》（全本）作者：我家有一条大鱼【TXT】' },
  @{ id = 'b474297b6fda6d7c'; title = '《大唐第一仙》（全本）作者：言归正传【TXT】' },
  @{ id = 'ab55486ad2ed1d35'; title = '傲娇大小姐的奇妙恋情(trxs.cc)' },
  @{ id = '5ccaa0d73dc71a34'; title = '柯学：黄毛侦探(trxs.cc)' },
  @{ id = 'ff5d96a098a8e94d'; title = '恋爱从继承父上遗产开始 - 小小汤包灬' },
  @{ id = '26006bdc71a243eb'; title = '都当冒险者了，谁还自己努力啊！(trxs.cc)' },
  @{ id = '481bc54f0e702194'; title = '谁让你专吃窝边草的？_分章结果' },
  @{ id = '50f67fcd4f956ad2'; title = '天后老婆又闹离婚了_分章结果' },
  @{ id = 'f6caea080374886f'; title = '仙子失格_分章结果' },
  @{ id = '4510f8d18601ab85'; title = '这个装备栏太棒了_分章结果' },
  @{ id = '4ee4bb9a6ccb4bbb'; title = '高中生！长生仙族系统什么鬼！_分章结果' },
  @{ id = '76c1f430f3a3e4ca'; title = '你们本地魔法少女怎么是男的？(trxs.cc)' },
  @{ id = 'e4401341fc2a3757'; title = '被迫成为魔法少女公敌！(trxs.cc)' },
  @{ id = '53c790e192580939'; title = '早知道男声优也会被潜' },
  @{ id = 'e20bbd750062c514'; title = '恋爱要在食戟之后(trxs.cc)' }
)

Set-Content -LiteralPath $log -Value '' -Encoding utf8
foreach ($b in $books) {
  $src = "F:\DDNovelReader\cache\sources\$($b.id).txt"
  if (-not (Test-Path -LiteralPath $src)) {
    Add-Content -LiteralPath $log -Value ("$($b.id) SKIP no source") -Encoding utf8
    continue
  }
  $anJson = "$anRoot\build\parity_cur.json"
  # 必须先删除上一次的产物，否则 flutter test 失败时会拿旧 JSON 比对出假结果
  if (Test-Path -LiteralPath $anJson) { Remove-Item -LiteralPath $anJson -Force }
  Push-Location $anRoot
  $out = & flutter test test\cache_parity_test.dart `
      "--dart-define=PARITY_SRC=$src" `
      "--dart-define=PARITY_TITLE=$($b.title)" `
      "--dart-define=PARITY_OUT=$anJson" `
      "--dart-define=PARITY_CH=6" 2>&1 | Out-String
  Pop-Location
  if (-not (Test-Path -LiteralPath $anJson)) {
    Add-Content -LiteralPath $log -Value ("$($b.id) ANDROID_FAIL " + ($out.Substring([Math]::Max(0, $out.Length - 200))) ) -Encoding utf8
    continue
  }
  Push-Location $pcRoot
  $cmp = & $py tests\parity_dump.py $src $b.title $anJson 6 2>&1 | Out-String
  Pop-Location
  $verdict = ($cmp -split "`n" | Select-String -Pattern '====' | Select-Object -Last 1).ToString().Trim()
  $keys = ($cmp -split "`n" | Select-String -Pattern 'PC: key=|AN: key=' | ForEach-Object { $_.ToString().Trim() }) -join ' | '
  Add-Content -LiteralPath $log -Value ("$($b.id)  $verdict  $keys") -Encoding utf8
  if ($verdict -match '^====\s+0 FAIL') { continue }
  Add-Content -LiteralPath $log -Value $cmp -Encoding utf8
}
Add-Content -LiteralPath $log -Value 'DONE' -Encoding utf8
