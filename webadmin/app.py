"""DNX Caddy Admin — consola de gestión de rutas, seguridad y estadísticas.

Autenticación propia (SQLite + PBKDF2 + sesiones). El primer arranque pide crear
el administrador. `python app.py --reset-password-admin 'nueva'` lo resetea.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import ssl
import sys
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunsplit

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import caddyops
import db
import integrations
import monitor
import security
import updater

APP_VERSION = "2.1.0"
SESSION_COOKIE = "caddy_admin_session"
CSRF_COOKIE = "caddy_admin_csrf"
SORT_COOKIE = "caddy_admin_sort"
COOKIE_SECURE = os.getenv("CADDY_ADMIN_COOKIE_SECURE", "1") != "0"
MIN_PASSWORD = 4
FAVICON_MAX = 512 * 1024
FAVICON_TTL_OK = 86400
FAVICON_TTL_FAIL = 900
FAVICON_CACHE: dict[str, tuple[float, bytes | None, str]] = {}
FAVICON_VERSION = str(time.time_ns())

OK_MESSAGES = {
    "1": "Cambios guardados.",
    "2": "Usuario actualizado.",
    "3": "Usuario eliminado.",
    "4": "Espera de login reseteada.",
    "created": "Ruta creada y aplicada.",
    "updated": "Ruta actualizada y aplicada.",
    "deleted": "Ruta eliminada y aplicada.",
    "restored": "Copia restaurada y aplicada.",
    "domain": "Dominio aplicado y Caddy recargado.",
    "favicons": "Caché de favicons invalidada. Recargando iconos…",
    "update-settings": "Ajustes de actualización guardados.",
}

ERROR_MESSAGES = {
    "not-found": "La ruta no existe.",
    "noexiste": "El usuario no existe.",
    "usuario": "Usuario no válido (3-32, a-z 0-9 . _ -).",
    "ocupado": "Ese usuario ya existe.",
    "corta": f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.",
    "coincide": "Las contraseñas no coinciden.",
    "actual": "La contraseña actual no es correcta.",
    "propio": "No puedes desactivar ni cambiar tu propio rol de administrador.",
    "ultimo": "Debe quedar al menos un administrador activo.",
    "nombre": "El nombre de la instancia no es válido.",
    "sesion": "La duración de sesión debe estar entre 1 y 168 horas.",
    "intervalo": "El intervalo de muestreo debe estar entre 60 y 3600 segundos.",
    "url": "Las URLs deben empezar por http:// o https://.",
    "dominio": "El dominio no es válido. Ejemplo: micasa.duckdns.org",
    "pack": "En modo pack el Caddyfile lo gestiona el usuario.",
    "sso": "Authelia no está disponible para activar la autenticación.",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("caddy-admin")
templates = Jinja2Templates(directory="templates")

START_TS = time.time()
SECTIONS_ADMIN = [
    ("rutas", "Rutas", "⇄", "/"),
    ("directorio", "Directorio", "🗂", "/directorio"),
    ("directorio-admin", "Directorio admin", "🛠", "/directorio-admin"),
    ("ajustes", "Ajustes", "⚙", "/ajustes"),
    ("seguridad", "Seguridad", "🛡", "/seguridad"),
    ("usuarios", "Usuarios", "👥", "/usuarios"),
    ("estadisticas", "Estadísticas", "📊", "/estadisticas"),
    ("actividad", "Actividad", "🕘", "/actividad"),
    ("copias", "Copias", "💾", "/copias"),
    ("diagnostico", "Diagnóstico", "🩺", "/diagnostico"),
    ("update", "Update", "⬆", "/update"),
]

SECTIONS_USER = [
    ("directorio", "Directorio", "🗂", "/directorio"),
    ("seguridad", "Seguridad", "🛡", "/seguridad"),
    ("diagnostico", "Diagnóstico", "🩺", "/diagnostico"),
]


_auto_update_state: dict[str, str] = {"date": ""}


def _auto_update_loop() -> None:
    """Comprueba y aplica actualizaciones automaticamente segun los ajustes."""
    time.sleep(45)
    while True:
        try:
            settings = db.get_settings()
            if settings.get("update_auto") != "1":
                time.sleep(300)
                continue
            hour = int(settings.get("update_hour") or "4")
            now = datetime.now()
            today = now.date().isoformat()
            if now.hour == hour and _auto_update_state.get("date") != today:
                _auto_update_state["date"] = today
                token = settings.get("update_token", "")
                status = updater.update_status(APP_VERSION, force=True, token=token)
                if status.get("update_available"):
                    result = updater.apply_update(APP_VERSION, token=token)
                    caddyops.audit_log(
                        "auto-update",
                        "actualizacion",
                        f"auto latest={status.get('latest')}",
                        bool(result.get("ok")),
                        str(result.get("error") or ""),
                    )
                    logger.info("Auto-update: %s", result)
        except Exception:
            logger.exception("Fallo en el bucle de auto-update")
        time.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.purge_sessions()
    caddyops.CADDY_ROUTES_DIR.mkdir(parents=True, exist_ok=True)

    # Standalone: regenerar el Caddyfile cuando cambie la versión de la plantilla.
    if caddyops.managed_caddyfile():
        env_domain = os.getenv("CADDY_BASE_DOMAIN", "").strip()
        marker = "Generado por DNX Caddy Admin (v3)"
        try:
            existing = (
                caddyops.CADDY_CONFIG.read_text(encoding="utf-8")
                if caddyops.CADDY_CONFIG.exists()
                else ""
            )
        except OSError:
            existing = ""
        domain = env_domain or caddyops.current_domain()
        if domain and marker not in existing:
            ok, message = caddyops.apply_domain(
                domain,
                os.getenv("ACME_EMAIL", "").strip() or caddyops.current_email(),
                os.getenv("CADDY_LAN_IP", "").strip() or caddyops.current_lan_ip(),
                "startup",
            )
            if ok:
                db.set_setting("base_domain", domain)
                logger.info("Caddyfile regenerado al arrancar: %s", domain)
            else:
                logger.warning("No se pudo regenerar el Caddyfile: %s", message)

    try:
        routes = caddyops.load_routes()
        for attempt in range(5):
            ok, message = caddyops.apply_routes(routes, user="startup", audit=False)
            if ok:
                break
            logger.warning("Intento %s de aplicar rutas iniciales: %s", attempt + 1, message)
            time.sleep(2)
    except Exception:
        logger.exception("Fallo preparando las rutas iniciales")
    monitor.Sampler().start()
    threading.Thread(target=_auto_update_loop, daemon=True).start()
    yield


app = FastAPI(title="DNX Caddy Admin", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------- auth helpers

def session_hours() -> int:
    try:
        hours = int(db.get_setting("session_hours", "8") or 8)
    except ValueError:
        hours = 8
    return max(1, min(hours, 168))


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    row = db.get_session(security.token_hash(token))
    if row is None:
        return None
    if row["expires_at"] < db.now_iso() or not row["active"]:
        db.delete_session(row["token_hash"])
        return None
    return row


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        target = quote(str(request.url.path))
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={target}"})
    return user


def require_admin(request: Request):
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores.")
    return user


def login_user(response: Response, request: Request, user_id: int, username: str) -> None:
    token = security.new_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=session_hours())).isoformat()
    db.create_session(
        security.token_hash(token),
        int(user_id),
        expires,
        security.client_ip(request),
        request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=session_hours() * 3600,
    )
    db.touch_login(int(user_id))
    caddyops.audit_log(username, "login", security.client_ip(request), True, "")


def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def check_csrf(request: Request, token: str) -> None:
    expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=400, detail="Token CSRF invalido. Recarga la pagina.")


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
    section: str = "",
    user=None,
):
    settings = db.get_settings()
    token = csrf_token(request)
    active_user = user if user is not None else current_user(request)
    if active_user:
        sso = integrations.sso_status()
    else:
        sso = {"sso": False, "authelia": False, "lldap": False, "detail": "", "checked_at": ""}
    if active_user:
        sections = SECTIONS_ADMIN if active_user.get("role") == "admin" else SECTIONS_USER
    else:
        sections = []
    ctx: dict[str, Any] = {
        "request": request,
        "user": active_user,
        "settings": settings,
        "app_name": settings.get("instance_name", "Caddy Admin"),
        "theme_default": settings.get("theme_default", "auto"),
        "sections": sections,
        "section": section,
        "csrf": token,
        "version": APP_VERSION,
        "favicon_version": FAVICON_VERSION,
        "sso": sso,
        "base_domain": caddyops.current_domain(),
        "managed": caddyops.managed_caddyfile(),
        "allow_path": caddyops.managed_caddyfile(),
        "lan_ip": caddyops.current_lan_ip(),
        "acme_email": caddyops.current_email(),
        "update": (
            updater.update_status(APP_VERSION, token=db.get_setting("update_token", ""))
            if active_user and str(active_user.get("role") or "") == "admin"
            else None
        ),
    }
    if context:
        ctx.update(context)
    if ctx.get("ok"):
        ctx["ok"] = OK_MESSAGES.get(str(ctx["ok"]), str(ctx["ok"]))
    if ctx.get("error"):
        ctx["error"] = ERROR_MESSAGES.get(str(ctx["error"]), str(ctx["error"]))
    response = templates.TemplateResponse(template, ctx, status_code=status_code)
    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,
            secure=COOKIE_SECURE,
            samesite="lax",
            path="/",
            max_age=3600,
        )
    return response


def safe_next(value: str) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def add_query(url: str, **params: Any) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.update({key: str(value) for key, value in params.items()})
    return urlunsplit(("", "", parts.path or "/", urlencode(query), ""))


def valid_avatar(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc and len(value) <= 300:
            return value
        return ""
    return value[:8]


# ---------------------------------------------------------------- estáticos

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse("static/favicon.ico", media_type="image/vnd.microsoft.icon")


@app.get("/favicon-32.png", include_in_schema=False)
async def favicon_png():
    return FileResponse("static/favicon-32.png", media_type="image/png")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/favicon-16.png", include_in_schema=False)
async def favicon_16():
    return FileResponse("static/favicon-16.png", media_type="image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_icon():
    return FileResponse("static/apple-touch-icon.png", media_type="image/png")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _icon_score(tag: str, href: str) -> int:
    lower = tag.lower()
    href_lower = href.lower()
    score = 0
    if "apple-touch-icon" in lower:
        score += 1000
    if "mask-icon" in lower:
        score -= 500
    if href_lower.endswith(".svg") or "image/svg" in lower:
        score += 800
    sizes = re.search(r"sizes\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
    if sizes:
        for token in sizes.group(1).split():
            match = re.fullmatch(r"(\d+)x(\d+)", token.strip(), re.IGNORECASE)
            if match:
                score = max(score, int(match.group(1)))
    if "192" in href_lower or "512" in href_lower:
        score += 300
    return score


def _read_icon_links(html: str, base_url: str) -> list[str]:
    scored: list[tuple[int, str]] = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.IGNORECASE):
        if "icon" not in tag.lower():
            continue
        match = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not match:
            continue
        href = match.group(1).strip()
        if href.startswith("data:"):
            continue
        scored.append((_icon_score(tag, href), urljoin(base_url, href)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in scored]


def _fetch_image(url: str, context) -> tuple[bytes | None, str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "dnx-caddy-admin"})
        with urllib.request.urlopen(request, timeout=1.5, context=context) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None, ""
            data = response.read(FAVICON_MAX + 1)
            if len(data) > FAVICON_MAX:
                return None, ""
            return data, content_type
    except Exception:
        return None, ""


def _fetch_favicon(route: dict[str, Any]) -> tuple[bytes | None, str]:
    base = caddyops.backend_url(route)
    if not base:
        return None, ""
    context = ssl._create_unverified_context() if route.get("scheme", "http") == "https" else None

    override = str(route.get("favicon", "")).strip()
    if override:
        override_context = (
            ssl._create_unverified_context() if override.lower().startswith("https://") else None
        )
        data, icon_type = _fetch_image(override, override_context)
        if data:
            return data, icon_type

    # 1) Leer el HTML raíz y buscar <link rel="icon" href="...">
    try:
        request = urllib.request.Request(f"{base}/", headers={"User-Agent": "dnx-caddy-admin"})
        with urllib.request.urlopen(request, timeout=1.5, context=context) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type.startswith("text/html"):
                html = response.read(128 * 1024).decode("utf-8", errors="replace")
                for icon_url in _read_icon_links(html, f"{base}/"):
                    data, icon_type = _fetch_image(icon_url, context)
                    if data:
                        return data, icon_type
    except Exception:
        pass

    # 2) Rutas habituales
    candidates: list[str] = []
    if route.get("mode") == "path" and route.get("path"):
        candidates.append(f"{base}{route['path']}/favicon.ico")
    candidates.extend(
        [
            f"{base}/apple-touch-icon.png",
            f"{base}/favicon-192x192.png",
            f"{base}/favicon-512x512.png",
            f"{base}/favicon-32x32.png",
            f"{base}/favicon.ico",
            f"{base}/favicon.svg",
            f"{base}/favicon.png",
        ]
    )
    for url in candidates:
        data, icon_type = _fetch_image(url, context)
        if data:
            return data, icon_type
    return None, ""


def _fetch_description(route: dict[str, Any]) -> str:
    base = caddyops.backend_url(route)
    if not base:
        return ""
    context = ssl._create_unverified_context() if route.get("scheme", "http") == "https" else None
    try:
        request = urllib.request.Request(f"{base}/", headers={"User-Agent": "dnx-caddy-admin"})
        with urllib.request.urlopen(request, timeout=1.5, context=context) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("text/html"):
                return ""
            html = response.read(128 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return ""

    match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
            html,
            re.IGNORECASE,
        )
    text = match.group(1) if match else ""
    if not text:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            text = match.group(1)
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:200]


def visible_route_ids(user: dict[str, Any]) -> set[str] | None:
    if user.get("role") == "admin":
        return None
    ids = set(db.directory_items("common", 0))
    ids.update(db.directory_items("user", int(user["user_id"])))
    return ids


@app.get("/api/favicon/{route_id}")
def route_favicon(request: Request, route_id: str):
    user = require_user(request)
    if user["role"] != "admin":
        visible = visible_route_ids(user) or set()
        if route_id not in visible:
            raise HTTPException(status_code=404)
    now = time.time()
    cached = FAVICON_CACHE.get(route_id)
    if cached and now - cached[0] < (FAVICON_TTL_OK if cached[1] else FAVICON_TTL_FAIL):
        data, content_type = cached[1], cached[2]
    else:
        route = caddyops.find_route(caddyops.load_routes(), route_id)
        data, content_type = (_fetch_favicon(route) if route else (None, ""))
        FAVICON_CACHE[route_id] = (now, data, content_type)

    if data:
        return Response(
            content=data,
            media_type=content_type or "image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return FileResponse(
        "static/logo.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.post("/favicons/refresh")
async def favicons_refresh(request: Request, csrf: str = Form(...)):
    global FAVICON_VERSION
    user = require_admin(request)
    check_csrf(request, csrf)
    FAVICON_CACHE.clear()
    FAVICON_VERSION = str(time.time_ns())
    caddyops.audit_log(user["username"], "favicons", "refresh", True, "")
    return RedirectResponse("/?ok=favicons", status_code=303)

# ---------------------------------------------------------------- setup / login

@app.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request, error: str | None = None):
    if db.count_users() > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", {"error": error}, user=False)


@app.post("/setup", response_class=HTMLResponse)
async def setup_create(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
):
    check_csrf(request, csrf)
    if db.count_users() > 0:
        return RedirectResponse("/login", status_code=303)

    ip = security.client_ip(request)
    if not security.is_private_ip(ip):
        return render(
            request,
            "setup.html",
            {
                "error": "Por seguridad, crea el administrador desde la red local "
                "(la IP LAN del servidor).",
            },
            user=False,
            status_code=403,
        )

    username = username.strip().lower()
    if not (3 <= len(username) <= 32) or not all(c.isalnum() or c in "._-" for c in username):
        return render(request, "setup.html", {"error": "Usuario no válido (3-32, a-z 0-9 . _ -)."}, user=False)
    if len(password) < MIN_PASSWORD:
        return render(request, "setup.html", {"error": f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres."}, user=False)
    if password != confirm:
        return render(request, "setup.html", {"error": "Las contraseñas no coinciden."}, user=False)

    user_id = db.create_user(username, security.hash_password(password), "admin")
    caddyops.audit_log(username, "setup", ip, True, "")
    target = "/setup-stack" if caddyops.managed_caddyfile() and not caddyops.current_domain() else "/"
    response = RedirectResponse(target, status_code=303)
    login_user(response, request, user_id, username)
    return response


@app.get("/setup-stack", response_class=HTMLResponse)
async def setup_stack_form(request: Request, error: str | None = None, ok: str | None = None):
    user = require_admin(request)
    return render(request, "setup_stack.html", {"error": error, "ok": ok}, section="ajustes", user=user)


@app.post("/setup-stack", response_class=HTMLResponse)
async def setup_stack_apply(
    request: Request,
    csrf: str = Form(...),
    base_domain: str = Form(...),
    acme_email: str = Form(""),
    lan_ip: str = Form(""),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    if not caddyops.managed_caddyfile():
        return RedirectResponse("/ajustes?error=pack", status_code=303)

    domain = base_domain.strip().lower()
    email = acme_email.strip()
    lan = lan_ip.strip()
    if email and ("@" not in email or email.startswith("http")):
        return render(
            request,
            "setup_stack.html",
            {"error": "El email ACME no parece válido."},
            section="ajustes",
            user=user,
            status_code=400,
        )

    ok, message = caddyops.apply_domain(domain, email, lan, user["username"])
    if not ok:
        return render(
            request,
            "setup_stack.html",
            {"error": message},
            section="ajustes",
            user=user,
            status_code=400,
        )

    db.set_setting("base_domain", domain)
    db.set_setting("acme_email", email)
    db.set_setting("lan_ip", lan)
    return RedirectResponse("/?ok=domain", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/", error: str | None = None):
    if db.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    active = current_user(request)
    if active is not None:
        return RedirectResponse(
            "/" if active["role"] == "admin" else "/directorio", status_code=303
        )
    return render(request, "login.html", {"next": safe_next(next), "error": error}, user=False)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    check_csrf(request, csrf)
    username = username.strip().lower()
    ip = security.client_ip(request)
    key = f"{ip}|{username}"
    wait = security.login_limiter.retry_after(key)
    if wait:
        return render(
            request,
            "login.html",
            {"next": safe_next(next), "error": f"Demasiados intentos. Espera {wait}s."},
            status_code=429,
            user=False,
        )

    row = db.find_user_by_name(username)
    ok = bool(row) and bool(row["active"]) and security.verify_password(password, row["password_hash"])
    if not ok:
        security.login_limiter.register_failure(key)
        caddyops.audit_log(username or "?", "login", ip, False, "credenciales")
        return render(
            request,
            "login.html",
            {"next": safe_next(next), "error": "Usuario o contraseña incorrectos."},
            status_code=401,
            user=False,
        )

    security.login_limiter.reset(key)
    target = safe_next(next)
    if row["role"] != "admin" and target not in {"/directorio", "/seguridad", "/diagnostico"}:
        target = "/directorio"
    response = RedirectResponse(target, status_code=303)
    login_user(response, request, int(row["id"]), row["username"])
    return response


@app.post("/logout")
async def logout(request: Request, csrf: str = Form(...)):
    check_csrf(request, csrf)
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        db.delete_session(security.token_hash(token))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- rutas

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    ok: str | None = None,
    error: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    q: str | None = None,
):
    user = require_user(request)
    if user["role"] != "admin":
        return RedirectResponse("/directorio", status_code=303)
    all_routes = caddyops.load_routes()
    routes = all_routes

    query = (q or "").strip().lower()
    if query:
        routes = [
            route
            for route in routes
            if query in str(route.get("name", "")).lower()
            or query in str(route.get("id", "")).lower()
            or query in str(route.get("target", "")).lower()
            or query in str(route.get("port", ""))
        ]

    if not sort:
        raw = request.cookies.get(SORT_COOKIE, "")
        if "|" in raw:
            cookie_sort, cookie_order = raw.split("|", 1)
            if cookie_sort in {"name", "port", "auth", "sse", "enabled"}:
                sort, order = cookie_sort, cookie_order

    sort = sort if sort in {"name", "port", "auth", "sse", "enabled"} else "port"
    order = order if order == "desc" else "asc"

    def sort_key(item):
        value = item.get(sort, "")
        if isinstance(value, bool):
            return (0 if value else 1) if order == "asc" else (1 if value else 0)
        if sort == "port":
            return int(value) if str(value).isdigit() else 0
        return str(value).lower()

    routes = sorted(routes, key=sort_key, reverse=(order == "desc"))
    domain = caddyops.current_domain()
    public_urls = {
        route["id"]: caddyops.public_url(route, domain, all_routes) for route in routes
    }

    response = render(
        request,
        "index.html",
        {
            "routes": routes,
            "public_urls": public_urls,
            "port_min": caddyops.PORT_MIN,
            "port_max": caddyops.PORT_MAX,
            "ok": ok,
            "error": error,
            "sort": sort,
            "order": order,
            "q": q or "",
        },
        section="rutas",
        user=user,
    )
    response.set_cookie(
        SORT_COOKIE,
        f"{sort}|{order}",
        max_age=31536000,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


def route_form_context(
    routes: list[dict[str, Any]],
    route: dict[str, Any],
    is_new: bool,
    error: str | None,
    ignore_id: str | None = None,
) -> dict[str, Any]:
    return {
        "route": route,
        "is_new": is_new,
        "error": error,
        "port_min": caddyops.PORT_MIN,
        "port_max": caddyops.PORT_MAX,
        "free_ports": caddyops.free_ports(routes, ignore_id),
        "used_ports": sorted(caddyops.used_ports(routes, ignore_id)),
    }


@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, error: str | None = None):
    user = require_admin(request)
    routes = caddyops.load_routes()
    route = {
        "id": "",
        "name": "",
        "mode": "port",
        "port": caddyops.next_free_port(routes),
        "path": "",
        "host": "",
        "target": "",
        "description": "",
        "favicon": "",
        "auth": "authelia" if integrations.sso_status().get("sso") else "none",
        "scheme": "http",
        "skip_verify": False,
        "sse": False,
        "enabled": True,
    }
    return render(
        request,
        "form.html",
        route_form_context(routes, route, True, error),
        section="rutas",
        user=user,
    )


@app.post("/new", response_class=HTMLResponse)
async def create_route(
    request: Request,
    csrf: str = Form(...),
    name: str = Form(...),
    mode: str = Form("port"),
    port: int = Form(0),
    path: str = Form(""),
    host: str = Form(""),
    target: str = Form(...),
    description: str = Form(""),
    favicon: str = Form(""),
    auth: str = Form(...),
    scheme: str = Form("http"),
    skip_verify: bool = Form(False),
    sse: bool = Form(False),
    enabled: bool = Form(False),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    routes = caddyops.load_routes()
    route = {
        "id": caddyops.slugify(name),
        "name": name.strip(),
        "mode": mode,
        "port": port or caddyops.next_free_port(routes),
        "path": path.strip(),
        "host": host.strip(),
        "target": caddyops.normalize_target(target.strip()),
        "description": (description or "").strip()[:200],
        "favicon": (favicon or "").strip()[:500],
        "auth": auth,
        "scheme": scheme,
        "skip_verify": skip_verify,
        "sse": sse,
        "enabled": enabled,
    }
    if route["mode"] == "path" and not caddyops.managed_caddyfile():
        return render(
            request,
            "form.html",
            route_form_context(
                routes, route, True,
                "El modo path solo está disponible en standalone.",
                ignore_id=route["id"],
            ),
            section="rutas",
            user=user,
        )
    error = caddyops.validate_route(route, routes)
    if error:
        return render(
            request,
            "form.html",
            route_form_context(routes, route, True, error, ignore_id=route["id"]),
            section="rutas",
            user=user,
        )

    if not route.get("description"):
        route["description"] = _fetch_description(route)
    routes.append(route)
    ok, message = caddyops.apply_routes(routes, user["username"])
    if not ok:
        return render(
            request,
            "form.html",
            route_form_context(routes, route, True, message, ignore_id=route["id"]),
            section="rutas",
            user=user,
        )
    FAVICON_CACHE.clear()
    return RedirectResponse("/?ok=created", status_code=303)


@app.get("/{route_id}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, route_id: str, error: str | None = None):
    user = require_admin(request)
    routes = caddyops.load_routes()
    route = caddyops.find_route(routes, route_id)
    if route is None:
        return RedirectResponse("/?error=not-found", status_code=303)
    return render(
        request,
        "form.html",
        route_form_context(routes, route, False, error, ignore_id=route_id),
        section="rutas",
        user=user,
    )


@app.post("/{route_id}/edit", response_class=HTMLResponse)
async def update_route(
    request: Request,
    route_id: str,
    csrf: str = Form(...),
    name: str = Form(...),
    mode: str = Form("port"),
    port: int = Form(0),
    path: str = Form(""),
    host: str = Form(""),
    target: str = Form(...),
    description: str = Form(""),
    favicon: str = Form(""),
    auth: str = Form(...),
    scheme: str = Form("http"),
    skip_verify: bool = Form(False),
    sse: bool = Form(False),
    enabled: bool = Form(False),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    routes = caddyops.load_routes()
    route = caddyops.find_route(routes, route_id)
    if route is None:
        return RedirectResponse("/?error=not-found", status_code=303)

    updated = {
        "id": route_id,
        "name": name.strip(),
        "mode": mode,
        "port": port or caddyops.next_free_port(routes),
        "path": path.strip(),
        "host": host.strip(),
        "target": caddyops.normalize_target(target.strip()),
        "description": (description or "").strip()[:200],
        "favicon": (favicon or "").strip()[:500],
        "auth": auth,
        "scheme": scheme,
        "skip_verify": skip_verify,
        "sse": sse,
        "enabled": enabled,
    }
    if updated["mode"] == "path" and not caddyops.managed_caddyfile():
        return render(
            request,
            "form.html",
            route_form_context(
                routes, updated, False,
                "El modo path solo está disponible en standalone.",
                ignore_id=route_id,
            ),
            section="rutas",
            user=user,
        )
    error = caddyops.validate_route(updated, routes, ignore_id=route_id)
    if error:
        return render(
            request,
            "form.html",
            route_form_context(routes, updated, False, error, ignore_id=route_id),
            section="rutas",
            user=user,
        )

    if not updated.get("description"):
        updated["description"] = _fetch_description(updated)
    routes[routes.index(route)] = updated
    ok, message = caddyops.apply_routes(routes, user["username"])
    if not ok:
        return render(
            request,
            "form.html",
            route_form_context(routes, updated, False, message, ignore_id=route_id),
            section="rutas",
            user=user,
        )
    FAVICON_CACHE.clear()
    return RedirectResponse("/?ok=updated", status_code=303)


@app.post("/{route_id}/toggle-auth")
async def toggle_route_auth(
    request: Request,
    route_id: str,
    csrf: str = Form(...),
    next: str = Form("/"),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    routes = caddyops.load_routes()
    route = caddyops.find_route(routes, route_id)
    if route is None:
        return RedirectResponse(add_query("/", error="not-found"), status_code=303)
    new_auth = "none" if route.get("auth") == "authelia" else "authelia"
    if new_auth == "authelia" and not integrations.sso_status().get("sso"):
        return RedirectResponse(add_query(safe_next(next), error="sso"), status_code=303)
    updated = dict(route, auth=new_auth)
    routes[routes.index(route)] = updated
    ok, message = caddyops.apply_routes(routes, user["username"])
    if not ok:
        return RedirectResponse(add_query(safe_next(next), error=message), status_code=303)
    caddyops.audit_log(user["username"], "rutas", f"auth {route_id}={new_auth}", True, "")
    return RedirectResponse(add_query(safe_next(next), ok="updated"), status_code=303)


@app.post("/{route_id}/toggle-sse")
async def toggle_route_sse(
    request: Request,
    route_id: str,
    csrf: str = Form(...),
    next: str = Form("/"),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    routes = caddyops.load_routes()
    route = caddyops.find_route(routes, route_id)
    if route is None:
        return RedirectResponse(add_query("/", error="not-found"), status_code=303)
    updated = dict(route, sse=not bool(route.get("sse")))
    routes[routes.index(route)] = updated
    ok, message = caddyops.apply_routes(routes, user["username"])
    if not ok:
        return RedirectResponse(add_query(safe_next(next), error=message), status_code=303)
    caddyops.audit_log(
        user["username"], "rutas", f"sse {route_id}={updated['sse']}", True, ""
    )
    return RedirectResponse(add_query(safe_next(next), ok="updated"), status_code=303)


@app.post("/{route_id}/delete")
async def delete_route(request: Request, route_id: str, csrf: str = Form(...)):
    user = require_admin(request)
    check_csrf(request, csrf)
    routes = caddyops.load_routes()
    route = caddyops.find_route(routes, route_id)
    if route is None:
        return RedirectResponse("/?error=not-found", status_code=303)
    routes.remove(route)
    ok, message = caddyops.apply_routes(routes, user["username"])
    if not ok:
        return RedirectResponse(f"/?error={quote(message)}", status_code=303)
    db.delete_directory_route(route_id)
    FAVICON_CACHE.clear()
    return RedirectResponse("/?ok=deleted", status_code=303)


# ---------------------------------------------------------------- directorio

DIRECTORY_SORTS = {"name", "url", "auth", "status", "description"}


def _directory_target(user: dict[str, Any], as_user: str | None):
    if user.get("role") == "admin" and as_user:
        try:
            target = db.find_user(int(as_user))
        except (TypeError, ValueError):
            target = None
        if target:
            return target, True
    return user, False


def _directory_routes_for(
    target: dict[str, Any],
    preview: bool,
    routes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    routes = routes if routes is not None else caddyops.load_routes()
    if target.get("role") == "admin":
        return routes
    target_id = int(target.get("user_id") or target.get("id"))
    ordered = list(
        dict.fromkeys(
            db.directory_items("common", 0) + db.directory_items("user", target_id)
        )
    )
    by_id = {route["id"]: route for route in routes}
    return [by_id[route_id] for route_id in ordered if route_id in by_id]


@app.get("/directorio", response_class=HTMLResponse)
async def directorio(
    request: Request,
    ok: str | None = None,
    error: str | None = None,
    q: str | None = None,
    view: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    as_user: str | None = None,
):
    user = require_user(request)
    target, preview = _directory_target(user, as_user)
    target_id = int(target.get("user_id") or target.get("id"))
    prefs = db.get_user_prefs(target_id)

    view_mode = view if view in {"list", "icons"} else prefs.get("view", "list")
    if view_mode not in {"list", "icons"}:
        view_mode = "list"
    sort_field = sort if sort in DIRECTORY_SORTS else prefs.get("sort", "name")
    if sort_field not in DIRECTORY_SORTS:
        sort_field = "name"
    sort_order = order if order in {"asc", "desc"} else prefs.get("sort_order", "asc")
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"

    if not preview and (
        view in {"list", "icons"} or sort in DIRECTORY_SORTS or order in {"asc", "desc"}
    ):
        db.set_user_prefs(
            target_id, view=view_mode, sort=sort_field, sort_order=sort_order
        )

    domain = caddyops.current_domain()
    all_routes = caddyops.load_routes()
    items: list[dict[str, Any]] = []
    for route in _directory_routes_for(target, preview, all_routes):
        url = caddyops.public_url(route, domain, all_routes)
        items.append(
            {
                "route": route,
                "name": route.get("name", ""),
                "id": route.get("id", ""),
                "description": route.get("description", ""),
                "url": url,
                "auth": route.get("auth", "none"),
                "enabled": bool(route.get("enabled")),
                "available": bool(url) and bool(route.get("enabled")),
            }
        )

    query = (q or "").strip().lower()
    if query:
        items = [
            item
            for item in items
            if query in item["name"].lower()
            or query in item["id"].lower()
            or query in item["description"].lower()
            or query in item["url"].lower()
        ]

    def sort_key(item):
        if sort_field == "status":
            return 0 if item["enabled"] else 1
        if sort_field == "auth":
            return 0 if item["auth"] == "authelia" else 1
        return str(item.get(sort_field, "")).lower()

    items.sort(key=sort_key, reverse=(sort_order == "desc"))

    columns_raw = db.get_setting("directory_columns", "url,auth,status,description")
    columns = [
        column
        for column in columns_raw.split(",")
        if column in {"url", "auth", "status", "description"}
    ]

    return render(
        request,
        "directory.html",
        {
            "items": items,
            "view": view_mode,
            "sort": sort_field,
            "order": sort_order,
            "q": q or "",
            "columns": columns,
            "preview": preview,
            "preview_user": target.get("username", "") if preview else "",
            "as_user": target_id if preview else "",
            "is_admin": user.get("role") == "admin",
            "ok": ok,
            "error": error,
        },
        section="directorio",
        user=user,
    )


@app.get("/directorio-admin", response_class=HTMLResponse)
async def directorio_admin(
    request: Request,
    tab: str = "common",
    user_id: str = "",
    ok: str | None = None,
    error: str | None = None,
):
    user = require_admin(request)
    tab = tab if tab in {"common", "users", "settings"} else "common"
    users = db.list_users()
    target_user = None
    if tab == "users":
        if user_id.isdigit():
            target_user = db.find_user(int(user_id))
        if target_user is None or target_user["role"] == "admin":
            candidates = [item for item in users if item["role"] != "admin"]
            target_user = candidates[0] if candidates else None
    target_id = int(target_user["id"]) if target_user else 0
    scope = "user" if tab == "users" else "common"
    included_ids = db.directory_items(scope, target_id if scope == "user" else 0)
    routes = caddyops.load_routes()
    domain = caddyops.current_domain()
    route_map = {route["id"]: route for route in routes}
    included = [
        dict(route_map[route_id], url=caddyops.public_url(route_map[route_id], domain, routes))
        for route_id in included_ids
        if route_id in route_map
    ]
    available = [
        dict(route, url=caddyops.public_url(route, domain, routes))
        for route in routes
        if route["id"] not in included_ids
    ]
    settings = db.get_settings()
    columns = [
        column
        for column in settings.get("directory_columns", "").split(",")
        if column in {"url", "auth", "status", "description"}
    ]
    return render(
        request,
        "directory_admin.html",
        {
            "tab": tab,
            "users": users,
            "target_user": target_user,
            "target_id": target_id,
            "included": included,
            "available": available,
            "domain": caddyops.current_domain(),
            "columns": columns,
            "ok": ok,
            "error": error,
        },
        section="directorio-admin",
        user=user,
    )


@app.post("/directorio-admin/items")
async def directorio_admin_item(
    request: Request,
    csrf: str = Form(...),
    scope: str = Form(...),
    user_id: str = Form("0"),
    action: str = Form(...),
    route_id: str = Form(...),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    if scope not in {"common", "user"}:
        return RedirectResponse("/directorio-admin?error=scope", status_code=303)
    try:
        uid = 0 if scope == "common" else int(user_id or 0)
    except ValueError:
        uid = 0
    ids = db.directory_items(scope, uid)
    if action == "add" and route_id not in ids:
        ids.append(route_id)
    elif action == "remove" and route_id in ids:
        ids.remove(route_id)
    elif action in {"up", "down"} and route_id in ids:
        index = ids.index(route_id)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(ids):
            ids[index], ids[swap] = ids[swap], ids[index]
    db.replace_directory_items(scope, uid, ids)
    caddyops.audit_log(
        user["username"], "directorio", f"{scope}:{action}:{route_id}", True, ""
    )
    if scope == "common":
        target = "/directorio-admin?tab=common&ok=1"
    else:
        target = f"/directorio-admin?tab=users&user_id={uid}&ok=1"
    return RedirectResponse(target, status_code=303)


@app.post("/directorio-admin/settings")
async def directorio_admin_settings(
    request: Request,
    csrf: str = Form(...),
    view: str = Form("list"),
    columns: list[str] = Form([]),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    if view not in {"list", "icons"}:
        view = "list"
    allowed = {"url", "auth", "status", "description"}
    selected = [column for column in columns if column in allowed]
    db.set_setting("directory_view", view)
    db.set_setting("directory_columns", ",".join(selected))
    caddyops.audit_log(user["username"], "directorio", "settings", True, "")
    return RedirectResponse("/directorio-admin?tab=settings&ok=1", status_code=303)


# ---------------------------------------------------------------- ajustes

@app.get("/ajustes", response_class=HTMLResponse)
async def ajustes_form(request: Request, ok: str | None = None, error: str | None = None):
    user = require_admin(request)
    return render(
        request,
        "ajustes.html",
        {
            "ok": ok,
            "error": error,
            "caddy_version": caddyops.caddy_version(),
            "sites": caddyops.parse_sites(),
            "authelia": caddyops.read_authelia_policy(),
            "cert": monitor.cert_info(),
            "uptime": int(time.time() - START_TS),
        },
        section="ajustes",
        user=user,
    )


@app.post("/ajustes", response_class=HTMLResponse)
async def ajustes_save(
    request: Request,
    csrf: str = Form(...),
    instance_name: str = Form(...),
    theme_default: str = Form("auto"),
    session_hours: int = Form(8),
    stats_interval: int = Form(300),
    authelia_url: str = Form(""),
    lldap_url: str = Form(""),
    portainer_url: str = Form(""),
    base_domain: str = Form(""),
    acme_email: str = Form(""),
    lan_ip: str = Form(""),
):
    user = require_admin(request)
    check_csrf(request, csrf)

    if not (1 <= len(instance_name.strip()) <= 40):
        return RedirectResponse("/ajustes?error=nombre", status_code=303)
    if theme_default not in {"auto", "dark", "light"}:
        theme_default = "auto"
    if not 1 <= session_hours <= 168:
        return RedirectResponse("/ajustes?error=sesion", status_code=303)
    if not 60 <= stats_interval <= 3600:
        return RedirectResponse("/ajustes?error=intervalo", status_code=303)
    for value in (authelia_url, lldap_url, portainer_url):
        if value and not (value.startswith("http://") or value.startswith("https://")):
            return RedirectResponse("/ajustes?error=url", status_code=303)

    domain = base_domain.strip().lower()
    email = acme_email.strip()
    lan = lan_ip.strip()
    if domain and not re.fullmatch(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", domain):
        return RedirectResponse("/ajustes?error=dominio", status_code=303)

    if caddyops.managed_caddyfile():
        if not domain:
            return RedirectResponse("/ajustes?error=dominio", status_code=303)
        ok, message = caddyops.apply_domain(domain, email, lan, user["username"])
        if not ok:
            return RedirectResponse(f"/ajustes?error={quote(message)}", status_code=303)

    for key, value in {
        "instance_name": instance_name.strip(),
        "theme_default": theme_default,
        "session_hours": str(session_hours),
        "stats_interval": str(stats_interval),
        "authelia_url": authelia_url.strip(),
        "lldap_url": lldap_url.strip(),
        "portainer_url": portainer_url.strip(),
        "base_domain": domain,
        "acme_email": email,
        "lan_ip": lan,
    }.items():
        db.set_setting(key, value)

    caddyops.audit_log(user["username"], "ajustes", "guardados", True, "")
    return RedirectResponse("/ajustes?ok=1", status_code=303)


# ---------------------------------------------------------------- seguridad

@app.get("/seguridad", response_class=HTMLResponse)
async def seguridad_form(request: Request, ok: str | None = None, error: str | None = None):
    user = require_user(request)
    return render(
        request,
        "seguridad.html",
        {
            "ok": ok,
            "error": error,
            "sessions": db.list_sessions(int(user["user_id"])),
            "min_password": MIN_PASSWORD,
        },
        section="seguridad",
        user=user,
    )


@app.post("/seguridad/cuenta", response_class=HTMLResponse)
async def seguridad_cuenta(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(""),
    confirm: str = Form(""),
):
    user = require_user(request)
    check_csrf(request, csrf)
    row = db.find_user(int(user["user_id"]))
    if row is None or not security.verify_password(current_password, row["password_hash"]):
        return RedirectResponse("/seguridad?error=actual", status_code=303)

    username = username.strip().lower()
    if not (3 <= len(username) <= 32) or not all(c.isalnum() or c in "._-" for c in username):
        return RedirectResponse("/seguridad?error=usuario", status_code=303)
    other = db.find_user_by_name(username)
    if other is not None and int(other["id"]) != int(user["user_id"]):
        return RedirectResponse("/seguridad?error=ocupado", status_code=303)

    fields: dict[str, Any] = {"username": username}
    if new_password:
        if len(new_password) < MIN_PASSWORD:
            return RedirectResponse("/seguridad?error=corta", status_code=303)
        if new_password != confirm:
            return RedirectResponse("/seguridad?error=coincide", status_code=303)
        fields["password_hash"] = security.hash_password(new_password)

    db.update_user(int(user["user_id"]), **fields)
    caddyops.audit_log(username, "seguridad", "cuenta actualizada", True, "")
    return RedirectResponse("/seguridad?ok=1", status_code=303)


@app.post("/seguridad/sesiones")
async def seguridad_cerrar_sesiones(request: Request, csrf: str = Form(...)):
    user = require_user(request)
    check_csrf(request, csrf)
    db.delete_user_sessions(int(user["user_id"]))
    caddyops.audit_log(user["username"], "seguridad", "sesiones cerradas", True, "")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/perfil/avatar")
async def perfil_avatar(request: Request, csrf: str = Form(...), avatar: str = Form("")):
    user = require_user(request)
    check_csrf(request, csrf)
    db.update_user(int(user["user_id"]), avatar=valid_avatar(avatar))
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


# ---------------------------------------------------------------- usuarios

@app.get("/usuarios", response_class=HTMLResponse)
async def usuarios_list(request: Request, ok: str | None = None, error: str | None = None):
    user = require_admin(request)
    users = db.list_users()
    lockouts = {
        u["id"]: security.login_limiter.user_retry_after(u["username"]) for u in users
    }
    locked_users = [u for u in users if lockouts.get(u["id"])]
    return render(
        request,
        "usuarios.html",
        {
            "ok": ok,
            "error": error,
            "users": users,
            "lockouts": lockouts,
            "locked_users": locked_users,
            "min_password": MIN_PASSWORD,
            "is_admin": user["role"] == "admin",
        },
        section="usuarios",
        user=user,
    )


@app.post("/usuarios/new", response_class=HTMLResponse)
async def usuarios_new(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    avatar: str = Form(""),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    username = username.strip().lower()
    if not (3 <= len(username) <= 32) or not all(c.isalnum() or c in "._-" for c in username):
        return RedirectResponse("/usuarios?error=usuario", status_code=303)
    if db.find_user_by_name(username) is not None:
        return RedirectResponse("/usuarios?error=ocupado", status_code=303)
    if len(password) < MIN_PASSWORD:
        return RedirectResponse("/usuarios?error=corta", status_code=303)
    if role not in {"admin", "user"}:
        role = "user"
    db.create_user(username, security.hash_password(password), role, valid_avatar(avatar))
    caddyops.audit_log(user["username"], "usuarios", f"creado {username} ({role})", True, "")
    return RedirectResponse("/usuarios?ok=1", status_code=303)


@app.post("/usuarios/{user_id}/reset-login")
async def usuarios_reset_login(request: Request, user_id: int, csrf: str = Form(...)):
    admin = require_admin(request)
    check_csrf(request, csrf)
    target = db.find_user(user_id)
    if target is None:
        return RedirectResponse("/usuarios?error=noexiste", status_code=303)
    cleared = security.login_limiter.reset_user(target["username"])
    caddyops.audit_log(
        admin["username"], "usuarios", f"login-reset {target['username']} ({cleared})", True, ""
    )
    return RedirectResponse("/usuarios?ok=4", status_code=303)


@app.post("/usuarios/{user_id}/edit", response_class=HTMLResponse)
async def usuarios_edit(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
    role: str = Form("user"),
    avatar: str = Form(""),
    active: bool = Form(False),
    password: str = Form(""),
):
    admin = require_admin(request)
    check_csrf(request, csrf)
    target = db.find_user(user_id)
    if target is None:
        return RedirectResponse("/usuarios?error=noexiste", status_code=303)
    if role not in {"admin", "user"}:
        role = "user"

    # No dejar el sistema sin administradores activos.
    if int(target["id"]) == int(admin["user_id"]) and (role != "admin" or not active):
        return RedirectResponse("/usuarios?error=propio", status_code=303)
    if target["role"] == "admin" and target["active"] and (role != "admin" or not active):
        if db.count_admins() <= 1:
            return RedirectResponse("/usuarios?error=ultimo", status_code=303)

    fields: dict[str, Any] = {"role": role, "avatar": valid_avatar(avatar), "active": 1 if active else 0}
    if password:
        if len(password) < MIN_PASSWORD:
            return RedirectResponse("/usuarios?error=corta", status_code=303)
        fields["password_hash"] = security.hash_password(password)
        db.delete_user_sessions(int(target["id"]))
    db.update_user(int(target["id"]), **fields)
    caddyops.audit_log(admin["username"], "usuarios", f"editado {target['username']}", True, "")
    return RedirectResponse("/usuarios?ok=2", status_code=303)


@app.post("/usuarios/{user_id}/delete")
async def usuarios_delete(request: Request, user_id: int, csrf: str = Form(...)):
    admin = require_admin(request)
    check_csrf(request, csrf)
    target = db.find_user(user_id)
    if target is None:
        return RedirectResponse("/usuarios?error=noexiste", status_code=303)
    if int(target["id"]) == int(admin["user_id"]):
        return RedirectResponse("/usuarios?error=propio", status_code=303)
    if target["role"] == "admin" and target["active"] and db.count_admins() <= 1:
        return RedirectResponse("/usuarios?error=ultimo", status_code=303)
    db.delete_user(int(target["id"]))
    caddyops.audit_log(admin["username"], "usuarios", f"eliminado {target['username']}", True, "")
    return RedirectResponse("/usuarios?ok=3", status_code=303)


# ---------------------------------------------------------------- estadísticas

def stats_context() -> dict[str, Any]:
    live = monitor.live_stats()
    samples = list(reversed(db.list_samples(24)))
    return {"live": live, "samples": samples}


@app.get("/estadisticas", response_class=HTMLResponse)
async def estadisticas(request: Request, ok: str | None = None):
    user = require_admin(request)
    ctx = stats_context()
    ctx["ok"] = ok
    ctx["users_count"] = db.count_users()
    return render(request, "estadisticas.html", ctx, section="estadisticas", user=user)


@app.get("/api/stats")
async def api_stats(request: Request):
    require_admin(request)
    return JSONResponse(stats_context())


# ---------------------------------------------------------------- actualizacion

@app.get("/update", response_class=HTMLResponse)
async def update_page(request: Request, ok: str | None = None, error: str | None = None):
    user = require_admin(request)
    token = db.get_setting("update_token", "")
    status = updater.update_status(APP_VERSION, token=token)
    return render(
        request,
        "update.html",
        {
            "ok": ok,
            "error": error,
            "update_status": status,
            "update_auto": db.get_setting("update_auto", "0") == "1",
            "update_hour": db.get_setting("update_hour", "4"),
            "update_token_set": bool(token),
            "repo": updater.REPO,
        },
        section="update",
        user=user,
    )


@app.post("/update/settings")
async def update_settings(
    request: Request,
    csrf: str = Form(...),
    auto: str = Form("0"),
    hour: int = Form(4),
    token: str = Form(""),
    token_clear: str = Form(""),
):
    user = require_admin(request)
    check_csrf(request, csrf)
    db.set_setting("update_auto", "1" if auto == "1" else "0")
    db.set_setting("update_hour", str(max(0, min(23, hour))))
    if token_clear == "1":
        db.set_setting("update_token", "")
    elif token.strip():
        db.set_setting("update_token", token.strip()[:200])
    updater.clear_update_cache()
    caddyops.audit_log(user["username"], "actualizacion", "settings", True, "")
    return RedirectResponse("/update?ok=update-settings", status_code=303)


@app.get("/api/update")
async def api_update(request: Request, force: bool = False):
    require_admin(request)
    status = updater.update_status(
        APP_VERSION, force=force, token=db.get_setting("update_token", "")
    )
    return JSONResponse(status)


@app.post("/api/update/check")
async def api_update_check(request: Request):
    require_admin(request)
    status = updater.update_status(
        APP_VERSION, force=True, token=db.get_setting("update_token", "")
    )
    return JSONResponse(status)


@app.post("/api/update/apply")
def api_update_apply(request: Request):
    user = require_admin(request)
    check_csrf(request, request.cookies.get("caddy_admin_csrf") or "")
    result = updater.apply_update(APP_VERSION, token=db.get_setting("update_token", ""))
    caddyops.audit_log(
        user["username"],
        "actualizacion",
        f"apply latest={result.get('latest')}",
        bool(result.get("ok")),
        str(result.get("error") or ""),
    )
    return JSONResponse(result)


# ---------------------------------------------------------------- actividad

@app.get("/actividad", response_class=HTMLResponse)
async def actividad(request: Request, user: str = "", action: str = "", ok: str | None = None):
    current = require_admin(request)
    entries = caddyops.read_audit(limit=300, user=user, action=action)
    return render(
        request,
        "actividad.html",
        {"entries": entries, "filter_user": user, "filter_action": action, "ok": ok},
        section="actividad",
        user=current,
    )


# ---------------------------------------------------------------- copias

@app.get("/copias", response_class=HTMLResponse)
async def copias(request: Request, ok: str | None = None, error: str | None = None):
    current = require_admin(request)
    return render(
        request,
        "copias.html",
        {"snapshots": caddyops.list_snapshots(), "ok": ok, "error": error},
        section="copias",
        user=current,
    )


@app.post("/copias/{name}/restore")
async def copias_restore(request: Request, name: str, csrf: str = Form(...)):
    current = require_admin(request)
    check_csrf(request, csrf)
    ok, message = caddyops.restore_snapshot(name, current["username"])
    if not ok:
        return RedirectResponse(f"/copias?error={quote(message)}", status_code=303)
    db.prune_directory_items({route["id"] for route in caddyops.load_routes()})
    FAVICON_CACHE.clear()
    return RedirectResponse("/copias?ok=1", status_code=303)


@app.get("/copias/{name}/download")
async def copias_download(request: Request, name: str):
    require_admin(request)
    data = caddyops.zip_snapshot(name)
    if data is None:
        raise HTTPException(status_code=404, detail="Copia no encontrada")
    headers = {"Content-Disposition": f'attachment; filename="caddy-backup-{name}.zip"'}
    return Response(content=data, media_type="application/zip", headers=headers)


# ---------------------------------------------------------------- diagnóstico

@app.get("/diagnostico", response_class=HTMLResponse)
async def diagnostico(request: Request):
    user = require_user(request)
    is_admin = user.get("role") == "admin"
    visible = None if is_admin else visible_route_ids(user)
    domain = caddyops.current_domain()

    routes = caddyops.load_routes()
    all_routes = routes
    if visible is not None:
        routes = [route for route in routes if route.get("id") in visible]

    checks = []
    try:
        for route in routes:
            hostport = caddyops.backend_hostport(route)
            if hostport is None:
                continue
            host, port = hostport
            result = monitor.tcp_check(host, port)
            checks.append(
                {
                    "route": route,
                    "check": result,
                    "url": caddyops.public_url(route, domain, all_routes),
                }
            )
    except Exception:
        logger.exception("Fallo comprobando destinos")

    if is_admin:
        validation = caddyops.run_caddy(
            ["validate", "--config", str(caddyops.CADDY_CONFIG), "--adapter", "caddyfile"]
        )
        validation_ok = validation.returncode == 0
        validation_output = caddyops.output_of(validation)
        caddy_version = caddyops.caddy_version()
        cert = monitor.cert_info()
        disk = monitor.disk_usage()
        metrics = monitor.fetch_metrics()
        writable = True
        try:
            probe = caddyops.CADDY_ROUTES_DIR / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            writable = False
    else:
        validation_ok = True
        validation_output = ""
        caddy_version = ""
        cert = {"ok": False, "days": None, "not_after": "", "issuer": "", "error": ""}
        disk = {"free": 0, "total": 0, "used": 0}
        metrics = {
            "available": False,
            "requests": 0,
            "admin_requests": 0,
            "errors": 0,
            "error": "",
        }
        writable = True

    return render(
        request,
        "diagnostico.html",
        {
            "is_admin": is_admin,
            "validation_ok": validation_ok,
            "validation_output": validation_output,
            "caddy_version": caddy_version,
            "cert": cert,
            "checks": checks,
            "disk": disk,
            "writable": writable,
            "metrics": metrics,
            "uptime": int(time.time() - START_TS),
        },
        section="diagnostico",
        user=user,
    )


# ---------------------------------------------------------------- CLI

def reset_admin_password(password: str, username: str = "") -> int:
    db.init_db()
    if len(password) < MIN_PASSWORD:
        print(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.", file=sys.stderr)
        return 2

    target = db.find_user_by_name(username) if username else None
    if target is None:
        admins = [u for u in db.list_users() if u["role"] == "admin"]
        target = admins[0] if admins else None

    if target is not None:
        db.update_user(int(target["id"]), password_hash=security.hash_password(password), active=1)
        db.delete_user_sessions(int(target["id"]))
        print(f"Contraseña actualizada para '{target['username']}'.")
        return 0

    new_name = username or "admin"
    if db.find_user_by_name(new_name) is not None:
        print(f"El usuario '{new_name}' ya existe.", file=sys.stderr)
        return 2
    db.create_user(new_name, security.hash_password(password), "admin")
    print(f"Administrador '{new_name}' creado.")
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(description="DNX Caddy Admin")
    parser.add_argument("--serve", action="store_true", help="Arranca el servidor (por defecto)")
    parser.add_argument("--reset-password-admin", metavar="PASSWORD", help="Resetea la contraseña del administrador")
    parser.add_argument("--user", default="", help="Usuario objetivo del reset")
    parser.add_argument("--recreate-image", metavar="IMAGE_ID", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.recreate_image:
        return updater.helper_recreate(args.recreate_image)

    if args.reset_password_admin:
        return reset_admin_password(args.reset_password_admin, args.user)

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "9095")),
        workers=1,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
