"""Métricas de Caddy, comprobaciones de red/TLS y muestreo periódico a SQLite."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from db import add_sample, get_setting
from caddyops import load_routes

METRICS_URL = os.getenv("CADDY_METRICS_URL", "http://caddy:2019/metrics")
CADDY_HOST = os.getenv("CADDY_HOST", "localhost")
DATA_DIR = os.getenv("CADDY_ADMIN_DATA_DIR", "/data")

logger = logging.getLogger("caddy-admin.monitor")

_METRIC_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([0-9eE.+-]+)$")


def fetch_metrics(timeout: float = 3.0) -> dict[str, Any]:
    data = {
        "available": False,
        "requests": 0,
        "admin_requests": 0,
        "errors": 0,
        "error": "",
    }
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        data["error"] = str(exc)
        return data

    totals: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _METRIC_RE.match(line.strip())
        if not match:
            continue
        name, _labels, raw = match.groups()
        try:
            value = float(raw)
        except ValueError:
            continue
        totals[name] = totals.get(name, 0.0) + value

    data["available"] = True
    data["requests"] = int(
        totals.get("caddy_http_requests_total", 0)
        + totals.get("caddy_http_requests_in_flight", 0)
    )
    data["admin_requests"] = int(totals.get("caddy_admin_http_requests_total", 0))
    data["errors"] = int(
        totals.get("caddy_http_request_errors_total", 0)
        + totals.get("caddy_admin_http_request_errors_total", 0)
    )
    data["http_metrics"] = int(totals.get("caddy_http_requests_total", 0))
    return data


def disk_usage(path: str = DATA_DIR) -> dict[str, int]:
    try:
        usage = shutil.disk_usage(path)
        return {"free": usage.free, "total": usage.total, "used": usage.used}
    except OSError:
        return {"free": 0, "total": 0, "used": 0}


def tcp_check(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    started = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
        return {"ok": True, "ms": int((time.time() - started) * 1000), "error": ""}
    except Exception as exc:
        return {"ok": False, "ms": int((time.time() - started) * 1000), "error": str(exc)}


def cert_info(host: str = CADDY_HOST, port: int = 443, timeout: float = 5.0) -> dict[str, Any]:
    info: dict[str, Any] = {"ok": False, "days": None, "not_after": "", "issuer": "", "error": ""}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        not_after = cert.get("notAfter", "")
        info["not_after"] = not_after
        if not_after:
            expires = ssl.cert_time_to_seconds(not_after)
            info["days"] = int((expires - time.time()) // 86400)
        issuer = cert.get("issuer", ())
        for group in issuer:
            for key, value in group:
                if key == "commonName":
                    info["issuer"] = value
        info["ok"] = True
    except Exception as exc:
        info["error"] = str(exc)
    return info


def live_stats() -> dict[str, Any]:
    routes = []
    try:
        routes = load_routes()
    except Exception:
        pass
    metrics = fetch_metrics()
    disk = disk_usage()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "disk": disk,
        "routes_total": len(routes),
        "routes_enabled": sum(1 for r in routes if r.get("enabled")),
        "routes_authelia": sum(1 for r in routes if r.get("auth") == "authelia"),
        "routes_none": sum(1 for r in routes if r.get("auth") != "authelia"),
        "uptime_seconds": int(time.time() - _STARTED_AT),
    }


_STARTED_AT = time.time()


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="stats-sampler")

    def run(self) -> None:
        # Primera muestra a los pocos segundos de arrancar.
        time.sleep(5)
        while True:
            try:
                interval = int(get_setting("stats_interval", "300") or 300)
            except ValueError:
                interval = 300
            interval = max(60, min(interval, 3600))
            try:
                stats = live_stats()
                add_sample(
                    ts=stats["ts"],
                    requests=stats["metrics"].get("requests", 0),
                    admin_requests=stats["metrics"].get("admin_requests", 0),
                    errors=stats["metrics"].get("errors", 0),
                    routes_total=stats["routes_total"],
                    routes_enabled=stats["routes_enabled"],
                    disk_free=stats["disk"].get("free", 0),
                    disk_total=stats["disk"].get("total", 0),
                )
            except Exception:
                logger.exception("Fallo guardando muestra de estadisticas")
            time.sleep(interval)
