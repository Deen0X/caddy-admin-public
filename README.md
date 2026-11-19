# Caddy Admin

Consola web para gestionar rutas de Caddy, con login propio, estadísticas, copias y
diagnóstico. Incluye un instalador que monta **Caddy + consola** y deja el sistema listo
para añadir rutas.

- Authelia/LLDAP son **opcionales**: si existen, la consola los detecta y habilita SSO.
- El panel tiene su propia autenticación; no depende de Authelia.
- Las rutas se pueden publicar por **puerto**, **subdominio** o **path**.

## Funciones

| Sección | Contenido |
|---|---|
| **Rutas** | Alta/edición/baja, búsqueda, ordenación, estado y destino |
| **Ajustes** | Nombre, tema, sesión, muestreo, dominio, email ACME, IP LAN y URLs de integraciones |
| **Seguridad** | Usuario/contraseña, avatar (emoji o URL), sesiones activas |
| **Usuarios** | Cuentas de la consola (admin / solo lectura) |
| **Estadísticas** | Métricas en vivo de Caddy + histórico muestreado, donut y barras |
| **Actividad** | Auditoría filtrable |
| **Copias** | Snapshots de rutas: descargar ZIP y restaurar con validación |
| **Diagnóstico** | Validación del Caddyfile, TCP a cada destino, certificado, disco y métricas |

## Tipos de ruta

| Tipo | URL | Requiere |
|---|---|---|
| `port` | `dominio:81xx` | Nada (funciona con cualquier app) |
| `subdomain` | `app.dominio` | DNS wildcard (`*.dominio`) |
| `path` | `dominio/app` | La app debe soportar base path |

## Instalación en un comando

> Requiere un repositorio **público** para que `raw.githubusercontent.com` funcione.

**Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.sh | sh

# con dominio y email
curl -fsSL https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.sh \
  | sh -s -- --domain TU_DOMINIO --email tu@email.com
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.ps1 | iex

# con dominio y email
$env:DNX_DOMAIN="TU_DOMINIO"; $env:DNX_EMAIL="tu@email.com"
irm https://raw.githubusercontent.com/Deen0X/caddy-admin/master/get.ps1 | iex
```

> Ejecutar código remoto tiene riesgos. Si prefieres revisarlo antes:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/Deen0X/caddy-admin/master/install.sh -o install.sh
> less install.sh
> sh install.sh --domain TU_DOMINIO
> ```

El instalador usa `~/dnx-caddy-admin` (Linux) o `%USERPROFILE%\dnx-caddy-admin`
(Windows), sin permisos de administrador.

## Instalación manual

```bash
git clone https://github.com/Deen0X/caddy-admin.git
cd caddy-admin
bash install.sh --local . --domain TU_DOMINIO --email tu@email.com
```

Opciones: `--dir RUTA`, `--lan-ip IP`, `--force` (sobrescribe `.env` y el Caddyfile inicial).

## Primer arranque

1. Abre `https://IP_LAN:9095` (acepta el aviso de certificado interno).
2. Crea el administrador.
3. En **Ajustes → Dominio**, pon tu dominio y pulsa **Aplicar**.
   La consola genera el Caddyfile, valida y recarga Caddy sin recrear contenedores.

El panel sigue accesible por la IP LAN aunque el certificado del dominio tarde.

## Autenticación

- Propia: SQLite + PBKDF2-HMAC-SHA256 + sesiones con cookie `HttpOnly`.
- Primer arranque: `/setup` (solo desde red privada).
- El resto de servicios pueden seguir protegidos por Authelia/LLDAP si los tienes.
- Sin Authelia, la opción SSO aparece deshabilitada; con Authelia, se habilita sola.

### Reset de contraseña del administrador

```bash
docker compose run --rm caddy-admin --reset-password-admin 'NUEVA_CONTRASEÑA'
# opcional: --user otro_admin
```

## Persistencia

- Base de datos: volumen **`caddy_admin_data`** montado en `/data`.
- Rutas, fragmentos, snapshots y auditoría: `./caddy`.
- Nada de estado dentro de la imagen.

## Estructura

| Ruta | Contenido |
|---|---|
| `caddy/Caddyfile.example` | Ejemplo de Caddyfile para modo pack |
| `caddy/routes/` | Fragmentos de rutas (runtime; no se versiona) |
| `caddy/routes.json` | Índice de rutas (runtime; no se versiona) |
| `caddy/assets/` | Iconos/favicons |
| `webadmin/` | `app.py`, `db.py`, `security.py`, `caddyops.py`, `monitor.py`, `integrations.py`, `templates/`, `static/` |
| `docker-compose.standalone.yml` | Caddy + consola |
| `install.sh` / `install.ps1` | Instaladores Linux / Windows |
| `get.sh` / `get.ps1` | Bootstrap de un solo comando |
| `PLAN_V3.md` / `Project_Architecture.md` | Plan y arquitectura |
| `.env.example` | Variables de ejemplo |

> El repositorio **no incluye** Caddyfile, rutas, backups ni datos de ningún despliegue.
> El instalador los genera vacíos.

## Cómo encaja

```
Internet → :443 / :81xx → Caddy → servicios internos
                              ▲
                              │ API admin :2019 (/metrics)
                       caddy-admin (UI :9095)
                              │ login propio + escribe
                              ▼
                   caddy/routes/*.caddy + routes.json + /data/admin.db
```

## Puertos

Caddy necesita **80, 443 y 9095** libres, y las rutas por puerto usan **8100–8199**.
Si otro servicio ocupa 80/443, Caddy no arranca:

```bash
sudo ss -tlnp | grep -E ':(80|443|9095)\b'
```

Opciones: liberar los puertos, integrar el otro servicio detrás de Caddy o, solo para
pruebas, cambiar los mapeos en `compose.yml`. Sin 80/443 no habrá certificado público.

## Notas

- Cambios en el Caddyfile sin cortar servicio: la consola usa la API `:2019`.
- Si reemplazas el Caddyfile con `sed -i` (nuevo inodo), recrea `caddy` con compose o
  escribe con `cat >` para preservar el inodo.
- `.gitattributes` fuerza LF en `.sh` y CRLF en `.ps1`.
