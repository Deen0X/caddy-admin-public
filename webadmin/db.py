"""Persistencia SQLite de la consola (usuarios, sesiones, ajustes y muestras).

La base vive fuera de la imagen: CADDY_ADMIN_DB (por defecto /data/admin.db).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(os.getenv("CADDY_ADMIN_DB", "/data/admin.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    avatar        TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stats_samples (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    requests       INTEGER NOT NULL DEFAULT 0,
    admin_requests INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    routes_total   INTEGER NOT NULL DEFAULT 0,
    routes_enabled INTEGER NOT NULL DEFAULT 0,
    disk_free      INTEGER NOT NULL DEFAULT 0,
    disk_total     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON stats_samples(ts);

CREATE TABLE IF NOT EXISTS directory_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scope    TEXT NOT NULL,
    user_id  INTEGER NOT NULL DEFAULT 0,
    route_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE(scope, user_id, route_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    view       TEXT NOT NULL DEFAULT 'list',
    sort       TEXT NOT NULL DEFAULT 'name',
    sort_order TEXT NOT NULL DEFAULT 'asc'
);

CREATE INDEX IF NOT EXISTS idx_directory_scope ON directory_items(scope, user_id, position);
"""

DEFAULT_SETTINGS = {
    "instance_name": "Caddy Admin",
    "theme_default": "auto",
    "session_hours": "8",
    "stats_interval": "300",
    "authelia_url": "",
    "lldap_url": "",
    "portainer_url": "",
    "base_domain": "",
    "acme_email": "",
    "lan_ip": "",
    "directory_view": "list",
    "directory_columns": "url,auth,status,description",
    "update_auto": "0",
    "update_hour": "4",
    "update_token": "",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params))]


def one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with tx() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        # Migracion: el antiguo rol viewer pasa a user (directorio).
        conn.execute("UPDATE users SET role = 'user' WHERE role = 'viewer'")


# ---------------------------------------------------------------- ajustes

def get_settings() -> dict[str, str]:
    return {row["key"]: row["value"] for row in query("SELECT key, value FROM settings")}


def get_setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------- usuarios

def count_users() -> int:
    row = one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def count_admins() -> int:
    row = one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1")
    return int(row["n"]) if row else 0


def list_users() -> list[sqlite3.Row]:
    return query("SELECT * FROM users ORDER BY username COLLATE NOCASE")


def find_user_by_name(username: str) -> sqlite3.Row | None:
    return one("SELECT * FROM users WHERE username = ?", (username,))


def find_user(user_id: int) -> sqlite3.Row | None:
    return one("SELECT * FROM users WHERE id = ?", (user_id,))


def create_user(username: str, password_hash: str, role: str = "user", avatar: str = "") -> int:
    stamp = now_iso()
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, role, avatar, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (username, password_hash, role, avatar, stamp, stamp),
        )
        return int(cur.lastrowid)


def update_user(user_id: int, **fields: Any) -> None:
    allowed = {"username", "password_hash", "role", "avatar", "active"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = now_iso()
    clause = ", ".join(f"{k} = ?" for k in sets)
    with tx() as conn:
        conn.execute(f"UPDATE users SET {clause} WHERE id = ?", (*sets.values(), user_id))


def delete_user(user_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def touch_login(user_id: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user_id))


# ---------------------------------------------------------------- sesiones

def create_session(token_hash: str, user_id: int, expires_at: str, ip: str, user_agent: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash, user_id, now_iso(), expires_at, ip[:64], user_agent[:300]),
        )


def get_session(token_hash: str) -> sqlite3.Row | None:
    return one(
        "SELECT s.token_hash, s.expires_at, u.id AS user_id, u.username, u.role, u.avatar, u.active "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (token_hash,),
    )


def delete_session(token_hash: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_user_sessions(user_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_sessions() -> None:
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))


def list_sessions(user_id: int | None = None) -> list[sqlite3.Row]:
    if user_id is None:
        return query(
            "SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
            "ORDER BY s.created_at DESC"
        )
    return query(
        "SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.user_id = ? ORDER BY s.created_at DESC",
        (user_id,),
    )


# ---------------------------------------------------------------- directorio

def directory_items(scope: str, user_id: int = 0) -> list[str]:
    rows = query(
        "SELECT route_id FROM directory_items WHERE scope = ? AND user_id = ? "
        "ORDER BY position, id",
        (scope, user_id),
    )
    return [row["route_id"] for row in rows]


def replace_directory_items(scope: str, user_id: int, route_ids: list[str]) -> None:
    with tx() as conn:
        conn.execute(
            "DELETE FROM directory_items WHERE scope = ? AND user_id = ?", (scope, user_id)
        )
        for index, route_id in enumerate(route_ids):
            conn.execute(
                "INSERT INTO directory_items(scope, user_id, route_id, position) "
                "VALUES (?, ?, ?, ?)",
                (scope, user_id, route_id, index),
            )


def delete_directory_route(route_id: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM directory_items WHERE route_id = ?", (route_id,))


def prune_directory_items(route_ids: set[str]) -> None:
    with tx() as conn:
        rows = conn.execute("SELECT DISTINCT route_id FROM directory_items").fetchall()
        stale = [row["route_id"] for row in rows if row["route_id"] not in route_ids]
        for route_id in stale:
            conn.execute("DELETE FROM directory_items WHERE route_id = ?", (route_id,))


def get_user_prefs(user_id: int) -> dict[str, Any]:
    row = one("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))
    if row:
        return row
    return {
        "user_id": user_id,
        "view": get_setting("directory_view", "list"),
        "sort": "name",
        "sort_order": "asc",
    }


def set_user_prefs(user_id: int, **fields: Any) -> None:
    allowed = {"view", "sort", "sort_order"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    columns = ", ".join(values.keys())
    placeholders = ", ".join("?" for _ in values)
    updates = ", ".join(f"{key} = excluded.{key}" for key in values)
    with tx() as conn:
        conn.execute(
            f"INSERT INTO user_prefs(user_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}",
            (user_id, *values.values()),
        )


# ---------------------------------------------------------------- muestras

def add_sample(**data: Any) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO stats_samples(ts, requests, admin_requests, errors, routes_total, "
            "routes_enabled, disk_free, disk_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data.get("ts") or now_iso(),
                int(data.get("requests", 0)),
                int(data.get("admin_requests", 0)),
                int(data.get("errors", 0)),
                int(data.get("routes_total", 0)),
                int(data.get("routes_enabled", 0)),
                int(data.get("disk_free", 0)),
                int(data.get("disk_total", 0)),
            ),
        )


def list_samples(limit: int = 60) -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM stats_samples ORDER BY id DESC LIMIT ?", (limit,)
    )
