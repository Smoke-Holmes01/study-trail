param([switch]$SkipDatabase, [int]$ApiPort=8000, [int]$UiPort=5173)
$ErrorActionPreference = 'Stop'
$srcRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $srcRoot 'backend'
$localRoot = if ($env:RUNTIME_ROOT) { $env:RUNTIME_ROOT } else { Join-Path $srcRoot '.local/v004' }
$composeProject = if ($env:STUDY_TRAIL_COMPOSE_PROJECT) { $env:STUDY_TRAIL_COMPOSE_PROJECT } else { 'study-trail-v004' }
$isDailyRoot = [IO.Path]::GetFullPath($localRoot).TrimEnd('\','/') -eq [IO.Path]::GetFullPath((Join-Path $srcRoot '.local/v004')).TrimEnd('\','/')
if ($isDailyRoot -ne ($composeProject -eq 'study-trail-v004')) { throw 'Runtime and Compose project identities must both select daily or both select isolation' }
if ($composeProject -ne 'study-trail-v004') { $ApiPort=8010; $UiPort=5180 }
$env:STUDY_TRAIL_ENV_FILE = Join-Path $localRoot 'backend.env'
$env:VITE_API_TARGET = "http://127.0.0.1:$ApiPort"
[IO.Directory]::CreateDirectory($localRoot) | Out-Null
$env:PYTHONUTF8 = '1'
if (Test-Path (Join-Path $localRoot 'maintenance')) { throw '平台维护中，请先执行维护恢复检查' }
$pidFile = Join-Path $localRoot 'processes.json'
if (Test-Path -LiteralPath $pidFile) {
    $decoded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $entries = @($decoded | Where-Object { $_ -and $_.id })
    $running = @($entries | Where-Object { $proc = Get-Process -Id $_.id -ErrorAction SilentlyContinue; $proc -and $proc.Path -eq $_.path -and ($null -eq $_.started_at -or $proc.StartTime.ToUniversalTime().ToString('o') -eq ([datetime]$_.started_at).ToUniversalTime().ToString('o')) })
    if ($running.Count -eq 3) { Write-Output "学迹已在运行：http://127.0.0.1:$UiPort"; exit 0 }
    if ($running.Count -gt 0) { & (Join-Path $PSScriptRoot 'stop.ps1') }
}
$dockerBin = 'C:\Program Files\Docker\Docker\resources\bin'
if (Test-Path -LiteralPath $dockerBin) { $env:PATH = $dockerBin + ';' + $env:PATH }
if (-not $SkipDatabase) {
    Push-Location (Join-Path $srcRoot 'infra')
    try { docker compose -p $composeProject -f (Join-Path $srcRoot 'infra/compose.yaml') --env-file (Join-Path $localRoot 'infra.env') up -d --wait; if ($LASTEXITCODE) { throw '数据库启动失败' } } finally { Pop-Location }
}
$virtualPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
$python = & $virtualPython -c 'import sys,os; print(os.path.realpath(sys._base_executable))'
if ($LASTEXITCODE) { throw 'Project Python runtime unavailable' }
$env:PYTHONPATH = Join-Path $backendRoot '.venv/Lib/site-packages'
if (-not (Test-Path -LiteralPath $python)) { throw '请先在 src/backend 运行 uv sync --python 3.12' }
Push-Location $backendRoot
try { & $python tools/check_installation.py; if ($LASTEXITCODE) { throw '请先完成数据库升级与平台初始化' } } finally { Pop-Location }
if (Get-NetTCPConnection -State Listen -LocalPort $ApiPort,$UiPort -ErrorAction SilentlyContinue) { throw 'Application ports are occupied by unrecorded processes; stop them before starting' }
$processes = @()
foreach ($entry in @(@{Name='api';Args=@('tools/project_python.py','-m','uvicorn','study_trail.api:app','--host','127.0.0.1','--port',([string]$ApiPort))},@{Name='worker';Args=@('tools/project_python.py','-m','study_trail.execution')})) {
    $p = Start-Process -FilePath $python -ArgumentList $entry.Args -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot ($entry.Name + '.log')) -RedirectStandardError (Join-Path $localRoot ($entry.Name + '.error.log'))
    $processes += @{id=$p.Id;name=$entry.Name;path=$python;started_at=$p.StartTime.ToUniversalTime().ToString('o')}
}
$node = (Get-Command node.exe).Source
$vite = Join-Path $srcRoot 'frontend\node_modules\vite\bin\vite.js'
$p = Start-Process -FilePath $node -ArgumentList @($vite,'--host','127.0.0.1','--port',([string]$UiPort),'--strictPort') -WorkingDirectory (Join-Path $srcRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot 'frontend.log') -RedirectStandardError (Join-Path $localRoot 'frontend.error.log')
$processes += @{id=$p.Id;name='frontend';path=$node;started_at=$p.StartTime.ToUniversalTime().ToString('o')}
$processes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localRoot 'processes.json') -Encoding utf8
Write-Output "学迹启动： http://127.0.0.1:$UiPort ；日志位于 $localRoot。"
