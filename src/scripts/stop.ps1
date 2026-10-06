param([switch]$Database)
$ErrorActionPreference='Stop'
$srcRoot=Split-Path $PSScriptRoot -Parent
$pidFile=Join-Path $srcRoot '.local\processes.json'
function Stop-AppProcessTree([int]$processId) {
    $parent = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $parent) { return }
    $created = $parent.StartTime.ToUniversalTime()
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" | Where-Object { $_.CreationDate.ToUniversalTime() -ge $created })
    foreach ($child in $children) { Stop-AppProcessTree -processId $child.ProcessId }
    Stop-Process -Id $processId -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $pidFile) {
    foreach ($entry in (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json)) {
        $p=Get-Process -Id $entry.id -ErrorAction SilentlyContinue
        if ($p -and $p.Path -eq $entry.path -and ($null -eq $entry.started_at -or $p.StartTime.ToUniversalTime().ToString('o') -eq ([datetime]$entry.started_at).ToUniversalTime().ToString('o'))) { Stop-AppProcessTree -processId $entry.id }
    }
    Remove-Item -LiteralPath $pidFile
}
if ($Database) {
    $env:PATH='C:\Program Files\Docker\Docker\resources\bin;' + $env:PATH
    Push-Location (Join-Path $srcRoot 'infra')
    try { docker compose stop } finally { Pop-Location }
}
Write-Output '已停止记录的学迹进程。数据库卷与业务文件保留。'
