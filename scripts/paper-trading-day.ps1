param(
    [string]$Preset = "canary-moomoo",
    [string]$MarketCloseLocal = "13:30"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Test-Port {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "============================================================"
Write-Host "SPY Moomoo Paper Trading Day"
Write-Host "Root:   $Root"
Write-Host "Preset: $Preset"
Write-Host "============================================================"

Write-Step "[1/6] Checking moomoo OpenD on 127.0.0.1:11111..."
$opendOk = Test-NetConnection 127.0.0.1 -Port 11111 -InformationLevel Quiet
if (-not $opendOk) {
    Write-Host ""
    Write-Host "Moomoo OpenD is not listening on 127.0.0.1:11111." -ForegroundColor Red
    Write-Host "Open moomoo/OpenD, log in, make sure paper trading is available, then run START_PAPER_TRADING_DAY.bat again."
    exit 10
}

Write-Step "[2/6] Starting backend, connecting moomoo SIMULATE, and arming scanner..."
try {
    & (Join-Path $Root "scripts\start-paper-system.ps1") -Preset $Preset
} catch {
    throw "start-paper-system.ps1 failed. Check logs\run-live.out and logs\run-live.err. $($_.Exception.Message)"
}

Write-Step "[3/6] Starting frontend if needed..."
if (-not (Test-Port 5173) -and (Test-Path (Join-Path $Root "frontend\package.json"))) {
    $frontendCmd = "npm run dev -- --host 127.0.0.1 *> ..\logs\frontend.out"
    Start-Process powershell `
        -WorkingDirectory (Join-Path $Root "frontend") `
        -WindowStyle Hidden `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCmd) | Out-Null
    Start-Sleep -Seconds 3
}
Write-Host "Opening dashboard: http://127.0.0.1:5173/"
Start-Process "http://127.0.0.1:5173/"

Write-Step "[4/6] Running premarket safety check..."
python scripts\premarket_check.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Premarket check found a blocker." -ForegroundColor Yellow
    $answer = Read-Host "Continue watchdog loop anyway? Type YES to continue"
    if ($answer -ne "YES") {
        Write-Host "Stopping. Fix the blocker, then run START_PAPER_TRADING_DAY.bat again."
        exit 2
    }
}

Write-Step "[5/6] Watchdog loop active until $MarketCloseLocal local time."
Write-Host "Keep this window open for the day."
Write-Host "Logs:"
Write-Host "  logs\watchdog.log"
Write-Host "  logs\eod_audit.log"

$parts = $MarketCloseLocal.Split(":", 2)
$close = (Get-Date).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
if ((Get-Date) -gt $close) {
    $close = (Get-Date).AddMinutes(1)
}

while ((Get-Date) -lt $close) {
    $stamp = Get-Date -Format s
    Add-Content -Path (Join-Path $Root "logs\watchdog.log") -Value "[$stamp] watchdog tick"
    python scripts\watchdog.py *>> logs\watchdog.log
    $remaining = [int][Math]::Max(1, ($close - (Get-Date)).TotalSeconds)
    Start-Sleep -Seconds ([Math]::Min(300, $remaining))
}

Write-Step "[6/6] Market-close audit..."
python scripts\eod_audit.py >> logs\eod_audit.log 2>&1
python scripts\eod_audit.py

Write-Host ""
Write-Host "Paper trading day script finished." -ForegroundColor Green
Write-Host "Leave backend running if you still want the dashboard."
