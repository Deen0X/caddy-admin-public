# DNX Web UI — Pack de estilo para consolas web

Este pack añade a un proyecto el **sistema de diseño DNX Web UI** ya probado en la
consola de TCloud: paleta claro/oscuro/auto, sidebar con drawer móvil, topbar fija,
cards, badges, tablas, switches, modales y toasts.

## Contenido

```
.opencode/skills/dnx-web-ui/
  SKILL.md                     Guía de diseño + checklist
  assets/dnx-web-ui.css        Implementación de referencia (tokens + componentes)
  assets/index.html            Esqueleto del shell
  assets/theme-drawer.js       Tema cíclico + drawer
.opencode/commands/updatestyle.md   Comando /updatestyle (OpenCode)
.agent/workflows/updatestyle.md     Workflow equivalente (Antigravity)
DNX_WEB_UI.md                  Este fichero
```

## Cómo usar

1. Descomprime el ZIP en la **raíz del proyecto**. Deben quedar las carpetas
   `.opencode/` y `.agent/` junto a las que ya existen.
2. Abre el proyecto con OpenCode (o Antigravity).
3. Ejecuta el comando:

   ```
   /updatestyle
   ```

   Opcionalmente puedes indicar un ámbito: `/updatestyle web/` o
   `/updatestyle panel de administración`.
4. El agente leerá la guía y adaptará la UI existente **sin cambiar la lógica**.
5. Revisa el resultado en escritorio y en ancho móvil (DevTools).

## Qué NO modifica

- `.antigravity/Memory.txt` ni `.antigravity/memory/*` (tu conocimiento propio se
  conserva intacto).
- Skills existentes del proyecto.
- Lógica de negocio, endpoints, datos o tests.

Solo se toca la **capa visual/UI**: HTML, CSS y el JS de comportamiento visual
(tema y drawer).

## Notas

- `assets/index.html` usa `logo.png` como placeholder: sustitúyelo por el logo real.
- `assets/dnx-web-ui.css` incluye clases del dominio TCloud (lanes/jobs); son
  opcionales y se pueden ignorar si el proyecto no las usa.
- El comando es **idempotente**: se puede volver a ejecutar para acercar más el
  proyecto a la guía.
