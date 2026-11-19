"""Operaciones sobre Caddy: rutas, fragmentos, Caddyfile, validación y backups.

Modos de ruta:
  - port      -> site block {dominio}:{puerto}
  - subdomain -> site block {host}.{dominio}
  - path      -> handle_path dentro del site raíz {dominio}

El Caddyfile solo se genera si CADDY_MANAGED_CADDYFILE=1 (standalone).
En modo pack (0) se respeta el Caddyfile del usuario.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CADDY_CONFIG = Path(os.getenv("CADDY_CONFIG", "/caddy/Caddyfile"))
CADDY_ROUTES_JSON = Path(os.getenv("CADDY_ROUTES_JSON", "/caddy/routes.json"))
CADDY_ROUTES_DIR = Path(os.getenv("CADDY_ROUTES_DIR", "/caddy/routes"))
CADDY_PATHS_DIR = CADDY_ROUTES_DIR / "paths"
CADDY_BACKUPS = Path(os.getenv("CADDY_BACKUPS_DIR", "/caddy/backups"))
CADDY_AUDIT = Path(os.getenv("CADDY_AUDIT_LOG", "/caddy/admin-audit.log"))
CADDY_ADMIN = os.getenv("CADDY_ADMIN", "caddy:2019")
CADDY_BIN = os.getenv("CADDY_BIN", "/usr/local/bin/caddy")
PORT_MIN = int(os.getenv("CADDY_ROUTE_PORT_MIN", "8100"))
PORT_MAX = int(os.getenv("CADDY_ROUTE_PORT_MAX", "8199"))
RESERVED_PORTS = {80, 443, 8095, 8096, 8098, 8099, 8443, 9091, 9095, 17170}

MODES = {"port", "path", "subdomain"}
TARGET_RE = re.compile(
    r"^(?P<address>[A-Za-z0-9._-]+:\d{1,5})(?P<path>/[A-Za-z0-9._~!$&()*+,;=:@%/-]*)?$"
)
RESERVED_PATHS = {
    "",
    "/",
    "/login",
    "/logout",
    "/setup",
    "/setup-stack",
    "/health",
    "/api",
    "/static",
    "/favicon.ico",
    "/favicon-32.png",
    "/apple-touch-icon.png",
    "/ajustes",
    "/seguridad",
    "/usuarios",
    "/estadisticas",
    "/actividad",
    "/copias",
    "/diagnostico",
}

logger = logging.getLogger("caddy-admin")
apply_lock = threading.Lock()


# ---------------------------------------------------------------- destinos

def split_target(target: str) -> tuple[str, str]:
    """Separa host:puerto de una ruta base opcional en el destino.

    Ejemplos:
      - 192.0.2.10:8080            -> ("192.0.2.10:8080", "")
      - 192.0.2.10:8080/downloader -> ("192.0.2.10:8080", "/downloader")
    """
    match = TARGET_RE.fullmatch(str(target or "").strip())
    if not match:
        return "", ""
    path = re.sub(r"/{2,}", "/", (match.group("path") or "").strip()).rstrip("/")
    if any(part == ".." for part in path.split("/")):
        return "", ""
    return match.group("address"), path


def normalize_target(target: str) -> str:
    address, path = split_target(target)
    if not address:
        return str(target or "").strip()
    return f"{address}{path}"


def backend_url(route: dict[str, Any]) -> str:
    address, path = split_target(str(route.get("target", "")))
    if not address:
        return ""
    scheme = route.get("scheme", "http")
    return f"{scheme}://{address}{path}"


def backend_hostport(route: dict[str, Any]) -> tuple[str, int] | None:
    address, _ = split_target(str(route.get("target", "")))
    if not address or ":" not in address:
        return None
    host, port = address.rsplit(":", 1)
    if not port.isdigit():
        return None
    return host, int(port)


def _same_port_routes(
    route: dict[str, Any], routes: list[dict[str, Any]], enabled_only: bool = False
) -> list[dict[str, Any]]:
    port = route.get("port")
    group: list[dict[str, Any]] = []
    for item in routes:
        if item.get("mode", "port") != "port" or item.get("port") != port:
            continue
        if enabled_only and not item.get("enabled"):
            continue
        group.append(item)
    if not any(item is route or item.get("id") == route.get("id") for item in group):
        if not enabled_only or route.get("enabled"):
            group.append(route)
    return group


def public_path(route: dict[str, Any], routes: list[dict[str, Any]]) -> str:
    """Ruta pública de una ruta de puerto cuando comparte puerto.

    Si el puerto solo tiene una ruta activa se publica en la raíz. Si hay varias,
    cada una se publica bajo la ruta base de su destino para poder distinguirlas.
    """
    if route.get("mode", "port") != "port":
        return ""
    group = _same_port_routes(route, routes, enabled_only=True)
    if len(group) <= 1:
        return ""
    _address, base_path = split_target(str(route.get("target", "")))
    return base_path


# ---------------------------------------------------------------- configuración

def managed_caddyfile() -> bool:
    return os.getenv("CADDY_MANAGED_CADDYFILE", "0") == "1"


def _setting(key: str, default: str = "") -> str:
    try:
        import db  # import local para evitar ciclos

        return db.get_setting(key, default) or default
    except Exception:
        return default


def current_domain() -> str:
    domain = _setting("base_domain", "").strip()
    if not domain:
        domain = os.getenv("CADDY_BASE_DOMAIN", "").strip()
    if not domain:
        domain = detect_domain()
    return domain


def current_email() -> str:
    email = _setting("acme_email", "").strip()
    return email or os.getenv("ACME_EMAIL", "").strip()


def current_lan_ip() -> str:
    ip = _setting("lan_ip", "").strip()
    return ip or os.getenv("CADDY_LAN_IP", "").strip()


def detect_domain() -> str:
    """Primer host con dominio del Caddyfile (modo pack)."""
    sites = parse_sites()
    for site in sites:
        host = site["host"]
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
            return host
    return ""


# ---------------------------------------------------------------- rutas

def load_routes() -> list[dict[str, Any]]:
    if not CADDY_ROUTES_JSON.exists():
        save_json([])
        return []

    data = json.loads(CADDY_ROUTES_JSON.read_text(encoding="utf-8"))
    routes = data.get("routes", [])

    if not isinstance(routes, list):
        raise ValueError("routes.json debe contener una lista 'routes'")

    for route in routes:
        route.setdefault("mode", "port")
    return routes


def save_json(routes: list[dict[str, Any]]) -> None:
    atomic_write(
        CADDY_ROUTES_JSON,
        json.dumps({"routes": routes}, indent=2, ensure_ascii=False) + "\n",
    )


def find_route(routes: list[dict[str, Any]], route_id: str) -> dict[str, Any] | None:
    return next((route for route in routes if route["id"] == route_id), None)


def next_free_port(routes: list[dict[str, Any]]) -> int:
    used = {route.get("port") for route in routes if route.get("mode", "port") == "port"}
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in used and port not in RESERVED_PORTS:
            return port
    return PORT_MIN


def used_ports(routes: list[dict[str, Any]], ignore_id: str | None = None) -> set[int]:
    used: set[int] = set()
    for route in routes:
        if route.get("mode", "port") != "port":
            continue
        if ignore_id and route.get("id") == ignore_id:
            continue
        port = route.get("port")
        if isinstance(port, int):
            used.add(port)
    return used


def free_ports(routes: list[dict[str, Any]], ignore_id: str | None = None) -> list[int]:
    used = used_ports(routes, ignore_id)
    return [p for p in range(PORT_MIN, PORT_MAX + 1) if p not in used and p not in RESERVED_PORTS]


def validate_path(value: str) -> str | None:
    value = (value or "").strip()
    if not re.fullmatch(r"/[a-z0-9][a-z0-9-]{0,30}", value):
        return "El path debe ser /letras-numeros-guiones (máx. 31)."
    if value.lower() in RESERVED_PATHS:
        return f"El path {value} está reservado por la consola."
    return None


def validate_host(value: str) -> str | None:
    value = (value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", value):
        return "El subdominio debe ser una etiqueta DNS (a-z, 0-9, guiones)."
    return None


def validate_route(
    route: dict[str, Any],
    routes: list[dict[str, Any]],
    ignore_id: str | None = None,
) -> str | None:
    name = str(route.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        return "El nombre debe tener entre 1 y 60 caracteres."

    if not route.get("id"):
        route["id"] = slugify(name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", route["id"]):
        return "El identificador generado no es valido."
    if any(item["id"] == route["id"] and item["id"] != ignore_id for item in routes):
        return "Ya existe una ruta con ese nombre. Cambia el nombre."

    mode = route.get("mode", "port")
    if mode not in MODES:
        return "El modo de ruta no es valido."

    if mode == "port":
        port = route.get("port")
        if not isinstance(port, int) or not PORT_MIN <= port <= PORT_MAX:
            return f"El puerto debe estar entre {PORT_MIN} y {PORT_MAX}."
        if port in RESERVED_PORTS:
            return f"El puerto {port} esta reservado por el pack."
        address, _base_path = split_target(str(route.get("target", "")))
        if address:
            group = [
                item
                for item in routes
                if item.get("mode", "port") == "port"
                and item.get("port") == port
                and item.get("id") != ignore_id
            ]
            group.append(route)
            if len(group) > 1:
                paths: list[str] = []
                for item in group:
                    _item_address, item_path = split_target(str(item.get("target", "")))
                    paths.append(item_path)
                if paths.count("") > 1:
                    return (
                        f"El puerto {port} ya tiene una ruta raíz. Para compartirlo, "
                        "usa destinos con rutas base distintas (por ejemplo "
                        "192.0.2.10:8080/app)."
                    )
                seen: set[str] = set()
                for item_path in paths:
                    if not item_path:
                        continue
                    if item_path in seen:
                        return (
                            f"El puerto {port} ya tiene una ruta para {item_path}. "
                            "Usa otra ruta base en el destino."
                        )
                    seen.add(item_path)
    elif mode == "path":
        error = validate_path(route.get("path", ""))
        if error:
            return error
        route["path"] = route["path"].strip().lower()
        if any(
            item.get("mode") == "path"
            and item.get("path", "").lower() == route["path"]
            and item["id"] != ignore_id
            for item in routes
        ):
            return f"El path {route['path']} ya lo usa otra ruta."
    else:
        error = validate_host(route.get("host", ""))
        if error:
            return error
        route["host"] = route["host"].strip().lower()
        if any(
            item.get("mode") == "subdomain"
            and item.get("host", "").lower() == route["host"]
            and item["id"] != ignore_id
            for item in routes
        ):
            return f"El subdominio {route['host']} ya lo usa otra ruta."

    target = str(route.get("target", "")).strip()
    address, _base_path = split_target(target)
    if not address:
        return (
            "El destino debe ser host:puerto o host:puerto/prefijo, por ejemplo "
            "192.0.2.10:8080 o 192.0.2.10:8080/app."
        )
    target_port = int(address.rsplit(":", 1)[1])
    if not 1 <= target_port <= 65535:
        return "El puerto de destino no es valido."

    if route.get("scheme", "http") not in {"http", "https"}:
        return "El esquema del backend debe ser http o https."
    if route.get("auth") not in {"authelia", "none"}:
        return "El modo de autenticacion no es valido."

    favicon = str(route.get("favicon", "")).strip()
    if favicon:
        parsed = urlparse(favicon)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or len(favicon) > 500
        ):
            return "El favicon debe ser una URL http(s) valida."

    return None


# ---------------------------------------------------------------- render

def _indent(lines: list[str], tabs: int = 1) -> list[str]:
    pad = "\t" * tabs
    return [pad + line if line else "" for line in lines]


def _proxy_lines(route: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if route.get("auth") == "authelia":
        lines.extend(
            [
                "forward_auth authelia:9091 {",
                "\turi /api/authz/forward-auth",
                "\tcopy_headers Remote-User Remote-Groups Remote-Email Remote-Name",
                "}",
                "",
            ]
        )

    scheme = route.get("scheme", "http")
    target = str(route.get("target", "")).strip()
    address, base_path = split_target(target)
    if not address:
        # Compatibilidad con destinos antiguos ya validados.
        address, base_path = target, ""
    upstream = f"{scheme}://{address}" if scheme == "https" else address
    skip_verify = bool(route.get("skip_verify", False)) and scheme == "https"
    sse = bool(route.get("sse", False))

    if not base_path and not sse and not skip_verify:
        lines.append(f"reverse_proxy {upstream}")
        return lines

    lines.append(f"reverse_proxy {upstream} {{")
    if base_path:
        lines.append(f"\trewrite {base_path}{{uri}}")
    if sse:
        lines.append("\tflush_interval -1")
    if skip_verify or sse:
        lines.append("\ttransport http {")
        if skip_verify:
            lines.append("\t\ttls_insecure_skip_verify")
        if sse:
            lines.append("\t\tread_timeout 0")
            lines.append("\t\twrite_timeout 0")
        lines.append("\t}")
    lines.append("}")
    return lines


def render_site(route: dict[str, Any], domain: str) -> str:
    mode = route.get("mode", "port")
    address = f"{domain}:{route['port']}" if mode == "port" else f"{route['host']}.{domain}"

    lines = [
        f"# {route['name']} ({mode}) - generado por caddy-admin.",
        f"{address} {{",
        "\tencode zstd gzip",
        "",
    ]
    lines.extend(_indent(_proxy_lines(route), 1))
    lines.extend(
        [
            "",
            "\tlog {",
            "\t\toutput stdout",
            "\t\tformat console",
            "\t}",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def render_port_site(routes: list[dict[str, Any]], domain: str) -> str:
    """Renderiza un puerto compartido por varias rutas.

    Las rutas con ruta base se publican bajo esa ruta (handle_path) y la ruta
    sin ruta base queda como catch-all. Las más específicas van primero.
    """
    if len(routes) == 1:
        return render_site(routes[0], domain)

    port = routes[0]["port"]
    names = ", ".join(route.get("name", "") for route in routes)
    lines = [
        f"# Puerto {port}: {names} - generado por caddy-admin.",
        f"{domain}:{port} {{",
        "\tencode zstd gzip",
        "",
    ]

    ordered = sorted(
        routes,
        key=lambda route: (
            0 if public_path(route, routes) else 1,
            -len(public_path(route, routes)),
            str(route.get("name", "")).lower(),
        ),
    )
    for route in ordered:
        route_path = public_path(route, routes)
        if route_path:
            lines.append(f"\tredir {route_path} {route_path}/ 308")
    if any(public_path(route, routes) for route in ordered):
        lines.append("")

    for route in ordered:
        route_path = public_path(route, routes)
        if route_path:
            lines.append(f"\thandle_path {route_path}/* {{")
            lines.extend(_indent(_proxy_lines(route), 2))
            lines.append("\t}")
        else:
            lines.append("\thandle {")
            lines.extend(_indent(_proxy_lines(route), 2))
            lines.append("\t}")
        lines.append("")

    lines.extend(
        [
            "\tlog {",
            "\t\toutput stdout",
            "\t\tformat console",
            "\t}",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def render_path(route: dict[str, Any]) -> str:
    path = route["path"]
    lines = [
        f"# {route['name']} (path) - generado por caddy-admin.",
        f"redir {path} {path}/ 308",
        f"handle_path {path}/* {{",
    ]
    lines.extend(_indent(_proxy_lines(route), 1))
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def public_url(
    route: dict[str, Any], domain: str, routes: list[dict[str, Any]] | None = None
) -> str:
    if not domain:
        return ""
    mode = route.get("mode", "port")
    if mode == "port":
        url = f"https://{domain}:{route['port']}"
        if routes:
            url += public_path(route, routes)
        return url
    if mode == "subdomain":
        return f"https://{route['host']}.{domain}"
    return f"https://{domain}{route['path']}"


def caddyfile_template(domain: str, email: str = "", lan_ip: str = "") -> str:
    header = [
        "# Generado por DNX Caddy Admin (v3).",
        "# Los cambios manuales se perderán al aplicar desde la consola.",
        "{",
    ]
    if email:
        header.append(f"\temail {email}")
    header.extend(
        [
            f"\tdefault_sni {domain}",
            "\tmetrics",
            "\tservers {",
            "\t\tprotocols h1 h2",
            "\t}",
            "\tadmin :2019 {",
            "\t\torigins http://caddy:2019 http://127.0.0.1:2019",
            "\t}",
            "}",
            "",
            "import routes/*.caddy",
            "",
            f"# Sitio raíz del dominio: rutas por path.",
            f"{domain} {{",
            "\tencode zstd gzip",
            "\timport routes/paths/*.caddy",
            "\thandle {",
            f"\t\tredir https://{domain}:9095/ 302",
            "\t}",
            "}",
            "",
            "# Panel de administración (dominio).",
            f"{domain}:9095 {{",
            "\timport caddy_admin_ui",
            "}",
            "",
        ]
    )
    if lan_ip:
        header.extend(
            [
                "# Panel de administración (IP LAN, certificado interno).",
                f"https://{lan_ip}:9095 {{",
                "\ttls internal",
                "\timport caddy_admin_ui",
                "}",
                "",
            ]
        )
    return "\n".join(header)


PANEL_SNIPPET = """# Snippet del panel, compartido por dominio e IP.
(caddy_admin_ui) {
\tencode zstd gzip
\thandle {
\t\treverse_proxy caddy-admin:9095
\t}
\tlog {
\t\toutput stdout
\t\tformat console
\t}
}
"""


def build_caddyfile(domain: str, email: str = "", lan_ip: str = "") -> str:
    return PANEL_SNIPPET + "\n" + caddyfile_template(domain, email, lan_ip)


# ---------------------------------------------------------------- escritura

def write_route_files(routes: list[dict[str, Any]], domain: str) -> None:
    CADDY_ROUTES_DIR.mkdir(parents=True, exist_ok=True)
    CADDY_PATHS_DIR.mkdir(parents=True, exist_ok=True)

    sites: dict[str, str] = {}
    paths: dict[str, str] = {}
    port_groups: dict[int, list[dict[str, Any]]] = {}
    for route in routes:
        if not route.get("enabled"):
            continue
        mode = route.get("mode", "port")
        if mode == "path":
            paths[f"{route['id']}.caddy"] = render_path(route)
        elif mode == "port":
            port_groups.setdefault(int(route["port"]), []).append(route)
        else:
            sites[f"{route['id']}.caddy"] = render_site(route, domain)

    for port, group in sorted(port_groups.items()):
        sites[f"port_{port}.caddy"] = render_port_site(group, domain)

    if not sites:
        sites["00-empty.caddy"] = "# Sin rutas por puerto o subdominio.\n"
    if not paths:
        paths["00-empty.caddy"] = "# Sin rutas por path.\n"

    _sync_dir(CADDY_ROUTES_DIR, sites)
    _sync_dir(CADDY_PATHS_DIR, paths)


def _sync_dir(directory: Path, desired: dict[str, str]) -> None:
    for path in directory.glob("*.caddy"):
        if path.name not in desired:
            path.unlink()
    for name, content in desired.items():
        atomic_write(directory / name, content)


def write_caddyfile(domain: str, email: str = "", lan_ip: str = "") -> None:
    """Escribe el Caddyfile preservando el inodo (bind-mount de archivo)."""
    content = build_caddyfile(domain, email, lan_ip)
    CADDY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CADDY_CONFIG.exists():
        # Truncar en sitio: no usar os.replace (rompería el bind-mount).
        CADDY_CONFIG.write_text(content, encoding="utf-8")
    else:
        CADDY_CONFIG.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- aplicar

def apply_routes(routes: list[dict[str, Any]], user: str, audit: bool = True) -> tuple[bool, str]:
    domain = current_domain()
    if not domain:
        return False, "Falta el dominio base. Configúralo en Ajustes."

    with apply_lock:
        old_json = CADDY_ROUTES_JSON.read_text(encoding="utf-8") if CADDY_ROUTES_JSON.exists() else None
        old_files = read_route_files()
        snapshot()

        try:
            save_json(routes)
            write_route_files(routes, domain)

            validation = run_caddy(["validate", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile"])
            if validation.returncode != 0:
                restore(old_json, old_files)
                return False, validation_error(validation)

            reload = run_caddy(
                ["reload", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile", "--address", CADDY_ADMIN]
            )
            if reload.returncode != 0:
                restore(old_json, old_files)
                return False, f"Caddy no recargo la configuracion: {output_of(reload)}"

            if audit:
                audit_log(user, "apply", f"{len(routes)} rutas", True, "")
            logger.info("Configuracion aplicada por %s (%s rutas)", user, len(routes))
            return True, "Configuracion aplicada."
        except Exception as exc:
            restore(old_json, old_files)
            if audit:
                audit_log(user, "apply", "", False, str(exc))
            logger.exception("Fallo aplicando rutas")
            return False, f"No se pudo aplicar: {exc}"


def apply_domain(domain: str, email: str, lan_ip: str, user: str) -> tuple[bool, str]:
    """Regenera Caddyfile + fragmentos y recarga. Solo en modo standalone."""
    if not managed_caddyfile():
        return False, "El Caddyfile lo gestiona el usuario (modo pack)."

    domain = domain.strip().lower()
    if not re.fullmatch(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", domain):
        return False, "El dominio no es válido."

    with apply_lock:
        old_caddyfile = CADDY_CONFIG.read_text(encoding="utf-8") if CADDY_CONFIG.exists() else None
        old_json = CADDY_ROUTES_JSON.read_text(encoding="utf-8") if CADDY_ROUTES_JSON.exists() else None
        old_files = read_route_files()
        snapshot()

        try:
            write_caddyfile(domain, email, lan_ip)
            routes = load_routes()
            save_json(routes)
            write_route_files(routes, domain)

            validation = run_caddy(["validate", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile"])
            if validation.returncode != 0:
                _restore_caddyfile(old_caddyfile)
                restore(old_json, old_files)
                return False, validation_error(validation)

            reload = run_caddy(
                ["reload", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile", "--address", CADDY_ADMIN]
            )
            if reload.returncode != 0:
                _restore_caddyfile(old_caddyfile)
                restore(old_json, old_files)
                return False, f"Caddy no recargo la configuracion: {output_of(reload)}"

            audit_log(user, "apply-domain", domain, True, "")
            return True, f"Dominio {domain} aplicado."
        except Exception as exc:
            _restore_caddyfile(old_caddyfile)
            restore(old_json, old_files)
            audit_log(user, "apply-domain", domain, False, str(exc))
            return False, f"No se pudo aplicar: {exc}"


def _restore_caddyfile(content: str | None) -> None:
    if content is None:
        CADDY_CONFIG.unlink(missing_ok=True)
        return
    CADDY_CONFIG.write_text(content, encoding="utf-8")


def run_caddy(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [CADDY_BIN, *args], capture_output=True, text=True, timeout=30, check=False
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "sin detalle").strip()[:1500]


def validation_error(result: subprocess.CompletedProcess[str]) -> str:
    return f"La configuracion no es valida: {output_of(result)}"


def read_route_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for directory in (CADDY_ROUTES_DIR, CADDY_PATHS_DIR):
        if not directory.exists():
            continue
        for path in directory.glob("*.caddy"):
            files[f"{directory.name}/{path.name}"] = path.read_text(encoding="utf-8")
    return files


def restore(old_json: str | None, old_files: dict[str, str]) -> None:
    if old_json is None:
        CADDY_ROUTES_JSON.unlink(missing_ok=True)
    else:
        atomic_write(CADDY_ROUTES_JSON, old_json)

    for directory in (CADDY_ROUTES_DIR, CADDY_PATHS_DIR):
        if not directory.exists():
            continue
        for path in directory.glob("*.caddy"):
            key = f"{directory.name}/{path.name}"
            if key not in old_files:
                path.unlink()
    for key, content in old_files.items():
        directory, name = key.split("/", 1)
        target = CADDY_ROUTES_DIR if directory == "routes" else CADDY_PATHS_DIR
        atomic_write(target / name, content)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "ruta"


# ---------------------------------------------------------------- backups

def snapshot() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = CADDY_BACKUPS / stamp
    target.mkdir(parents=True, exist_ok=True)
    if CADDY_ROUTES_JSON.exists():
        shutil.copy2(CADDY_ROUTES_JSON, target / "routes.json")
    if CADDY_CONFIG.exists():
        shutil.copy2(CADDY_CONFIG, target / "Caddyfile")
    for name, content in read_route_files().items():
        safe = name.replace("/", "__")
        (target / safe).write_text(content, encoding="utf-8")
    return target


def list_snapshots() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not CADDY_BACKUPS.exists():
        return items
    for path in sorted(CADDY_BACKUPS.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        files = list(path.glob("*"))
        items.append(
            {
                "name": path.name,
                "files": len(files),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "size": sum(f.stat().st_size for f in files if f.is_file()),
            }
        )
    return items


def snapshot_dir(name: str) -> Path | None:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{6}", name):
        return None
    path = CADDY_BACKUPS / name
    return path if path.is_dir() else None


def restore_snapshot(name: str, user: str) -> tuple[bool, str]:
    source = snapshot_dir(name)
    if source is None:
        return False, "Copia no encontrada."

    routes_json = source / "routes.json"
    caddyfile = source / "Caddyfile"
    files: dict[str, str] = {}
    for path in source.glob("*.caddy"):
        if "__" in path.name:
            directory, fname = path.name.split("__", 1)
            files[f"{directory}/{fname}"] = path.read_text(encoding="utf-8")
        else:
            files[f"routes/{path.name}"] = path.read_text(encoding="utf-8")

    snapshot()
    old_caddyfile = CADDY_CONFIG.read_text(encoding="utf-8") if CADDY_CONFIG.exists() else None
    old_json = CADDY_ROUTES_JSON.read_text(encoding="utf-8") if CADDY_ROUTES_JSON.exists() else None
    old_files = read_route_files()

    try:
        if routes_json.exists():
            atomic_write(CADDY_ROUTES_JSON, routes_json.read_text(encoding="utf-8"))
        if caddyfile.exists() and managed_caddyfile():
            _restore_caddyfile(caddyfile.read_text(encoding="utf-8"))
        for directory in (CADDY_ROUTES_DIR, CADDY_PATHS_DIR):
            if not directory.exists():
                continue
            for path in directory.glob("*.caddy"):
                key = f"{directory.name}/{path.name}"
                if key not in files:
                    path.unlink()
        for key, content in files.items():
            directory, fname = key.split("/", 1)
            target = CADDY_ROUTES_DIR if directory == "routes" else CADDY_PATHS_DIR
            atomic_write(target / fname, content)

        validation = run_caddy(["validate", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile"])
        if validation.returncode != 0:
            _restore_caddyfile(old_caddyfile)
            restore(old_json, old_files)
            return False, validation_error(validation)

        reload = run_caddy(
            ["reload", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile", "--address", CADDY_ADMIN]
        )
        if reload.returncode != 0:
            _restore_caddyfile(old_caddyfile)
            restore(old_json, old_files)
            return False, f"Caddy no recargo: {output_of(reload)}"

        audit_log(user, "restore", name, True, "")
        return True, f"Copia {name} restaurada."
    except Exception as exc:
        _restore_caddyfile(old_caddyfile)
        restore(old_json, old_files)
        audit_log(user, "restore", name, False, str(exc))
        return False, f"No se pudo restaurar: {exc}"


def zip_snapshot(name: str) -> bytes | None:
    source = snapshot_dir(name)
    if source is None:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source.glob("*"):
            if path.is_file():
                zf.write(path, arcname=f"{name}/{path.name}")
    return buffer.getvalue()


# ---------------------------------------------------------------- auditoría

def audit_log(user: str, action: str, detail: str, ok: bool, error: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "action": action,
        "detail": detail,
        "ok": ok,
        "error": error,
    }
    CADDY_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with CADDY_AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200, user: str = "", action: str = "") -> list[dict[str, Any]]:
    if not CADDY_AUDIT.exists():
        return []
    entries: list[dict[str, Any]] = []
    with CADDY_AUDIT.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if user and user.lower() not in str(item.get("user", "")).lower():
                continue
            if action and action != str(item.get("action", "")):
                continue
            entries.append(item)
    return list(reversed(entries[-limit:]))


# ---------------------------------------------------------------- lectura

def caddy_version() -> str:
    result = run_caddy(["version"])
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else "desconocida"


def parse_sites() -> list[dict[str, str]]:
    sites: list[dict[str, str]] = []
    if not CADDY_CONFIG.exists():
        return sites
    pattern = re.compile(r"^([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?::(\d+))?\s*\{", re.IGNORECASE)
    for line in CADDY_CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line.strip())
        if match:
            sites.append({"host": match.group(1), "port": match.group(2) or "443"})
    return sites


def read_authelia_policy() -> dict[str, Any]:
    path = Path(os.getenv("AUTHELIA_CONFIG", "/authelia/configuration.yml"))
    info: dict[str, Any] = {"available": False, "default_policy": "", "rules": 0}
    if not path.exists():
        return info
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    info["available"] = True
    match = re.search(r"default_policy:\s*'?([a-z_]+)'?", text)
    if match:
        info["default_policy"] = match.group(1)
    info["rules"] = len(re.findall(r"-\s*domain:", text))
    return info
