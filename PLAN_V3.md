# Plan de implementación v3 — Caddy Admin standalone

> Estado: **aprobado**. Backup: `webadmin/prod_backup_20261113_204719/` y tag `pre-v3-plan`.
> Fecha: 2026-11-13.

## 1. Objetivo

Convertir la consola en un producto **instalable y autónomo** (Caddy + panel), con:

- Dominio configurable desde la propia consola (sin `.env`, sin recrear contenedores).
- Rutas por **puerto**, **path** y **subdominio**.
- Detección automática de Authelia (SSO opcional, nunca obligatorio).
- Instalador `.sh` (Linux) y `.ps1` (Windows) que deja el sistema funcionando.
- Sin rutas semilla: Caddy arranca con 0 rutas.

## 2. Decisiones cerradas

| Tema | Decisión |
|---|---|
| Authelia / LLDAP | **Fuera** del producto. Si existen, la consola los detecta y habilita SSO |
| Panel | Login propio siempre; Authelia no autentica el panel |
| Dominio | Editable en la consola; al guardar se regenera Caddyfile + fragmentos y se recarga Caddy |
| Reinicio de contenedores desde la consola | **No**. Solo Caddy (reload por API). Authelia la gestiona el usuario |
| Rutas | `port`, `path`, `subdomain` en v1 |
| Semilla | Eliminada. `DEFAULT_ROUTES` deja de usarse |
| Instalador | Docker en Linux y Windows; instala Caddy + consola |
| Caddyfile en pack existente | No se sobrescribe (`CADDY_MANAGED_CADDYFILE=0`) |

## 3. Modelo de ruta

```json
{
  "id": "tcloud",
  "name": "TCloud",
  "mode": "port",              // port | path | subdomain
  "port": 8131,                // solo mode=port
  "path": "/tcloud",           // solo mode=path
  "host": "tcloud",            // solo mode=subdomain (etiqueta, sin dominio)
  "target": "192.0.2.10:8931",
  "auth": "none",              // authelia solo si está disponible
  "scheme": "http",
  "skip_verify": false,
  "sse": false,
  "enabled": true
}
```

Compatibilidad: una ruta sin `mode` se interpreta como `port`.

### Validaciones

- `port`: 8100–8199, único, no reservado.
- `path`: `^/[a-z0-9][a-z0-9-]{0,30}$`, único, no reservado
  (`/`, `/login`, `/setup`, `/health`, `/api`, `/static`, `/favicon.ico`…).
- `host`: `^[a-z0-9][a-z0-9-]{0,30}$`, único; URL final `https://{host}.{dominio}`.
- `target`: `host:puerto`.
- `auth=authelia` solo si `sso_available`.

### Generación de Caddy

| Modo | Fichero | Contenido |
|---|---|---|
| `port` | `routes/{id}.caddy` | `{dominio}:{puerto} { ... }` |
| `subdomain` | `routes/{id}.caddy` | `{host}.{dominio} { ... }` |
| `path` | `routes/paths/{id}.caddy` | `handle_path {path}/* { ... }` + `handle { ... }` |

Caddyfile gestionado (solo standalone):

```caddy
{
    email {$ACME_EMAIL}
    default_sni {dominio}
    metrics
    admin :2019 { origins http://caddy:2019 http://127.0.0.1:2019 }
}

import routes/*.caddy

# Site raíz: paths y fallback
{dominio} {
    encode zstd gzip
    import routes/paths/*.caddy
    handle { respond 404 }
}

# Panel
{dominio}:9095 { import caddy_admin_ui }
https://{ip_lan}:9095 { tls internal; import caddy_admin_ui }
```

## 4. Modos de despliegue

| Modo | Env | Comportamiento |
|---|---|---|
| **pack** (actual) | `CADDY_MANAGED_CADDYFILE=0` | La consola solo escribe `routes/`; el Caddyfile es del usuario |
| **standalone** (instalador) | `CADDY_MANAGED_CADDYFILE=1` | La consola genera también el Caddyfile desde plantilla |

En modo pack, el dominio de las rutas se lee de `CADDY_BASE_DOMAIN` o del setting; si no hay, se detecta del Caddyfile (primer site).

## 5. Asistente de configuración

- Tras crear el admin, si no hay dominio: banner + `/setup-stack`.
- Campos: dominio, email ACME (opcional), IP LAN detectada (editable).
- Botón **Aplicar**:
  1. Valida formato y resolución DNS (aviso si no resuelve).
  2. Guarda settings.
  3. Regenera Caddyfile (si standalone) y todos los fragmentos.
  4. `caddy validate` + `caddy reload`.
  5. Muestra resultado; si falla, restaura el Caddyfile y los fragmentos previos.
