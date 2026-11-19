# Bootstrap de DNX Caddy Admin (Windows / Docker Desktop).
# Uso:
#   irm https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.ps1 | iex
#   $env:DNX_DOMAIN="micasa.duckdns.org"; irm ... | iex
#
# Para pasar parámetros sin variables de entorno:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.ps1))) -Domain micasa.duckdns.org
[CmdletBinding()]
param(
    [string]$Domain = $env:DNX_DOMAIN,
    [string]$Email = $env:DNX_EMAIL,
    [string]$Dir = $env:DNX_DIR,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:DNX_REPO_URL) { $env:DNX_REPO_URL } else { "https://github.com/Deen0X/caddy-admin-public/archive/refs/heads/master.zip" }

function Fail($message) {
    throw $message
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker no está instalado. Instala Docker Desktop y vuelve a ejecutar."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop no está en marcha." }

$tmp = Join-Path $env:TEMP ("dnx-caddy-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    $zip = Join-Path $tmp "repo.zip"
    Write-Host "Descargando DNX Caddy Admin..."
    Invoke-WebRequest -Uri $RepoUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $tmp -Force

    $src = Get-ChildItem -Path $tmp -Directory -Filter "caddy-admin-*" | Select-Object -First 1
    if (-not $src) { Fail "No se pudo extraer el repositorio." }

    $installer = Join-Path $src.FullName "install.ps1"
    $installArgs = @("-Local", $src.FullName)
    if (-not [string]::IsNullOrWhiteSpace($Domain)) { $installArgs += @("-Domain", $Domain) }
    if (-not [string]::IsNullOrWhiteSpace($Email)) { $installArgs += @("-Email", $Email) }
    if (-not [string]::IsNullOrWhiteSpace($Dir)) { $installArgs += @("-Dir", $Dir) }
    if ($Force) { $installArgs += "-Force" }

    & $installer @installArgs
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
