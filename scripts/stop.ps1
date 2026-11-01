param([switch]$Database)
$ErrorActionPreference = 'Stop'
$srcRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $srcRoot 'backend'
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
    docker compose -p study-trail-v004 -f (Join-Path $srcRoot 'infra/compose.yaml') --env-file (Join-Path $srcRoot '.local/v004/infra.env') stop
    if ($LASTEXITCODE) { throw 'Database stop failed' }
}
Write-Output 'Managed application processes stopped; data volumes and private files retained.'