- El panel sigue accesible por IP LAN siempre.

## 6. Detección de SSO

- `integrations.py` con caché de 30 s:
  - `GET http://authelia:9091/api/health` (timeout 2 s)
  - TCP `lldap:3890`
- Expone `sso_available`, `authelia_version`, `lldap_available`.
- UI:
  - Formulario de ruta: si no hay Authelia, la opción “Authelia” está **deshabilitada**.
  - Ajustes: tarjeta “Integraciones” con estado.
  - Lista de rutas: badge “SSO no disponible” si una ruta usa authelia y no está.
- Sin conversión automática.

## 7. Instalador

### `install.sh` (Linux)

1. Comprueba `docker`, `docker compose`, curl/git, puertos 80/443/9095.
2. Descarga el repo (o usa `--local DIR`).
3. Crea `/opt/dnx-caddy-admin` con `caddy/`, `data/`, `.env`.
4. Genera `.env` (dominio opcional, email, TZ, IP LAN).
5. `docker compose up -d --build`.
6. Espera healthchecks y muestra `https://IP_LAN:9095`.
7. Opciones: `--domain`, `--email`, `--dir`, `--local`, `--force`, `--uninstall`.

### `install.ps1` (Windows)

- Equivalente con Docker Desktop; descarga ZIP, extrae, escribe `.env` y ejecuta `docker compose`.
- Aviso de que los contenedores son Linux (WSL2).

### Compose standalone

```yaml
services:
  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443", "443:443/udp", "9095:9095"]
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile
      - ./caddy/routes:/etc/caddy/routes
      - ./caddy/assets:/srv/assets:ro
      - caddy_data:/data
      - caddy_config:/config
    environment:
      ACME_EMAIL: ${ACME_EMAIL}
  caddy-admin:
    build: ./webadmin
    environment:
      CADDY_CONFIG: /caddy/Caddyfile
      CADDY_ROUTES_JSON: /caddy/routes.json
      CADDY_ROUTES_DIR: /caddy/routes
      CADDY_BACKUPS_DIR: /caddy/backups
      CADDY_ADMIN: caddy:2019
      CADDY_BASE_DOMAIN: ${BASE_DOMAIN:-}
      CADDY_MANAGED_CADDYFILE: "1"
      CADDY_LAN_IP: ${LAN_IP:-}
      CADDY_ADMIN_DB: /data/admin.db
    volumes:
      - ./caddy:/caddy
      - caddy_admin_data:/data
```

## 8. Fases

| Fase | Entregable | Verificación |
|---|---|---|
| **0** | Backup + plan + updateproject + git | tag, commit |
| **1** | Modelo `mode` + generación port/path/subdomain + UI | crear/editar los 3 tipos en local |
| **2** | Dominio + Caddyfile gestionado + asistente | cambiar dominio y recargar sin recrear |
| **3** | Detección SSO + UI condicionada | con/sin Authelia |
| **4** | `install.sh` + `install.ps1` + compose standalone + README | instalación limpia en raspi |
| **5** | Pruebas en pack (modo 0) y standalone; commit/push | sin regresiones |

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| El Caddyfile gestionado pisa personalizaciones | Solo en standalone (`MANAGED=1`); backup antes de escribir |
| `sed -i` rompe el bind-mount | Escribir con `cat >` o recrear Caddy |
| Apps sin base path en modo path | Aviso explícito en el formulario; no se garantiza |
| Dominio sin DNS/80/443 | Validación y mensaje de diagnóstico; no se rompe lo anterior |
| Authelia desaparece con rutas SSO | Badge y aviso; sin cambios automáticos |

## 10. Criterios de aceptación

- Instalación limpia en Linux y Windows con `install.*` → panel en `https://IP_LAN:9095`.
- Crear admin, poner dominio, Aplicar → certificado emitido y panel en el dominio.
- Crear una ruta de cada modo y verificar URL:
  - `dominio:81xx`
  - `dominio/path`
  - `sub.dominio`
- Sin Authelia: opción SSO deshabilitada y sistema operativo.
- Con Authelia: opción habilitada sin reiniciar la consola.
- Sin rutas semilla en instalación nueva.
- `--reset-password-admin` sigue funcionando.
- La producción actual (modo pack) no se altera.

## 11. Fuera de alcance

- Instalar Authelia/LLDAP automáticamente.
- 2FA propio en la consola.
- Gestión de usuarios del SSO vía LLDAP.
- Enmascarar paths de apps sin soporte real de base path.
