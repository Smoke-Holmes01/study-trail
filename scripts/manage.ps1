param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('install','backup','restore-verify','resume','check','cleanup','rebuild')]
    [string]$Command,
    [string]$AdminLogin,
    [string]$Backup,
    [string]$FileId,
    [switch]$All,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$srcRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $srcRoot 'backend'
$virtualPython = Join-Path $backendRoot '.venv/Scripts/python.exe'
# Run Docker/process orchestration directly from the matching base interpreter;
# keep the project's installed packages without depending on a venv redirector.
$basePython = & $virtualPython -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -or -not (Test-Path -LiteralPath $basePython)) { throw 'Project Python runtime unavailable' }
$previousPath = $env:PYTHONPATH
$previousUtf8 = $env:PYTHONUTF8
$env:PYTHONPATH = Join-Path $backendRoot '.venv/Lib/site-packages'
$env:PYTHONUTF8 = '1'
$arguments = @()
if ($Command -eq 'install') {
    if (-not $AdminLogin) { throw 'install requires -AdminLogin' }
    $arguments = @('tools/setup_local.py','--admin-login',$AdminLogin)
} elseif ($Command -in @('backup','restore-verify','resume')) {
    $arguments = @('tools/storage_maintenance.py',$Command)
    if ($Command -eq 'restore-verify') {
        if (-not $Backup) { throw 'restore-verify requires -Backup' }
        $arguments += @('--backup',$Backup)
    }
} else {
    $arguments = @('tools/vector_index.py',$Command)
    if ($FileId) { $arguments += @('--file-id',$FileId) }
    if ($All) { $arguments += '--all' }
    if ($Apply) { $arguments += '--apply' }
}
Push-Location $backendRoot
try {
    & $basePython tools/project_python.py @arguments
    if ($LASTEXITCODE) { throw 'Maintenance command failed; inspect output and retain maintenance state' }
} finally {
    Pop-Location
    $env:PYTHONPATH = $previousPath
    $env:PYTHONUTF8 = $previousUtf8
}
