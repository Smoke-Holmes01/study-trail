param([switch]$Database)
$ErrorActionPreference = 'Stop'
$srcRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $srcRoot 'backend'
$localRoot = if ($env:RUNTIME_ROOT) { $env:RUNTIME_ROOT } else { Join-Path $srcRoot '.local/v004' }
$composeProject = if ($env:STUDY_TRAIL_COMPOSE_PROJECT) { $env:STUDY_TRAIL_COMPOSE_PROJECT } else { 'study-trail-v004' }
$isDailyRoot = [IO.Path]::GetFullPath($localRoot).TrimEnd('\','/') -eq [IO.Path]::GetFullPath((Join-Path $srcRoot '.local/v004')).TrimEnd('\','/')
if ($isDailyRoot -ne ($composeProject -eq 'study-trail-v004')) { throw 'Runtime and Compose project identities must both select daily or both select isolation' }
$env:STUDY_TRAIL_ENV_FILE = Join-Path $localRoot 'backend.env'
$virtualPython = Join-Path $backendRoot '.venv/Scripts/python.exe'
$python = & $virtualPython -c 'import sys,os; print(os.path.realpath(sys._base_executable))'
if ($LASTEXITCODE) { throw 'Project Python runtime unavailable' }
$previousPath=$env:PYTHONPATH
$env:PYTHONPATH=Join-Path $backendRoot '.venv/Lib/site-packages'
Push-Location $backendRoot
try {
    & $python tools/project_python.py tools/manage_runtime.py stop
    if ($LASTEXITCODE) { throw 'Application stop failed; do not assume writes have stopped' }
} finally { Pop-Location; $env:PYTHONPATH=$previousPath }
if ($Database) {
    $env:PATH='C:\Program Files\Docker\Docker\resources\bin;' + $env:PATH
    docker compose -p $composeProject -f (Join-Path $srcRoot 'infra/compose.yaml') --env-file (Join-Path $localRoot 'infra.env') stop
    if ($LASTEXITCODE) { throw 'Database stop failed' }
}
Write-Output 'Managed application processes stopped; data volumes and private files retained.'
