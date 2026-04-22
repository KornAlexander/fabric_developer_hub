<#
.SYNOPSIS
    PowerShell launcher for the Developer Hub Docker Compose stack.

.DESCRIPTION
    Mirrors start.sh for Windows users who don't want to use WSL or Git Bash.
    Acquires a Power BI / Fabric token from the host Azure CLI and passes it
    to the dev-gateway container so device-code login is skipped.

.PARAMETER Mode
    'prod' (default) launches nginx + static frontend bundle.
    'dev'  launches webpack-dev-server with HMR (requires Node 20+ on host).

.EXAMPLE
    ./start.ps1
    Launches all services in prod mode.

.EXAMPLE
    ./start.ps1 dev
    Launches with frontend HMR.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('prod', 'dev')]
    [string]$Mode = 'prod',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ── Pre-flight: pick docker binary (prefer Docker Desktop) ──
$script:Docker = $null
$candidates = @(
    'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
    'C:\Program Files\Rancher Desktop\resources\resources\win32\bin\docker.exe'
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $script:Docker = $c; break }
}
if (-not $script:Docker) {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { $script:Docker = $cmd.Source }
}
if (-not $script:Docker) {
    Write-Host "❌ Docker not found. Install Docker Desktop and start it." -ForegroundColor Red
    exit 1
}
Write-Host "🐳 Using docker: $script:Docker" -ForegroundColor DarkGray
# Prepend Docker Desktop's bin so credential helpers (docker-credential-desktop.exe)
# resolve correctly when Rancher Desktop is also installed.
$dockerBin = Split-Path $script:Docker -Parent
if ($env:Path -notlike "$dockerBin*") { $env:Path = "$dockerBin;$env:Path" }
& $script:Docker info --format '{{.ServerVersion}}' 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker daemon not reachable. Start Docker Desktop and wait for the whale icon to settle." -ForegroundColor Red
    exit 1
}

# ── Acquire a Fabric token from the host Azure CLI ──────────
Write-Host "🔑 Acquiring Power BI token from host Azure CLI..." -ForegroundColor Cyan
$env:DEVGATEWAY_TOKEN = $null
if (Get-Command az -ErrorAction SilentlyContinue) {
    try {
        $token = az account get-access-token `
            --scope https://analysis.windows.net/powerbi/api/.default `
            --query accessToken -o tsv 2>$null
        if ($LASTEXITCODE -eq 0 -and $token) { $env:DEVGATEWAY_TOKEN = $token.Trim() }
    } catch { }
}

if ($env:DEVGATEWAY_TOKEN) {
    Write-Host "✅ Token acquired — device-code login will be skipped" -ForegroundColor Green
} else {
    Write-Host "⚠️  Could not get token from host (az CLI missing or not logged in)" -ForegroundColor Yellow
    Write-Host "   Falling back to device-code login inside the container"
}

# ── Clean up previous run ───────────────────────────────────
# Bash uses `|| true` to ignore errors here; PS needs explicit handling
# because $ErrorActionPreference='Stop' turns native-command stderr into errors.
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& $script:Docker compose --profile prod --profile dev down *>$null
& $script:Docker volume rm developerhub_manifest-pkg *>$null
$ErrorActionPreference = $prev
$global:LASTEXITCODE = 0  # ignore "no such volume" exit code

# ── Frontend dev-mode host-install guard ────────────────────
if ($Mode -eq 'dev') {
    $env:COMPOSE_PROFILES = 'dev'
    $needInstall = -not (Test-Path 'Frontend/node_modules') -or
        ((Get-Item 'Frontend/package-lock.json').LastWriteTime -gt
         (Get-Item 'Frontend/node_modules').LastWriteTime)
    if ($needInstall) {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            Write-Host "❌ Dev mode requires Node/npm on the host. Install Node 20+ or use prod mode." -ForegroundColor Red
            exit 1
        }
        Write-Host "📦 Installing frontend deps on host (dev mode)..." -ForegroundColor Cyan
        Push-Location Frontend
        try { npm ci --no-audit --no-fund }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host "🔧 Launching in DEV mode (webpack-dev-server + HMR)" -ForegroundColor Cyan
} else {
    $env:COMPOSE_PROFILES = 'prod'
    Write-Host "🚀 Launching in PROD mode (nginx, static bundle, no host Node required)" -ForegroundColor Cyan
    Write-Host "   (use './start.ps1 dev' for HMR during active development)"
}

# ── Launch all services ─────────────────────────────────────
$composeArgs = @('compose', 'up', '--build', '--abort-on-container-failure') + $ExtraArgs
& $script:Docker @composeArgs
exit $LASTEXITCODE
