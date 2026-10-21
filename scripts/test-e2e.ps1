param([switch]$KeepRunning, [string]$Spec)
$ErrorActionPreference = 'Stop'
$srcRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $srcRoot 'backend'
$localRoot = Join-Path $srcRoot '.local'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
Push-Location $backendRoot
try { & $python tools/prepare_e2e.py; if ($LASTEXITCODE) { throw 'E2E preparation failed' } } finally { Pop-Location }
$e2eConfig = Get-Content -LiteralPath (Join-Path $localRoot 'e2e-environment.json') -Raw | ConvertFrom-Json
foreach ($property in $e2eConfig.PSObject.Properties) { [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process') }
$env:VITE_API_TARGET = 'http://127.0.0.1:8001'
$env:E2E_BASE_URL = 'http://127.0.0.1:5174'
$entries = @(@{Name='e2e-api';Args=@('-m','uvicorn','tools.e2e_app:app','--host','127.0.0.1','--port','8001')}, @{Name='e2e-worker';Args=@('tools/e2e_app.py')})
$started = @()
try {
    foreach ($entry in $entries) {
        $process = Start-Process -FilePath $python -ArgumentList $entry.Args -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot ($entry.Name+'.log')) -RedirectStandardError (Join-Path $localRoot ($entry.Name+'.error.log'))
        $started += @{id=$process.Id;path=$python;started_at=$process.StartTime.ToUniversalTime().ToString('o')}
    }
    $node = (Get-Command node.exe).Source
    $vite = Join-Path $srcRoot 'frontend/node_modules/vite/bin/vite.js'
    $process = Start-Process -FilePath $node -ArgumentList @($vite,'--host','127.0.0.1','--port','5174','--strictPort') -WorkingDirectory (Join-Path $srcRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot 'e2e-frontend.log') -RedirectStandardError (Join-Path $localRoot 'e2e-frontend.error.log')
    $started += @{id=$process.Id;path=$node;started_at=$process.StartTime.ToUniversalTime().ToString('o')}
    $started | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localRoot 'e2e-processes.json') -Encoding utf8
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $null=Invoke-WebRequest -Uri 'http://127.0.0.1:8001/openapi.json' -TimeoutSec 2; $null=Invoke-WebRequest -Uri $env:E2E_BASE_URL -TimeoutSec 2; $ready=$true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Isolated browser-test services did not become ready' }
    Push-Location (Join-Path $srcRoot 'frontend')
    try { if ($Spec) { & npx playwright test $Spec --timeout=45000 } else { & npm run test:e2e }; if ($LASTEXITCODE) { throw 'Browser regression failed' } } finally { Pop-Location }
} finally {
    if (-not $KeepRunning) {
        foreach ($entry in $started) {
            $process=Get-Process -Id $entry.id -ErrorAction SilentlyContinue
            if ($process -and $process.Path -eq $entry.path -and $process.StartTime.ToUniversalTime().ToString('o') -eq $entry.started_at) { Stop-Process -Id $entry.id }
        }
    }
}
