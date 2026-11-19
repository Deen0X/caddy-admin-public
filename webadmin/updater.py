"""Deteccion de version del panel, estado de actualizacion y apply (GitHub + Docker)."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import client as http_client
from pathlib import Path
from typing import Any

REPO = os.getenv("CADDY_UPDATE_REPO", "Deen0X/caddy-admin-public")
CACHE_SECONDS = int(os.getenv("CADDY_UPDATE_CACHE_SECONDS", "3600"))
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
DOCKER_API_VERSION = os.getenv("DOCKER_API_VERSION", "v1.41")
CONTAINER_NAME = os.getenv("CADDY_CONTAINER", "dnx-caddy-admin")
IMAGE_TAG = os.getenv("CADDY_UPDATE_IMAGE", "dnxdservice/caddy-admin:1.0.0")
COMMIT_FILE = Path(os.getenv("CADDY_COMMIT_FILE", "/app/COMMIT"))
USER_AGENT = "dnx-caddy-admin-update"

_lock = threading.Lock()
_cache: dict[str, Any] = {"value": None, "at": 0.0}


class _UnixHTTPConnection(http_client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 60.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _headers(token: str = "") -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def docker_request(
    method: str,
    path: str,
    body: bytes | None = None,
    content_type: str | None = None,
    query: str = "",
    timeout: float = 60.0,
) -> tuple[int, Any]:
    conn = _UnixHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    headers: dict[str, str] = {"User-Agent": USER_AGENT}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if body is not None:
        headers["Content-Length"] = str(len(body))
    conn.request(method, f"/{DOCKER_API_VERSION}{path}{query}", body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    try:
        payload: Any = json.loads(data) if data else None
    except Exception:
        payload = data.decode("utf-8", "replace")
    return response.status, payload


def _tar_bytes(path: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(path, arcname=".")
    return buffer.getvalue()


def docker_build(tag: str, context_path: str, buildargs: dict[str, str] | None = None) -> str:
    data = _tar_bytes(context_path)
    query = f"?t={tag}"
    if buildargs:
        query += "&buildargs=" + urllib.parse.quote(json.dumps(buildargs))
    status, payload = docker_request(
        "POST",
        "/build",
        body=data,
        content_type="application/x-tar",
        query=query,
        timeout=900.0,
    )
    if status != 200:
        raise RuntimeError(f"docker build error: HTTP {status} {payload}")
    image_id: str | None = None
    lines: list[str] = []
    if isinstance(payload, dict) and "stream" in payload:
        lines.append(str(payload.get("stream") or ""))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and "stream" in item:
                lines.append(str(item.get("stream") or ""))
    elif isinstance(payload, str):
        lines = [line for line in payload.splitlines() if line.strip()]
    for line in lines:
        token = line.strip()
        if not token:
            continue
        try:
            obj = json.loads(token)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        aux = obj.get("aux") or {}
        if aux.get("ID"):
            image_id = str(aux["ID"])
        elif obj.get("id"):
            image_id = str(obj["id"])
    if not image_id:
        raise RuntimeError(f"docker build did not return image id: {payload}")
    return image_id


def docker_find_container(name: str) -> dict[str, Any] | None:
    status, payload = docker_request("GET", f"/containers/json?all=1&name={name}")
    if status != 200 or not isinstance(payload, list):
        return None
    for container in payload:
        names = container.get("Names") or []
        short = str(names[0] if names else container.get("Name") or "").lstrip("/")
        if short == name:
            return container
    return None


def docker_wait_healthy(container_id: str, timeout: float = 240.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = docker_request("GET", f"/containers/{container_id}/json")
        if status == 200 and isinstance(payload, dict):
            state = payload.get("State") or {}
            health = state.get("Health") or {}
            if health.get("Status") == "healthy":
                return True
            if state.get("Running") is False and state.get("Status") == "exited":
                return False
        time.sleep(5)
    return False


def _github_latest(token: str = "") -> str | None:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        tag = str(data.get("tag_name") or "").strip()
        if tag:
            return tag[1:] if tag[:1].lower() == "v" else tag
    except Exception:
        pass
    # Fallback: tags con formato de version (vX.Y.Z) si no hay releases.
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/tags?per_page=100",
            headers=_headers(token),
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            tags = json.load(response)
        best: str | None = None
        for item in tags if isinstance(tags, list) else []:
            name = str(item.get("name") or "").strip()
            if not re.fullmatch(r"v?\d+(\.\d+){1,3}", name):
                continue
            version = name[1:] if name[:1].lower() == "v" else name
            if best is None or _version_key(version) > _version_key(best):
                best = version
        return best
    except Exception:
        return None


def current_commit() -> str:
    override = os.getenv("CADDY_COMMIT", "").strip()
    if override:
        return override
    try:
        return COMMIT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _github_commit(token: str = "") -> tuple[dict[str, Any] | None, str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/master",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "GitHub devolvio 404: repo privado o inexistente. Configura un token."
        if exc.code == 403:
            return None, "GitHub limito las peticiones (403). Configura un token."
        return None, f"GitHub respondio HTTP {exc.code}."
    except Exception as exc:  # noqa: BLE001
        return None, f"No se pudo contactar con GitHub: {type(exc).__name__}."
    sha = str(data.get("sha") or "").strip()
    if not sha:
        return None, "GitHub no devolvio el commit de master."
    commit = data.get("commit") or {}
    committer = commit.get("committer") or {}
    message = str(commit.get("message") or "").splitlines()
    return (
        {
            "sha": sha,
            "short": sha[:7],
            "date": str(committer.get("date") or ""),
            "message": (message[0][:140] if message else ""),
        },
        "",
    )


def _version_key(value: str) -> tuple[int, ...]:
    text = str(value).strip().lstrip("vV")
    numbers: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        numbers.append(int(digits) if digits else 0)
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def _is_newer(latest: str, current: str) -> bool:
    try:
        return _version_key(latest) > _version_key(current)
    except Exception:
        return False


def update_status(current: str, force: bool = False, token: str = "") -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _cache.get("value")
        if not force and cached is not None and now - _cache.get("at", 0.0) < CACHE_SECONDS:
            return cached
    commit, detail = _github_commit(token)
    local_commit = current_commit()
    if commit is not None:
        status = {
            "current": current,
            "current_commit": local_commit[:7] if local_commit else "",
            "latest": "",
            "latest_commit": commit["short"],
            "latest_commit_full": commit["sha"],
            "latest_date": commit["date"],
            "latest_message": commit["message"],
            "update_available": bool(local_commit) and commit["sha"] != local_commit,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "mode": "commit",
            "detail": "",
        }
    else:
        latest = _github_latest(token)
        if latest is None:
            status = {
                "current": current,
                "current_commit": local_commit[:7] if local_commit else "",
                "latest": "",
                "latest_commit": "",
                "latest_commit_full": "",
                "latest_date": "",
                "latest_message": "",
                "update_available": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "mode": "unknown",
                "detail": detail or "No se pudo consultar GitHub.",
            }
        else:
            status = {
                "current": current,
                "current_commit": local_commit[:7] if local_commit else "",
                "latest": latest,
                "latest_commit": "",
                "latest_commit_full": "",
                "latest_date": "",
                "latest_message": "",
                "update_available": _is_newer(latest, current),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "mode": "version",
                "detail": "",
            }
    with _lock:
        _cache["value"] = status
        _cache["at"] = time.time()
    return status


def clear_update_cache() -> None:
    with _lock:
        _cache["value"] = None
        _cache["at"] = 0.0


def _download_tarball(ref: str, token: str = "") -> bytes:
    if token:
        api_url = f"https://api.github.com/repos/{REPO}/tarball/{ref}"
        request = urllib.request.Request(api_url, headers=_headers(token))
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else ""
            if exc.code in (301, 302, 303, 307, 308) and location:
                follow = urllib.request.Request(
                    location, headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(follow, timeout=300) as response:
                    return response.read()
            raise
    url = f"https://github.com/{REPO}/archive/{ref}.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def _spawn_helper(image_id: str) -> str:
    name = f"dnx-caddy-admin-updater-{int(time.time())}"
    body = {
        "Name": name,
        "Image": image_id,
        "Cmd": ["--recreate-image", image_id],
        "Env": [f"CADDY_CONTAINER={CONTAINER_NAME}"],
        "HostConfig": {
            "Mounts": [
                {"Type": "bind", "Source": DOCKER_SOCKET, "Target": DOCKER_SOCKET}
            ],
            "AutoRemove": True,
        },
    }
    status, created = docker_request(
        "POST",
        "/containers/create",
        body=json.dumps(body).encode(),
        content_type="application/json",
        query=f"?name={name}",
    )
    if status not in (200, 201):
        raise RuntimeError(f"docker helper create error: HTTP {status} {created}")
    helper_id = str((created or {}).get("Id") or "")
    start_status, _ = docker_request("POST", f"/containers/{helper_id}/start")
    if start_status not in (200, 204):
        raise RuntimeError(f"docker helper start error: HTTP {start_status}")
    return helper_id


def apply_update(current: str, token: str = "") -> dict[str, Any]:
    status = update_status(current, force=True, token=token)
    if not status.get("update_available"):
        return {"ok": False, "error": "no-update", "status": status}
    ref = str(status.get("latest_commit_full") or "")
    if not ref and status.get("latest"):
        ref = f"v{status.get('latest')}"
    if not ref:
        return {"ok": False, "error": "no-ref", "status": status}
    label = str(status.get("latest_commit") or status.get("latest") or "update")
    tarball = f"/tmp/caddy-admin-update-{label}.tar.gz"
    extract_dir = f"/tmp/caddy-admin-update-{label}"
    try:
        Path(tarball).write_bytes(_download_tarball(ref, token))
        shutil.rmtree(extract_dir, ignore_errors=True)
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(extract_dir, filter="data")
        context = Path(extract_dir) / "webadmin"
        if not context.is_dir():
            return {"ok": False, "error": "no-context", "status": status}
        image_id = docker_build(IMAGE_TAG, str(context), buildargs={"DNX_COMMIT": ref})
        helper_id = _spawn_helper(image_id)
        clear_update_cache()
        return {
            "ok": True,
            "latest": status.get("latest") or "",
            "latest_commit": status.get("latest_commit") or "",
            "image": image_id,
            "helper": helper_id,
            "message": "Actualizacion en marcha. La consola se reiniciara.",
        }
    except Exception as exc:  # noqa: BLE001
        clear_update_cache()
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "status": status,
        }


def _create_body(info: dict[str, Any], image_id: str) -> dict[str, Any]:
    config = info.get("Config") or {}
    host_config = dict(info.get("HostConfig") or {})
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    body: dict[str, Any] = {
        "Name": CONTAINER_NAME,
        "Image": image_id,
        "Env": list(config.get("Env") or []),
        "Cmd": list(config.get("Cmd") or []),
        "WorkingDir": str(config.get("WorkingDir") or "/app"),
        "HostConfig": host_config,
    }
    entrypoint = list(config.get("Entrypoint") or [])
    if entrypoint:
        body["Entrypoint"] = entrypoint
    if config.get("User"):
        body["User"] = str(config.get("User"))
    if config.get("Labels"):
        body["Labels"] = dict(config.get("Labels") or {})
    if config.get("ExposedPorts"):
        body["ExposedPorts"] = dict(config.get("ExposedPorts") or {})
    if networks:
        body["NetworkingConfig"] = {
            "EndpointsConfig": {name: {} for name in networks.keys()}
        }
    return body


def helper_recreate(image_id: str) -> int:
    """Se ejecuta en un contenedor auxiliar: para, borra y recrea la consola."""
    time.sleep(6)
    status, info = docker_request("GET", f"/containers/{CONTAINER_NAME}/json")
    if status != 200 or not isinstance(info, dict):
        print(f"No se pudo leer el contenedor {CONTAINER_NAME}: HTTP {status}")
        return 2
    container_id = str(info.get("Id") or "")
    stop_status, _ = docker_request("POST", f"/containers/{container_id}/stop?t=30")
    if stop_status not in (200, 204):
        print(f"docker stop error: HTTP {stop_status}")
        return 3
    rm_status, _ = docker_request("DELETE", f"/containers/{container_id}?force=1")
    if rm_status not in (200, 204):
        print(f"docker rm error: HTTP {rm_status}")
        return 4
    body = _create_body(info, image_id)
    create_status, created = docker_request(
        "POST",
        "/containers/create",
        body=json.dumps(body).encode(),
        content_type="application/json",
        query=f"?name={CONTAINER_NAME}",
    )
    if create_status not in (200, 201):
        print(f"docker create error: HTTP {create_status} {created}")
        return 5
    new_id = str((created or {}).get("Id") or "")
    start_status, _ = docker_request("POST", f"/containers/{new_id}/start")
    if start_status not in (200, 204):
        print(f"docker start error: HTTP {start_status}")
        return 6
    healthy = docker_wait_healthy(new_id)
    print(f"recreate {'ok' if healthy else 'sin health'} container={new_id}")
    return 0 if healthy else 7
