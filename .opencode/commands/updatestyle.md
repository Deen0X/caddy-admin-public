---
description: Adapta la UI web del proyecto al sistema de diseño DNX Web UI (tema claro/oscuro/auto, sidebar/drawer, topbar sticky, componentes) sin cambiar funcionalidad.
---

1. **Guía de diseño (obligatoria):**
   - Lee `.opencode/skills/dnx-web-ui/SKILL.md`.
   - Ten a mano los assets de referencia:
     - `.opencode/skills/dnx-web-ui/assets/dnx-web-ui.css`
     - `.opencode/skills/dnx-web-ui/assets/index.html`
     - `.opencode/skills/dnx-web-ui/assets/theme-drawer.js`

2. **Inventario de la UI actual (solo lectura):**
   - Localiza los ficheros de interfaz web del proyecto (p. ej. `web/`, `static/`,
     `templates/`, `public/`, `src/**/web/`).
   - Identifica: shell/layout, navegación, header, tema, componentes y CSS.
   - Si `$ARGUMENTS` indica una ruta o sección, limita el trabajo a eso.

3. **Plan de adaptación:**
   - Compara lo existente con la guía y lista los cambios concretos.
   - **No inventar funcionalidad:** solo se cambia la capa visual y el comportamiento
     de UI; la lógica ya probada se conserva.
   - **No modificar** `.antigravity/Memory.txt`, `.antigravity/memory/*` ni las skills
     existentes: pueden contener información propia del proyecto que no se debe perder.
     Esta skill es aditiva y autónoma.

4. **Aplicación (incremental):**
   - Tokens en `:root` y `:root[data-theme]` (oscuro por defecto, claro, `auto` por media query).
   - Script anti-FOUC del tema antes del render + botón cíclico Auto → Oscuro → Claro.
   - Shell: sidebar sticky en escritorio; drawer con overlay en móvil (≤820px).
   - Topbar sticky; el LED de estado actúa como refresco (sin botón duplicado).
   - Componentes: card, stat, badge, dot/LED, btn, inputs, tabla, switch, modal, toast, avatar.
   - Responsive: `100dvh` + `env(safe-area-inset-*)`, `viewport-fit=cover`, `.col-hide`.
   - Reutiliza clases/estructura existentes cuando sea razonable; migra por pasos.

5. **Verificación:**
   - Ejecuta el build/test del proyecto.
   - Comprueba en escritorio y en ancho móvil (DevTools): tema, drawer, topbar fija,
     tablas con scroll horizontal y respeto del área segura.
   - Reporta ficheros modificados y decisiones de diseño.

6. **Regla de oro:** si la guía choca con la funcionalidad actual, prioriza NO romper
   funcionalidad; anota la diferencia y pregunta. El comando es idempotente: se puede
   volver a ejecutar para acercar el proyecto a la guía.
