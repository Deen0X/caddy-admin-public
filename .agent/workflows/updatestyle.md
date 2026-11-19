---
description: Adapta la UI web del proyecto al sistema de diseño DNX Web UI (tema claro/oscuro/auto, sidebar/drawer, topbar sticky, componentes) sin cambiar funcionalidad.
---

1.  **Guía de diseño (obligatoria):**
    *   Lee `.opencode/skills/dnx-web-ui/SKILL.md`.
    *   Assets de referencia: `assets/dnx-web-ui.css`, `assets/index.html`,
        `assets/theme-drawer.js`.

2.  **Inventario de la UI actual (solo lectura):**
    *   Localiza los ficheros de interfaz web (`web/`, `static/`, `templates/`,
        `public/`, `src/**/web/`).
    *   Identifica shell/layout, navegación, header, tema, componentes y CSS.
    *   Si el usuario indica una ruta o sección, limita el trabajo a eso.

3.  **Plan de adaptación:**
    *   Lista los cambios concretos comparando con la guía.
    *   **No inventar funcionalidad:** solo capa visual y comportamiento de UI.
    *   **No modificar** `.antigravity/Memory.txt`, `.antigravity/memory/*` ni skills
        existentes (pueden tener información propia que no se debe perder).

4.  **Aplicación (incremental):**
    *   Tokens `:root` / `:root[data-theme]` (oscuro, claro, auto).
    *   Anti-FOUC del tema + botón cíclico Auto → Oscuro → Claro.
    *   Sidebar sticky (escritorio) y drawer con overlay (móvil ≤820px).
    *   Topbar fija (sticky); el LED de estado actúa como refresco.
    *   Componentes: card, stat, badge, dot/LED, btn, inputs, tabla, switch, modal,
        toast, avatar.
    *   Responsive: `100dvh` + `env(safe-area-inset-*)`, `viewport-fit=cover`.

5.  **Verificación:**
    *   Ejecuta build/test del proyecto.
    *   Revisa escritorio y móvil (DevTools): tema, drawer, topbar, tablas y safe-area.
    *   Reporta ficheros modificados y decisiones.

6.  **Regla de oro:** no romper funcionalidad; si la guía choca con lo actual, anota la
    diferencia y pregunta. El comando es idempotente.
