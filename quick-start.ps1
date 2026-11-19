$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$url = 'http://127.0.0.1:8502'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'rag_roles\server.py'
$dataPath = Join-Path $PSScriptRoot 'rag_roles\data'
$launchLock = New-Object System.Threading.Mutex($false, 'Local\CorporateRagQuickStart8502')
$lockTaken = $false

function Test-RagReady {
    try {
        $info = Invoke-RestMethod "$url/api/session" -TimeoutSec 2
        return [bool]$info.csrf
    } catch { return $false }
}

try {
    try { $lockTaken = $launchLock.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) {
        Write-Host 'RAG is already starting. Please wait for the browser.'
        exit 0
    }
    if (-not (Test-RagReady)) {
        if (-not (Test-Path -LiteralPath $pythonPath)) {
            throw 'Python environment is missing: .venv\Scripts\python.exe'
        }
        New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
        Write-Host 'Starting RAG and warming up models. Please wait...'
        $serverProcess = Start-Process -FilePath $pythonPath `
            -ArgumentList @('-B', ('"' + $serverPath + '"')) `
            -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $dataPath 'server.out.log') `
            -RedirectStandardError (Join-Path $dataPath 'server.err.log')
        $deadline = (Get-Date).AddMinutes(2)
        do {
            if ($serverProcess.HasExited) {
                throw 'Server stopped. See rag_roles\data\server.err.log'
            }
            if (Test-RagReady) { break }
            if ((Get-Date) -ge $deadline) {
                throw 'Startup is taking too long. See rag_roles\data\server.err.log before trying again.'
            }
            Start-Sleep -Milliseconds 500
        } while ($true)
    }
    Start-Process $url
    Write-Host 'RAG is running in the background. You may close this window.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($lockTaken) { $launchLock.ReleaseMutex() }
    $launchLock.Dispose()
}
