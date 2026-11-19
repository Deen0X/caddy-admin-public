---
name: dnx-web-ui
description: Aplica el sistema de diseño DNX Web UI al crear o modificar consolas web, paneles de administración, dashboards o SPA ligeras (paleta claro/oscuro/auto, sidebar con drawer móvil, topbar sticky, cards, badges, tablas, switches, modales). Use when creating HTML/CSS/JS admin UIs, when the user mentions "consola web", "panel de administración", "sidebar", "tema claro/oscuro/auto", "DNX Web UI" or wants the visual style of the TCloud console.
---

# DNX Web UI — Sistema de diseño para consolas web

> **Design Name:** DNX Web UI
> **Filosofía:** limpio, denso y legible (estilo Fluent suave, sin framework).
> **Base:** implementación real y probada de la consola de TCloud.
> **Uso:** agnóstico de proyecto; copia los tokens y patrones, no el dominio.

## 1. Principios

1. **Sin framework**: HTML + CSS + JS vanilla. Sin dependencias, sin build.
2. **Tokens primero**: todo color/medida sale de variables CSS (`:root`). Nunca
   colores en duro dentro de componentes.
3. **Tema en 3 estados**: `auto` (sigue al SO) / `oscuro` / `claro`, persistido y
   aplicado **antes** del primer render (sin parpadeo).
4. **Navegación**: sidebar fija en escritorio; en móvil se convierte en drawer
   deslizante que se abre con el icono de la app.
5. **Cabecera fija**: el topbar es `position: sticky`; el contenido pasa por debajo.
6. **Densidad**: tipografía 14px base, controles compactos, cards con borde 1px.
7. **Accesibilidad**: `aria-label`, `title` en botones-emoji, foco visible.

## 2. Tokens (variables CSS)

Definir en `:root` y `:root[data-theme="..."]`. Ver `assets/dnx-web-ui.css`.

### Oscuro (por defecto)
| Token | Valor | Uso |
|---|---|---|
| `--bg` | `#0b0f14` | Fondo de la app |
| `--panel` | `#121821` | Fondo de cards/sidebar |
| `--panel-2` | `#18202b` | Controles, hover, subpaneles |
| `--line` | `#263243` | Bordes y separadores |
| `--text` | `#e8eef6` | Texto principal |
| `--muted` | `#8b9bb0` | Texto secundario |
| `--accent` | `#3b82f6` | Acción/estado activo |
| `--accent-2` | `#1d4ed8` | Hover del acento |
| `--ok` | `#22c55e` | Éxito/encendido |
| `--warn` | `#f59e0b` | Aviso |
| `--err` | `#ef4444` | Error/peligro |
| `--radius` | `12px` | Radio de cards |
| `--shadow` | `0 6px 24px rgba(0,0,0,.28)` | Sombra de elevación |

### Claro
| Token | Valor |
|---|---|
| `--bg` | `#f4f7fb` |
| `--panel` | `#ffffff` |
| `--panel-2` | `#f8fafc` |
| `--line` | `#e2e8f0` |
| `--text` | `#0f172a` |
| `--muted` | `#64748b` |
| `--shadow` | `0 6px 20px rgba(15,23,42,.08)` |

> `auto` replica el bloque claro dentro de `@media (prefers-color-scheme: light)`.

## 3. Tipografía

- Fuente: `"Segoe UI", system-ui, -apple-system, sans-serif`.
- Base: `14px / 1.45`.
- `h1` 20px (título de sección), `h2` 15px (título de card).
- Etiquetas de tabla/eyebrow: 11.5px, mayúsculas, `letter-spacing:.05em`.
- Texto secundario: `.muted` + `.small` (12px).
- Mono: `"Cascadia Code", Consolas, monospace` para IDs, hashes y datos.

## 4. Estructura de la aplicación

```
.app (grid: 228px 1fr)
├─ aside.sidebar        ← sticky, 100vh (escritorio)
│  ├─ .brand            logo + nombre + versión
│  ├─ nav.nav           enlaces de sección
│  └─ .foot             usuario + logout + toggle de tema
└─ main.main
   ├─ .topbar           STICKY: menú móvil + título/subtítulo + LED + refresh
   └─ #view             contenido de la sección
```

- `.main`: `padding: 22px 26px 70px; max-width: 1400px`.
- El `.topbar` se pega arriba (`position: sticky; top:0; z-index:15`) con fondo
  `--bg` y sangrado (`margin: -22px -26px 18px; padding: 22px 26px 10px`).

