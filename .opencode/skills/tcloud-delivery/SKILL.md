---
name: tcloud-delivery
description: Entrega artefactos al móvil del usuario subiéndolos a su canal de Telegram con el servicio TCloud (APK debug, .sensepack, capturas, ZIP de evidencia). Use when a build or APK is generated, when the user asks to send/share/upload a file ("súbelo", "pásame el apk", "mándalo al móvil", "envíame"), when the user mentions TCloud or Telegram, or when working remotely and the user cannot access this machine.
---

# TCloud — entrega de artefactos al móvil

Servicio propio (vive en otro proyecto) que expone **Telegram como endpoint HTTP de subidas**.
Solo transporta bytes: no firma, no comprime, no analiza. Existe para que el usuario pueda
**probar los builds en el móvil cuando trabaja en remoto**, sin depender de un escritorio remoto.

## Requisitos

| Requisito | Detalle |
|---|---|
| Python 3 | En `PATH` (`python` en Windows, `python3` en macOS/Linux) |
| `.tcloud.env` | En la raíz del proyecto (ignorado por git) |
| Servicio | `TCLOUD_URL`, por defecto `http://TU_SERVIDOR:8931` |

```ini
TCLOUD_URL=http://TU_SERVIDOR:8931
TCLOUD_KEY=<token>
TCLOUD_CHAT=-100…            # canal destino
TCLOUD_TOPIC=2               # topic de APKs debug (0 = General)
TCLOUD_PROJECT=Mi App        # nombre que aparece en el caption
```

Si falta `.tcloud.env`, pide los valores al usuario; **nunca** los inventes ni los escribas en
el código, en un log o en un caption.

## Uso

Windows (PowerShell):

```powershell
.\tools\upload-file.ps1 -File app\build\outputs\apk\debug\app-debug.apk -Notes "resumen de cambios"
.\tools\upload-file.ps1 -File ruta\pack.sensepack -Notes "pack de pruebas"
.\tools\upload-file.ps1 -File app\build\outputs\apk\debug\app-debug.apk -Front   # salta la cola
```

macOS / Linux (bash):

```bash
bash tools/upload-file.sh app/build/outputs/apk/debug/app-debug.apk "resumen de cambios"
```

Directo al cliente (si el wrapper no existe):

```bash
set -a; . ./.tcloud.env; set +a
python3 tools/tcloud_upload.py <fichero> --caption "<project> | <versión> | <fecha hora> | <notas>"
```

## Caption

```
<proyecto> | <versión> (debug) | 2026-10-28 12:05 | <resumen breve>
<proyecto> | pack.sensepack | 2026-10-28 12:05 | <resumen breve>
```

Los wrappers lo generan solos: leen `versionName` de Gradle si el fichero es un APK y
`TCLOUD_PROJECT` para el nombre.

## Reglas

1. **Subir cada artefacto generado** que el usuario necesite probar, sin esperar a que lo pida
   (por defecto: cada APK debug al terminar el build).
2. Tras subir, **informar del `msg_id` y del enlace** (`https://t.me/c/…/2/…`).
3. **Streaming obligatorio**: el cliente lee en bloques de 1 MB. Nunca `read_bytes()` ni cargar
   el fichero entero en memoria.
4. **El token no se escribe nunca** en código, logs, captions, capturas ni commits.
5. Límites del servicio: **2 GB por fichero**, sin subida reanudable (si falla, job nuevo).
6. Errores:
   - `401/403` → parar y reportar (token o permisos).
   - `502` → reintentar **una vez** con un job nuevo.
   - `410` → cancelado; no reintentar en bucle.
   - Servicio caído (no responde `TCLOUD_URL`) → **avisar al usuario y esperar**. No cambiar la
     URL ni el token por cuenta propia.
7. Borrar los artefactos temporales después de subirlos si no son útiles en el repo.

## Qué subir

| Artefacto | Cuándo |
|---|---|
| APK debug | Cada build que el usuario vaya a probar |
| APK release / AAB | Solo si se pide o hay firma configurada |
| `.sensepack` / packs de skins | Cuando existan y el usuario quiera probarlos |
| Capturas / vídeos de pantalla | Para revisar UI en el móvil |
| ZIP de evidencia o logs anonimizados | Si el usuario lo pide (nunca con secretos) |

**No subir**: `.tcloud.env`, keystores, `local.properties`, tokens, datos personales sin anonimizar.

## Verificación

Salida esperada:

```
job <id> · 36034694 bytes · topic 2
OK msg_id=6 · 36034694 bytes
https://t.me/c/<chat_id>/<topic>/<msg_id>
```

Si no aparece `OK msg_id=…`, **no des la subida por buena**.

## Problemas frecuentes

| Síntoma | Causa / acción |
|---|---|
| `ERROR: faltan TCLOUD_KEY / --chat` | `.tcloud.env` incompleto o no cargado |
| Conexión rechazada en 8099 | Servicio parado → avisar y esperar |
| `401` / `403` | Token inválido o sin permiso en el canal → parar |
| `502` | Fallo del servicio → un reintento con job nuevo |
| `404` al consultar el job | Job caducado → volver a subir |
