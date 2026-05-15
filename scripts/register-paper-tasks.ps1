$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

$days = "MON,TUE,WED,THU,FRI"

function Register-Task {
    param(
        [string]$Name,
        [string]$Schedule,
        [string]$Time,
        [string]$Command,
        [string]$Modifier = ""
    )
    $task = "cmd.exe /c cd /d `"$Root`" && $Command"
    $args = @("/Create", "/F", "/TN", $Name, "/TR", $task, "/SC", $Schedule)
    if ($Time) {
        $args += @("/ST", $Time)
    }
    if ($Schedule -eq "WEEKLY") {
        $args += @("/D", $days)
    }
    if ($Modifier) {
        $args += @("/MO", $Modifier)
    }
    schtasks @args | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled task: $Name"
    }
}

# Local machine is expected to be Pacific time. These wall-clock times map to:
# 06:15 PT = 09:15 ET pre-market check; 13:30 PT = 16:30 ET EOD audit.
Register-Task -Name "SPY Paper Premarket Check" -Schedule "WEEKLY" -Time "06:15" -Command "python scripts\premarket_check.py >> logs\premarket_check.log 2>&1"
Register-Task -Name "SPY Paper EOD Audit" -Schedule "WEEKLY" -Time "13:30" -Command "python scripts\eod_audit.py >> logs\eod_audit.log 2>&1"
Register-Task -Name "SPY Paper Watchdog" -Schedule "MINUTE" -Time "" -Modifier "5" -Command "python scripts\watchdog.py >> logs\watchdog.log 2>&1"

Write-Host "Registered Windows scheduled tasks for automated paper trading checks."