Ver `assets/index.html` como esqueleto listo para copiar.

## 5. Tema Auto / Oscuro / Claro

- Se guarda en `localStorage` (p. ej. `tc_theme`).
- **Anti-FOUC**: script inline en `<head>` antes del CSS/render:
  ```html
  <script>
  (function(){var t=localStorage.getItem('tc_theme')||'auto';
  document.documentElement.setAttribute('data-theme',t);})();
  </script>
  ```
- El selector es un **botón cíclico**, no un `<select>`:
  `auto → oscuro → claro → auto`.
  - Iconos: `🌓` (auto), `🌙` (oscuro), `☀️` (claro).
  - `applyTheme()` actualiza `data-theme`, `localStorage`, icono y etiqueta.
- Atajo opcional: `Escape` cierra el drawer.

Ver `assets/theme-drawer.js`.

## 6. Componentes

| Componente | Clases | Notas |
|---|---|---|
| Card | `.card`, `.card-h` | radio 12, borde 1px, sombra; separación 14px |
| Stat | `.stat` (`.k/.v/.s`) | métrica: etiqueta, valor 22px, subtexto |
| Badge | `.badge` + `.ok/.warn/.err/.info` | píldoras de estado |
| Dot / LED | `.dot` + `.ok/.warn/.err/.off` | punto de estado; pulso opcional |
| Botón | `.btn`, `.btn.ghost`, `.btn.danger`, `.btn.sm`, `.btn.icon-btn` | 8x13 padding; emoji compacto |
| Input/Select | nativo estilizado | foco con outline azul translúcido |
| Tabla | `.table-wrap > table` | cabecera uppercase muted; hover de fila |
| Switch | `.switch > input + label` | 44x24, borde `--line`, activo `--ok` |
| Barra | `.bar > i` | progreso con gradiente de acento |
| Modal | `.modal > .modal-card` (+ `.modal-wide`) | overlay oscuro; botón cerrar |
| Toast | `#toast > div` (+ `.ok/.err`) | abajo-derecha, auto-oculta |
| Avatar | `.avatar` / `.user-av` (+ `.has-img`) | emoji, imagen o inicial |
| Toggle tema | `.theme-btn` | botón cíclico con icono + etiqueta |

## 7. Responsive (móvil ≤ 820px)

- La sidebar pasa a **drawer**: `position:fixed; width:264px; max-width:82vw;
  height:100dvh; transform:translateX(-100%)`; `.open` la muestra.
- Overlay `#drawer-overlay` (z-index inferior al drawer) cierra al tocar fuera.
- Botón `.menu-btn` (icono de app + `☰`) visible solo en móvil, dentro del topbar.
- Topbar en rejilla:
  ```
  "menu title led"
  ".    sub   sub"
  ```
  El subtítulo va debajo del título, a ancho completo y letra 12px.
- **Safe areas Android/iOS**: usar `100dvh` + `env(safe-area-inset-top/bottom)`
  en el drawer y en el padding inferior del `main`. `viewport-fit=cover` en el meta.
- Cerrar el drawer: overlay, clic fuera, `Escape` o al elegir sección.

## 8. Checklist al implementar

- [ ] Tokens en `:root` para oscuro y claro; `auto` por media query.
- [ ] Script anti-FOUC del tema antes del render.
- [ ] Sidebar sticky en escritorio; drawer + overlay + safe-area en móvil.
- [ ] Topbar sticky con fondo opaco y sangrado.
- [ ] Toggle de tema como botón cíclico con icono y etiqueta.
- [ ] Controles con foco visible y `title`/`aria-label`.
- [ ] Tablas con `.table-wrap` (scroll horizontal) y columnas `.col-hide` en móvil.
- [ ] Colores solo por variables; nada de hex sueltos en componentes.

## 9. Archivos de referencia (assets/)

- `assets/dnx-web-ui.css` — implementación completa de tokens + componentes
  (extraída de la consola TCloud; sirve como base o copia literal).
- `assets/index.html` — esqueleto HTML con head, anti-FOUC, shell, sidebar,
  topbar, drawer y overlay.
- `assets/theme-drawer.js` — lógica de tema cíclico, drawer y cierre por overlay
  / Escape / clic fuera.

> Copia los assets al proyecto destino y adapta nombres/clases de sección. No
> copies la lógica de negocio: esta skill es solo capa visual.
