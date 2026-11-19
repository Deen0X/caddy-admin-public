"""Hash de contraseñas (PBKDF2 stdlib), tokens de sesión, CSRF y rate limit."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
import threading
import time
from collections import deque
from typing import Deque

PBKDF2_ITERATIONS = 260_000
PBKDF2_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join(
        (
            PBKDF2_ALGO,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
        if algo != PBKDF2_ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return secrets.compare_digest(candidate, expected)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


class RateLimiter:
    """Límite de intentos por clave en una ventana deslizante (en memoria)."""

    def __init__(self, max_attempts: int = 6, window_seconds: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> Deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return hits

    def retry_after(self, key: str) -> int:
        with self._lock:
            now = time.time()
            hits = self._trim(key, now)
            if len(hits) >= self.max_attempts:
                return max(1, int(self.window - (now - hits[0])))
            return 0

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._trim(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset_user(self, username: str) -> int:
        """Elimina los intentos fallidos de un usuario, venga de la IP que venga."""
        username = (username or "").strip().lower()
        if not username:
            return 0
        suffix = f"|{username}"
        with self._lock:
            keys = [key for key in self._hits if key == username or key.endswith(suffix)]
            for key in keys:
                self._hits.pop(key, None)
            return len(keys)

    def user_retry_after(self, username: str) -> int:
        """Espera restante máxima para un usuario entre todas las IPs."""
        username = (username or "").strip().lower()
        if not username:
            return 0
        suffix = f"|{username}"
        now = time.time()
        wait = 0
        with self._lock:
            for key in list(self._hits):
                if key != username and not key.endswith(suffix):
                    continue
                hits = self._trim(key, now)
                if len(hits) >= self.max_attempts:
                    wait = max(wait, max(1, int(self.window - (now - hits[0]))))
        return wait


login_limiter = RateLimiter(max_attempts=6, window_seconds=900)
