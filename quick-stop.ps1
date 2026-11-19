$ErrorActionPreference = 'Stop'
$serverPath = Join-Path $PSScriptRoot 'rag_roles\server.py'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

try {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'")
    $targets = @($processes | Where-Object {
        $command = $_.CommandLine -replace '/', '\'
        $absoluteScript = $command -match ('(?:^|[\s"])' + [regex]::Escape($serverPath) + '(?:[\s"]|$)')
        $relativeScript = $command -match '(?:^|[\s"])(?:\.\\)?rag_roles\\server\.py(?:[\s"]|$)'
        $parent = $processes | Where-Object ProcessId -EQ $_.ParentProcessId
        $projectPython = $_.ExecutablePath -eq $pythonPath -or $parent.ExecutablePath -contains $pythonPath
        $absoluteScript -or ($relativeScript -and $projectPython)
    })
    if (-not $targets.Count) {
        Write-Host 'RAG is already stopped.'
        exit 0
    }
    # Match the project script, not every Python process or arbitrary port owner.
    foreach ($target in ($targets | Sort-Object ProcessId -Descending)) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($target.ProcessId)"
        if ($current -and $current.CreationDate -eq $target.CreationDate) {
            Stop-Process -Id $target.ProcessId -ErrorAction Stop
        }
    }
    Write-Host 'RAG stopped. Use START.cmd to start it again.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
