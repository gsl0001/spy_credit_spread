param(
    [string]$Preset = "canary-moomoo"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Get-EnvFileValue {
    param(
        [string]$Key,
        [string]$Default = ""
    )
    foreach ($path in @((Join-Path $Root "config\.env"), (Join-Path $Root ".env.live"))) {
        if (-not (Test-Path $path)) {
            continue
        }
        foreach ($line in Get-Content $path) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
                continue
            }
            $parts = $trimmed.Split("=", 2)
            if ($parts[0].Trim() -eq $Key) {
                return $parts[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $Default
}

$existing = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq "Listen" } |
    Select-Object -First 1

if ($existing) {
    Write-Host "Backend already listening on port 8000 (PID $($existing.OwningProcess))."
} else {
    $logPath = Join-Path $Root "logs\run-live.out"
    $errPath = Join-Path $Root "logs\run-live.err"
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $Root "scripts\run-live.ps1")
    )
    $proc = Start-Process powershell -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $errPath
    Write-Host "Started backend on port 8000 (PID $($proc.Id)). Logs: $logPath"
}

Start-Sleep -Seconds 3

try {
    $hostName = Get-EnvFileValue -Key "FUTU_HOST" -Default "127.0.0.1"
    $port = [int](Get-EnvFileValue -Key "FUTU_PORT" -Default "11111")
    $connectBody = @{
        host = $hostName
        port = $port
        trd_env = 0
        security_firm = "NONE"
        filter_trdmarket = "NONE"
    } | ConvertTo-Json -Compress
    $connectResp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/moomoo/connect" -Body $connectBody -ContentType "application/json" -TimeoutSec 20
    if ($connectResp.connected) {
        Write-Host "Moomoo connected in SIMULATE mode."
    } else {
        Write-Host "Moomoo paper connect did not complete: $($connectResp.error)"
    }
} catch {
    Write-Host "Moomoo paper connect skipped/failed: $($_.Exception.Message)"
}

try {
    $body = @{ name = $Preset } | ConvertTo-Json -Compress
    $resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/scanner/preset/start" -Body $body -ContentType "application/json" -TimeoutSec 10
    if ($resp.started) {
        Write-Host "Scanner armed with preset: $($resp.preset.name)"
    } else {
        Write-Host "Scanner start response:"
        $resp | ConvertTo-Json -Depth 6
    }
} catch {
    Write-Host "Backend not ready yet or scanner start failed: $($_.Exception.Message)"
    Write-Host "Run this after startup finishes: Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/scanner/preset/start -Body '{`"name`":`"$Preset`"}' -ContentType 'application/json'"
}
