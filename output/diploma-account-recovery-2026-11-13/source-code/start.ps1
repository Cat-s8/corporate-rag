$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'rag_roles\server.py'
$ready = $false
try {
    $sessionInfo = Invoke-RestMethod 'http://127.0.0.1:8502/api/session' -TimeoutSec 2
    $ready = [bool]$sessionInfo.csrf
} catch { }
Write-Host 'RAG: http://127.0.0.1:8502'
if ($ready) {
    Write-Host 'The application is already running. Open the address above.'
    return
}
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $pythonPath -B $serverPath
if ($LASTEXITCODE -ne 0) { throw 'RAG server failed. See the error above.' }
