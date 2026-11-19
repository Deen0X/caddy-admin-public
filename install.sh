#!/usr/bin/env sh
# DNX Caddy Admin — instalador standalone (Linux).
# Instala Caddy + consola. El dominio se configura después desde la consola.
set -eu

REPO_URL="${DNX_REPO_URL:-https://github.com/Deen0X/caddy-admin-public/archive/refs/heads/master.tar.gz}"
DEFAULT_DIR="${HOME:-/opt}/dnx-caddy-admin"
INSTALL_DIR="${DNX_DIR:-$DEFAULT_DIR}"
DOMAIN="${DNX_DOMAIN:-}"
EMAIL="${DNX_EMAIL:-}"
LAN_IP="${DNX_LAN_IP:-}"
FORCE=0
LOCAL=""

usage() {
  cat <<'EOF'
Uso: ./install.sh [opciones]

  --domain DOMINIO   Dominio base (opcional; se puede poner luego en la consola)
  --email EMAIL      Email ACME (opcional)
  --lan-ip IP        IP LAN del panel (por defecto se detecta)
  --dir RUTA         Directorio de instalación (por defecto /opt/dnx-caddy-admin)
  --local RUTA       Usa una copia local del proyecto en vez de descargarlo
  --force            Sobrescribe .env y el Caddyfile inicial
  -h, --help         Muestra esta ayuda

Requisitos: docker, docker compose v2, curl (si no se usa --local).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --lan-ip) LAN_IP="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --local) LOCAL="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage; exit 1 ;;
  esac
done

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Falta '$1' en el sistema." >&2; exit 1; }
}

