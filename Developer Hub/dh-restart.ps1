# Restart Developer Hub dev-gateway with a fresh AAD token from host az CLI.
# Usage: .\dh-restart.ps1            # restart just the dev-gateway
#        .\dh-restart.ps1 -All       # restart whole stack
[CmdletBinding()]
param([switch]$All)

$ErrorActionPreference = 'Stop'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$compose = Join-Path $PSScriptRoot 'docker-compose.yaml'
if (-not (Test-Path $compose)) { $compose = "C:\Users\alkorn\repos\Fabric_Developer_Hub\Developer Hub\docker-compose.yaml" }

# Make sure we're in the dev tenant
az account set --subscription "ME-MngEnvMCAP029796-alkorn-1" 2>$null

Write-Host "Acquiring Power BI access token..." -ForegroundColor Cyan
$token = az account get-access-token --scope https://analysis.windows.net/powerbi/api/.default --query accessToken -o tsv 2>$null
if (-not $token) {
    Write-Host "Failed to get token. Run 'az login --tenant fc3a8969-ed60-4daa-92df-1fdf4ff5bc15' first." -ForegroundColor Red
    exit 1
}
$env:DEVGATEWAY_TOKEN = $token.Trim()
Write-Host "Token acquired ($([math]::Round($env:DEVGATEWAY_TOKEN.Length / 1024, 1)) KB)" -ForegroundColor Green

Push-Location (Split-Path $compose -Parent)
try {
    if ($All) {
        & $docker compose -f $compose up -d --force-recreate
    } else {
        & $docker compose -f $compose up -d --force-recreate dev-gateway
    }
} finally {
    Pop-Location
    Remove-Item Env:\DEVGATEWAY_TOKEN -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 12
& $docker logs developer-hub-dev-gateway-1 --since 30s 2>&1 |
    Select-String -Pattern 'registered|ERROR|device-code' |
    Select-Object -Last 5
