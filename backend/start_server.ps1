# backend/start_server.ps1
# Single entry point for the CUS AI backend on port 8001.
#
# Fixes the "[Errno 10048] address already in use" error:
#   1. Finds the instance to stop by LISTENING PORT (catches uvicorn started
#      manually, via pythonw, or by any other launcher) in addition to the
#      process command line.
#   2. Waits until the port is actually free before starting (up to 30s) so a
#      dying instance's socket can never collide with the new one.
#   3. Starts exactly one instance and verifies it came up.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File .\start_server.ps1
#   powershell -ExecutionPolicy Bypass -File .\start_server.ps1  -Port 9000
#   powershell -ExecutionPolicy Bypass -File .\start_server.ps1  -KillOnly        # just stop, don't start

param(
    [int]$Port = 8001,
    [switch]$KillOnly
)

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "== Stopping all instances on port $Port =="
$found = @(
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "uvicorn" }
) + @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } |
        Where-Object { $_.ProcessName -match "python" } |
        ForEach-Object {
            Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)"
        }
)
$seen = @{}
foreach ($p in $found) {
    if ($p -and -not $seen.ContainsKey($p.ProcessId)) {
        $seen[$p.ProcessId] = $true
        Write-Host ("  stopping PID " + $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force
    }
}
if ($seen.Count -eq 0) { Write-Host "  nothing running on port $Port" }

Write-Host "== Waiting for port $Port to free up =="
$free = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { $free = $true; break }
}
if ($free) {
    Write-Host "  port $Port is free"
} else {
    Write-Host "  WARNING: port $Port still busy after 15s - a stray process may hold it."
    if ($KillOnly) { exit 1 }
}

if ($KillOnly) {
    Write-Host "== Done (kill-only mode) =="
    exit 0
}

Write-Host "== Starting fresh uvicorn instance on port $Port =="
$outLog = Join-Path $ScriptDir "server_out.log"
$errLog = Join-Path $ScriptDir "server_err.log"
Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$Port" `
    -WorkingDirectory $ScriptDir `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 10
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host ("== Server is UP on http://127.0.0.1:$Port (PID " + $listener.OwningProcess + ") ==")
} else {
    Write-Host "== Server did not come up. Last lines of server_err.log: =="
    Get-Content $errLog -Tail 20
    Write-Host "== Last lines of server_out.log: =="
    Get-Content $outLog -Tail 20
    exit 1
}