$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'rag_roles\server.py'
$dataPath = Join-Path $PSScriptRoot 'rag_roles\data'
$tailscaleCommand = Get-Command 'tailscale' -ErrorAction SilentlyContinue

if (-not $tailscaleCommand) {
    throw 'Tailscale is not installed. Install it on this computer and the laptop, then sign in on both devices.'
}

$ready = $false
try {
    $sessionInfo = Invoke-RestMethod 'http://127.0.0.1:8502/api/session' -TimeoutSec 2
    $ready = [bool]$sessionInfo.csrf
} catch { }

if ($ready) {
    throw 'The RAG application is already running. Stop its window with Ctrl+C, then run start-remote.ps1 again so Secure session cookies can be enabled.'
}

# The browser connects through HTTPS; Waitress remains private on localhost.
$env:RAG_HTTPS = '1'
$env:RAG_TRUST_LOCAL_PROXY = '1'
$serverProcess = $null
try {
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-B', $serverPath) `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $dataPath 'server.out.log') `
        -RedirectStandardError (Join-Path $dataPath 'server.err.log')

    $started = $false
    for ($attempt = 0; $attempt -lt 240; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "RAG server stopped with exit code $($serverProcess.ExitCode). See rag_roles\data\server.err.log."
        }
        try {
            $sessionInfo = Invoke-RestMethod 'http://127.0.0.1:8502/api/session' -TimeoutSec 1
            if ($sessionInfo.csrf) { $started = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $started) { throw 'RAG server did not become ready after model warmup. See rag_roles\data\server.err.log.' }

    Write-Host 'Remote access is active only while this window remains open.'
    Write-Host 'Open the HTTPS address printed below on the Tailscale-connected laptop.'
    & $tailscaleCommand.Source serve 8502
    if ($LASTEXITCODE -ne 0) {
        throw 'Tailscale Serve failed. Check that Tailscale is connected; if access is denied, run PowerShell as Administrator.'
    }
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id
    }
}
