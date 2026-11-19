---
description: Sube un artefacto (APK, pack, captura) al canal de Telegram del usuario vía TCloud.
---

1. **Artefacto:**
   - Si `$ARGUMENTS` trae una ruta, usa esa.
   - Si viene vacío, usa el último build (`app/build/outputs/apk/debug/app-debug.apk` u
     equivalente del proyecto).
   - Si no existe, detente y dilo.

2. **Subida:**
   - Windows: `.\tools\upload-file.ps1 -File <ruta> -Notes "<resumen>"`
   - macOS/Linux: `bash tools/upload-file.sh <ruta> "<resumen>"`
   - Requiere `.tcloud.env`; si falta, pide los valores (nunca los inventes).

3. **Respuesta:**
   - Reporta `msg_id`, tamaño y enlace (`https://t.me/c/…`).
   - Si el servicio no responde, avisa y espera; no cambies URL ni token.
   - Nunca muestres el token.

Detalle completo en la skill `tcloud-delivery`.
