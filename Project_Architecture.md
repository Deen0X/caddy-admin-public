# Project Architecture (SSoT) — Caddy Admin

> Fuente única de verdad de la arquitectura. Actualizado: 2026-11-13 (v3).
> Detalle del plan: `PLAN_V3.md`.

## 1. Visión

Consola web para gestionar rutas de Caddy, con login propio, y un instalador que
monta **Caddy + consola**. Authelia/LLDAP son **opcionales**: si existen, la consola
los detecta y habilita SSO en las rutas.

## 2. Componentes

| Componente | Rol | Estado |
|---|---|---|
| `caddy` (caddy:2-alpine) | Termina TLS, sirve los site blocks, API admin :2019, `/metrics` | Obligatorio |
| `caddy-admin` (Python/FastAPI) | UI, auth propia, rutas, ajustes, estadísticas | Obligatorio |
| `authelia` (opcional) | SSO, 2FA, políticas | Detectado, no instalado |
| `lldap` (opcional) | Directorio de usuarios | Detectado, no instalado |

## 3. Flujo de datos

```
Navegador → Caddy (:443 / :dominio:9095)
   ├─ site de app  → reverse_proxy target (auth: authelia | none)
   └─ site panel   → reverse_proxy caddy-admin:9095 (login propio)
caddy-admin:
   ├─ escribe caddy/routes.json + routes/*.caddy (+ routes/paths/*.caddy)
   ├─ valida con `caddy validate` y recarga por API :2019
   ├─ SQLite en /data/admin.db (usuarios, sesiones, ajustes, muestras)
   └─ lee /metrics de Caddy para estadísticas
```

## 4. Modos de despliegue

| Modo | `CADDY_MANAGED_CADDYFILE` | Caddyfile |
|---|---|---|
| **pack** | `0` | Lo mantiene el usuario; la consola solo escribe `routes/` |
| **standalone** | `1` | La consola lo genera desde plantilla (dominio, panel, imports) |

En standalone, si `CADDY_BASE_DOMAIN` viene en el entorno y el Caddyfile aún no es
gestionado, se aplica el dominio al arrancar (genera Caddyfile + fragmentos y recarga).

## 5. Modelo de ruta

`mode`: `port` | `path` | `subdomain`.

| Modo | URL | Generación |
|---|---|---|
| port | `dominio:81xx` | site block por puerto |
| subdomain | `host.dominio` | site block por host |
| path | `dominio/path` | `handle_path` dentro del site raíz |

Compatibilidad: sin `mode` → `port`.

## 6. Autenticación

- **Panel**: login propio (SQLite + PBKDF2 + sesión). Nunca depende de Authelia.
- **Rutas**: `auth=none` o `auth=authelia` (solo si la sonda SSO está OK).
- Bootstrap del admin: `/setup`, solo desde IP privada.
- CLI: `python app.py --reset-password-admin 'x'`.

## 7. Detección de SSO

`integrations.py` (caché 30 s):
- `GET http://authelia:9091/api/health`
- TCP `lldap:3890`
La UI deshabilita “Authelia” si no está disponible. Sin conversiones automáticas.

## 8. Ficheros críticos

| Ruta | Uso |
|---|---|
| `webadmin/app.py` | Rutas HTTP, auth, ajustes, secciones |
| `webadmin/db.py` | SQLite |
| `webadmin/security.py` | PBKDF2, sesiones, rate limit |
| `webadmin/caddyops.py` | Rutas, Caddyfile, validación, reload, backups, auditoría |
| `webadmin/monitor.py` | Métricas, TLS, TCP, muestreo |
| `webadmin/integrations.py` | Detección Authelia/LLDAP (v3) |
| `caddy/Caddyfile` | Configuración base (gestionada o del usuario) |
| `caddy/routes/` | Fragmentos port/subdomain |
| `caddy/routes/paths/` | Fragmentos path |
| `/data/admin.db` | Estado de la consola (volumen) |

## 9. Reglas

1. Nunca recrear `caddy-admin` con `docker run`: perdería el alias DNS.
2. Escribir el Caddyfile con `cat >` (mismo inodo) o recrear `caddy`.
3. En modo pack no tocar el Caddyfile.
4. Antes de aplicar: backup → validate → reload → restore si falla.
5. El panel debe seguir accesible por IP LAN aunque el dominio falle.