need docker
docker info >/dev/null 2>&1 || { echo "Docker no está en marcha." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Falta Docker Compose v2." >&2; exit 1; }

check_port() {
  if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | grep -q ":$1 "; then
      echo "AVISO: el puerto $1 ya está ocupado. Caddy no podrá arrancar hasta liberarlo." >&2
      BUSY_PORTS="${BUSY_PORTS:-} $1"
    fi
  fi
}
BUSY_PORTS=""
check_port 80
check_port 443
check_port 9095
if [ -n "$BUSY_PORTS" ]; then
  echo "============================================================" >&2
  echo " AVISO: puertos ocupados:$BUSY_PORTS" >&2
  echo " Caddy no arrancará hasta liberarlos (o cambiar los mapeos)." >&2
  echo " Continuando en 10 s (Ctrl+C para cancelar)..." >&2
  echo "============================================================" >&2
  sleep 10
fi

UID_NUM="$(id -u)"
GID_NUM="$(id -g)"

if [ -z "$LAN_IP" ]; then
  if command -v ip >/dev/null 2>&1; then
    LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
  fi
  if [ -z "$LAN_IP" ] && command -v hostname >/dev/null 2>&1; then
    LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
fi

echo "Instalando en: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

FRESH=0
[ -f "$INSTALL_DIR/.env" ] || FRESH=1

copy_tree() {
  src="$1"
  dst="$2"
  ( cd "$src" && tar -cf - \
      --exclude=.git \
      --exclude=.antigravity \
      --exclude=.opencode \
      --exclude=.agent \
      --exclude=.env \
      --exclude=compose \
      --exclude=resources \
      --exclude=tools \
      --exclude='webadmin/prod_backup_*' \
      --exclude=caddy/Caddyfile \
      --exclude=caddy/routes.json \
      --exclude=caddy/routes \
      --exclude=caddy/backups \
      --exclude='caddy/routes.bak-*' \
      . ) | tar -C "$dst" -xf -
}

if [ -n "$LOCAL" ]; then
  echo "Copiando desde $LOCAL"
  copy_tree "$LOCAL" "$INSTALL_DIR"
else
  need curl
  TMP="$(mktemp -d)"
  echo "Descargando $REPO_URL"
  curl -fsSL "$REPO_URL" -o "$TMP/repo.tar.gz"
  tar -xzf "$TMP/repo.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -maxdepth 1 -type d -name 'caddy-admin-*' | head -n 1)"
  if [ -z "$SRC" ]; then
    echo "No se pudo extraer el repo." >&2
    exit 1
  fi
  copy_tree "$SRC" "$INSTALL_DIR"
  rm -rf "$TMP"
fi

cd "$INSTALL_DIR"

# Instalación nueva: nunca reutilizar Caddyfile/rutas de ejemplo del repo.
if [ "$FRESH" -eq 1 ] || [ "$FORCE" -eq 1 ]; then
  rm -f caddy/Caddyfile caddy/routes.json
  rm -f caddy/routes/*.caddy caddy/routes/paths/*.caddy 2>/dev/null || true
  printf '{\n  "routes": []\n}\n' > caddy/routes.json
fi

if [ ! -f compose.yml ] && [ -f docker-compose.standalone.yml ]; then
  cp docker-compose.standalone.yml compose.yml
fi
if [ ! -f compose.yml ]; then
  echo "No se encontró docker-compose.standalone.yml." >&2
  exit 1
fi

if [ ! -f .env ] || [ "$FORCE" -eq 1 ]; then
  cat > .env <<EOF
BASE_DOMAIN=$DOMAIN
ACME_EMAIL=$EMAIL
LAN_IP=$LAN_IP
DNX_UID=$UID_NUM
DNX_GID=$GID_NUM
EOF
  chmod 600 .env 2>/dev/null || true
fi

mkdir -p caddy/routes/paths caddy/backups
if [ ! -f caddy/routes.json ]; then
  printf '{\n  "routes": []\n}\n' > caddy/routes.json
fi

# Caddyfile mínimo: panel por IP LAN con certificado interno.
# La consola lo sustituye por el completo al aplicar el dominio.
if [ ! -f caddy/Caddyfile ] || [ "$FORCE" -eq 1 ]; then
  PANEL_HOST="$LAN_IP"
  [ -n "$PANEL_HOST" ] || PANEL_HOST="localhost"
  cat > caddy/Caddyfile <<EOF
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

https://$PANEL_HOST:9095 {
	tls internal
	import caddy_admin_ui
}
EOF
fi

if [ -z "${DNX_COMMIT:-}" ] && command -v curl >/dev/null 2>&1; then
  API_REPO="${DNX_API_REPO:-Deen0X/caddy-admin-public}"
  DNX_COMMIT="$(curl -fsSL "https://api.github.com/repos/$API_REPO/commits/master" 2>/dev/null \
    | sed -n 's/.*"sha": *"\([a-f0-9]\{40\}\)".*/\1/p' | head -n 1 || true)"
fi
export DNX_COMMIT="${DNX_COMMIT:-}"

echo "Construyendo y arrancando..."
docker compose -f compose.yml up -d --build

echo "Esperando a que la consola esté sana..."
i=0
while [ "$i" -lt 60 ]; do
  STATUS="$(docker inspect --format '{{.State.Health.Status}}' dnx-caddy-admin 2>/dev/null || echo starting)"
  [ "$STATUS" = "healthy" ] && break
  i=$((i + 1))
  sleep 2
done

URL="https://${LAN_IP:-localhost}:9095"
cat <<EOF

============================================================
 DNX Caddy Admin instalado
 Panel: $URL
   1. Acepta el aviso del certificado (TLS interno).
   2. Crea el administrador.
   3. Ajustes -> Dominio: pon tu dominio y pulsa Aplicar.

 Directorio: $INSTALL_DIR
 Logs:      docker compose -f $INSTALL_DIR/compose.yml logs -f caddy-admin
 Parar:     docker compose -f $INSTALL_DIR/compose.yml down
============================================================
EOF

if [ -z "$LAN_IP" ]; then
  echo "Aviso: no se detectó IP LAN. Usa --lan-ip o accede por localhost." >&2
fi
