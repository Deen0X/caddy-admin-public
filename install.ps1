<#
.SYNOPSIS
  Instalador de DNX Caddy Admin (Caddy + consola) para Windows con Docker Desktop.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Domain micasa.duckdns.org -Email tu@email.com
  .\install.ps1 -Local C:\repos\caddy-admin
#>
[CmdletBinding()]
param(
    [string]$Domain = "",
    [string]$Email = "",
    [string]$LanIp = "",
    [string]$Dir = "$env:USERPROFILE\dnx-caddy-admin",
    [string]$Local = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:DNX_REPO_URL) { $env:DNX_REPO_URL } else { "https://github.com/Deen0X/caddy-admin-public/archive/refs/heads/master.zip" }

function Fail($message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

# --- Requisitos
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker no está instalado." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop no está en marcha." }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "Falta Docker Compose v2." }

# --- IP LAN
if ([string]::IsNullOrWhiteSpace($LanIp)) {
    try {
        $candidate = Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object {
                $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))\.' -and
                $_.InterfaceAlias -notmatch 'Loopback'
            } |
            Select-Object -First 1
        if ($candidate) { $LanIp = $candidate.IPAddress }
    } catch { }
}

Write-Host "Instalando en: $Dir"
New-Item -ItemType Directory -Path $Dir -Force | Out-Null

$fresh = -not (Test-Path (Join-Path $Dir ".env"))
$userEnv = $null
if ((-not $fresh) -and (-not $Force) -and (Test-Path (Join-Path $Dir ".env"))) {
    $userEnv = Get-Content -Path (Join-Path $Dir ".env") -Raw
}
$userCaddy = $null
if ((-not $fresh) -and (-not $Force) -and (Test-Path (Join-Path $Dir "caddy"))) {
    $userCaddy = Join-Path $env:TEMP ("dnx-caddy-cfg-" + [guid]::NewGuid().ToString("N"))
    Copy-Item -Path (Join-Path $Dir "caddy") -Destination $userCaddy -Recurse -Force
}

# --- Código
if (-not [string]::IsNullOrWhiteSpace($Local)) {
    Write-Host "Copiando desde $Local"
    Copy-Item -Path (Join-Path $Local '*') -Destination $Dir -Recurse -Force
} else {
    $tmp = Join-Path $env:TEMP ("dnx-caddy-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $zip = Join-Path $tmp "repo.zip"
    Write-Host "Descargando $RepoUrl"
    Invoke-WebRequest -Uri $RepoUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem -Path $tmp -Directory -Filter "caddy-admin-*" | Select-Object -First 1
    if (-not $src) { Fail "No se pudo extraer el repositorio." }
    Copy-Item -Path (Join-Path $src.FullName '*') -Destination $Dir -Recurse -Force
    Remove-Item -Recurse -Force $tmp
}

# Quitar metadatos de desarrollo y datos que no deben quedar en la instalación
foreach ($item in @(".git", ".antigravity", ".opencode", ".agent", "compose", "resources", "tools")) {
    Remove-Item -Recurse -Force (Join-Path $Dir $item) -ErrorAction SilentlyContinue
}
Get-ChildItem -Path (Join-Path $Dir "webadmin") -Directory -Filter "prod_backup_*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if ($userEnv) {
    Set-Content -Path (Join-Path $Dir ".env") -Value $userEnv -Encoding ASCII
}

Set-Location $Dir

if ($userCaddy) {
    Remove-Item -Recurse -Force (Join-Path $Dir "caddy") -ErrorAction SilentlyContinue
    Copy-Item -Path $userCaddy -Destination (Join-Path $Dir "caddy") -Recurse -Force
    Remove-Item -Recurse -Force $userCaddy -ErrorAction SilentlyContinue
}

# Instalación nueva: nunca reutilizar Caddyfile/rutas de ejemplo del repo.
if ($fresh -or $Force) {
    Remove-Item (Join-Path $Dir "caddy\Caddyfile") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Dir "caddy\routes.json") -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path (Join-Path $Dir "caddy\routes") -Filter "*.caddy" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    '{ "routes": [] }' | Set-Content -Path (Join-Path $Dir "caddy\routes.json") -Encoding UTF8
}

if ((Test-Path "docker-compose.standalone.yml") -and (-not (Test-Path "compose.yml") -or $Force)) {
    Copy-Item "docker-compose.standalone.yml" "compose.yml" -Force
}
if (-not (Test-Path "compose.yml")) { Fail "No se encontró docker-compose.standalone.yml." }

# --- .env
$envPath = Join-Path $Dir ".env"
if ((-not (Test-Path $envPath)) -or $Force) {
    @(
        "BASE_DOMAIN=$Domain",
        "ACME_EMAIL=$Email",
        "LAN_IP=$LanIp",
        "DNX_UID=1000",
        "DNX_GID=1000"
    ) | Set-Content -Path $envPath -Encoding ASCII
}

# --- Estructura
New-Item -ItemType Directory -Path (Join-Path $Dir "caddy\routes\paths") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Dir "caddy\backups") -Force | Out-Null
if (-not (Test-Path (Join-Path $Dir "caddy\routes.json"))) {
    '{ "routes": [] }' | Set-Content -Path (Join-Path $Dir "caddy\routes.json") -Encoding UTF8
}

# --- Caddyfile mínimo (panel por IP LAN, TLS interno)
$caddyfile = Join-Path $Dir "caddy\Caddyfile"
if ((-not (Test-Path $caddyfile)) -or $Force) {
    $panelHost = if ([string]::IsNullOrWhiteSpace($LanIp)) { "localhost" } else { $LanIp }
    $content = @"
{
	metrics
	servers {
		protocols h1 h2
	}
	admin :2019 {
		origins http://caddy:2019 http://127.0.0.1:2019
	}
}

(caddy_admin_ui) {
	encode zstd gzip
	handle {
		reverse_proxy caddy-admin:9095
	}
	log {
		output stdout
		format console
	}
}

https://$panelHost`:9095 {
	tls internal
	import caddy_admin_ui
}
"@
    Set-Content -Path $caddyfile -Value $content -Encoding ASCII
}

Write-Host "Construyendo y arrancando..."
docker compose -f compose.yml up -d --build

Write-Host "Esperando a que la consola esté sana..."
for ($i = 0; $i -lt 60; $i++) {
    $status = (docker inspect --format '{{.State.Health.Status}}' dnx-caddy-admin 2>$null)
    if ($status -eq "healthy") { break }
    Start-Sleep -Seconds 2
}

$panelHost = if ([string]::IsNullOrWhiteSpace($LanIp)) { "localhost" } else { $LanIp }
Write-Host ""
Write-Host "============================================================"
Write-Host " DNX Caddy Admin instalado"
Write-Host " Panel: https://$panelHost`:9095"
Write-Host "   1. Acepta el aviso del certificado (TLS interno)."
Write-Host "   2. Crea el administrador."
Write-Host "   3. Ajustes -> Dominio: pon tu dominio y pulsa Aplicar."
Write-Host ""
Write-Host " Directorio: $Dir"
Write-Host " Logs:      docker compose -f $Dir\compose.yml logs -f caddy-admin"
Write-Host " Parar:     docker compose -f $Dir\compose.yml down"
Write-Host "============================================================"
