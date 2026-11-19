#!/usr/bin/env sh
# Bootstrap de DNX Caddy Admin (Linux).
# Uso:
#   curl -fsSL https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.sh | sh
#   curl -fsSL .../get.sh | sh -s -- --domain micasa.duckdns.org --email tu@email.com
set -eu

REPO_URL="${DNX_REPO_URL:-https://github.com/Deen0X/caddy-admin-public/archive/refs/heads/master.tar.gz}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Falta '$1' en el sistema." >&2; exit 1; }
}

need curl
need tar

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

echo "Descargando DNX Caddy Admin..."
curl -fsSL "$REPO_URL" -o "$TMP/repo.tar.gz"
tar -xzf "$TMP/repo.tar.gz" -C "$TMP"

SRC="$(find "$TMP" -maxdepth 1 -type d -name 'caddy-admin-*' | head -n 1)"
if [ -z "$SRC" ]; then
  echo "No se pudo extraer el repositorio." >&2
  exit 1
fi

chmod +x "$SRC/install.sh" 2>/dev/null || true
sh "$SRC/install.sh" --local "$SRC" "$@"
