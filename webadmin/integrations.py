"""Detección de integraciones opcionales (Authelia / LLDAP) con caché."""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

AUTHELIA_HEALTH = os.getenv("AUTHELIA_HEALTH_URL", "http://authelia:9091/api/health")
LLDAP_HOST = os.getenv("LLDAP_HOST", "lldap")
LLDAP_PORT = int(os.getenv("LLDAP_PORT", "3890"))
CACHE_SECONDS = int(os.getenv("SSO_CACHE_SECONDS", "30"))

_lock = threading.Lock()
_cache: dict[str, Any] = {"value": None, "at": 0.0, "ts": 0.0}


def _probe() -> dict[str, Any]:
    authelia = False
    lldap = False
    detail = ""

    try:
        with urllib.request.urlopen(AUTHELIA_HEALTH, timeout=2) as response:
            authelia = response.status == 200
    except Exception as exc:
        detail = str(exc)[:160]

    try:
        with socket.create_connection((LLDAP_HOST, LLDAP_PORT), timeout=2):
            lldap = True
    except Exception:
        pass

    return {
        "authelia": authelia,
        "lldap": lldap,
        "sso": authelia,
        "detail": detail,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def sso_status(force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _cache.get("value")
        if not force and cached is not None and now - _cache.get("at", 0) < CACHE_SECONDS:
            return cached
        value = _probe()
        _cache["value"] = value
        _cache["at"] = now
        return value
