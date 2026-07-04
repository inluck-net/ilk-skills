$env:ILK_DOTSOURCE_ONLY = '1'
$watchdogPath = 'C:\mywork\github\inluck-net\ilk-skills\skills\ilk-watchdog\scripts\watchdog.ps1'
$projectPath = 'C:\Users\chad\AppData\Local\Temp\pytest-debug'
New-Item -ItemType Directory -Force -Path $projectPath | Out-Null

. $watchdogPath -ProjectPath $projectPath

$exists = Get-Command Get-StartupSentinelAction -ErrorAction SilentlyContinue
Write-Host "Function exists: $($null -ne $exists)"

$lt = [datetime]'2026-07-03T12:00:00'
$a = Get-StartupSentinelAction -State 'local_checks_failed' -EndedAt '2026-07-03T11:00:00' -LaunchTime $lt -LoopStatusExit 1 -LoopAlive $true
Write-Host "Result: '$a'"
