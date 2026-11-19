# Plan de implementación — Modo Directorio

> Fecha: 2026-11-17. Estado: aprobado e implementado.
> Sustituye al rol `viewer` por `user` (directorio).

## 1. Objetivo

Que un usuario sin conocimientos técnicos abra un único enlace y vea sus servicios
(favicon, nombre, descripción, URL) y pueda entrar con un clic, sin recordar rutas ni puertos.

## 2. Roles

| Rol | Acceso |
|---|---|
| `admin` | Todo el panel + gestión del directorio + preview por usuario |
| `user` | Directorio, Seguridad (avatar + contraseña), Diagnóstico (solo sus elementos) |

- Migración: `UPDATE users SET role='user' WHERE role='viewer'`.
- Nuevos usuarios: rol `user` por defecto.

## 3. Modelo de datos

```sql
directory_items(
  id INTEGER PK,
  scope TEXT,            -- 'common' | 'user'
  user_id INTEGER,       -- 0 = común; >0 = usuario
  route_id TEXT,
  position INTEGER,
  UNIQUE(scope, user_id, route_id)
)

user_prefs(
  user_id INTEGER PK,
  view TEXT,             -- 'list' | 'icons'
  sort TEXT,             -- name|url|auth|status|description
  sort_order TEXT        -- asc|desc
)
```

- El usuario ve **unión** de comunes + sus extras (sin duplicados; comunes primero).
- El admin en `/directorio` ve **todos** los elementos; con `?as=<user_id>` previsualiza la vista de un usuario.
- Columnas globales en settings: `directory_columns` = `["url","auth","status","description"]`.
- Vista por defecto en settings: `directory_view` = `list`.

## 4. Rutas: descripción

- Nuevo campo `description` (máx. 200) en `routes.json` y en el formulario.
- Si se guarda vacío, se intenta extraer de la web: `<meta name="description">` o `<title>`.
- Editable a mano; se muestra en el directorio.

## 5. Experiencia

### Usuario
- **Directorio** (su home): lista o cuadrícula, buscador, orden por columnas, persistente.
  - Lista: fila grande, favicon 32, nombre, descripción, URL, auth, estado. Toda la fila clicable.
  - Cuadrícula: tarjetas con favicon 48–64, nombre, descripción, estado.
  - Rutas desactivadas: badge **No disponible**, no clicables.
- **Seguridad**: solo avatar y cambio de contraseña.
- **Diagnóstico**: sus servicios con favicon, URL pública y estado (responde/no, latencia). Sin target interno.

### Admin
- **Directorio**: todos los elementos; selector “ver como usuario”.
- **Directorio admin**:
  - Vista común: añadir/quitar/ordenar rutas.
  - Por usuario: extras + orden.
  - Ajustes: vista por defecto y columnas.
  - Preview de cada usuario.
- **Diagnóstico** completo + favicon por ruta.
- Resto de secciones sin cambios.

## 6. Seguridad

- `user` no accede a Rutas, Ajustes, Usuarios, Actividad, Copias, Estadísticas ni Directorio admin.
- `/api/favicon/{id}`: un `user` solo puede pedir favicons de rutas asignadas.
- Diagnóstico de usuario: filtra rutas y oculta targets.
- El directorio nunca muestra `target`.

## 7. Fases

| Fase | Contenido |
|---|---|
| 1 | DB (directory_items, user_prefs), migración de rol, helpers |
| 2 | Rol `user`, navegación, guards, landing por rol |
| 3 | Directorio usuario (lista/cuadrícula, orden, buscador) |
| 4 | Directorio admin (común, extras, orden, ajustes, preview) |
| 5 | Descripción de rutas + autocompletado desde la web |
| 6 | Seguridad usuario y diagnóstico con favicons/filtrado |
| 7 | Despliegue, pruebas y documentación |

## 8. Criterios de aceptación

- Un `user` solo ve Directorio, Seguridad y Diagnóstico.
- Ve únicamente comunes + sus extras; no ve targets ni rutas ajenas.
- Lista y cuadrícula comparten orden; el orden y la vista persisten.
- El admin gestiona comunes/extras y previsualiza cualquier usuario.
- Rutas desactivadas aparecen como “No disponible”.
- Favicons funcionan en directorio y diagnóstico.
- Los administradores conservan todas las funciones actuales.
