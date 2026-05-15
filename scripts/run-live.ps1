param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UvicornArgs
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Import-EnvFile {
    param(
        [string]$Path,
        [bool]$Override = $false
    )
    if (-not (Test-Path $Path)) {
        return
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($Override -or -not [Environment]::GetEnvironmentVariable($key, "Process")) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

Import-EnvFile -Path (Join-Path $Root "config\.env") -Override $false
Import-EnvFile -Path (Join-Path $Root ".env.live") -Override $true

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONPATH = $Root

python -m uvicorn main:app --host 127.0.0.1 --port 8000 @UvicornArgs
