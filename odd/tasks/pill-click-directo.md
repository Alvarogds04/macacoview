# Clic directo en el pill del panel

## Problema

Clic en el pill de GNOME Shell → abre un menú de una sola opción útil:

```text
extension.js:13   const GUI = '/home/alvaro/.local/bin/pc-ai-monitor-gnome'   ← ruta de usuario
extension.js:87   Gio.Subprocess.new([GUI], NONE)                             ← hereda env de gnome-shell
```

Dos defectos separados:

1. **Fricción de UI**: el menú tiene `Abrir PC-AI Monitor` + separador + `Actualizando…`. El tercero
   (`reactive: false`) no es una opción: es información. O sea, un clic para abrir una sola cosa.
2. **Bug de fondo**: gnome-shell no tiene `WAYLAND_DISPLAY` ni `DBUS_SESSION_BUS_ADDRESS` en su entorno,
   así que el hijo muere al nacer. El clic no abre la app y no se ve por qué.

## Decisiones

- **Clic izquierdo = abrir/traer al frente la app.** Clic derecho (o `superclick`) = menú. Menú minimalista
  con las acciones que existen, no con información decorativa.
- El arranque pasa por **systemd --user**, que sí corre con el entorno de la sesión de login. Es el mismo
  patrón que `~/.config/systemd/user/llama-*.service`.
- `pc-ai-monitor@alvaro` **debe pasar a versionarse en el repo** (`gui/gnome-extension/`) y ser instalada por
  `install.sh`. Hoy sólo existe en `~/.local/share/gnome-shell/extensions/`, sin copia en el repositorio.
- Ninguna ruta de usuario literal en código: resolver contra `$HOME`.

## Tareas

- [x] Copiar la extensión activa a `gui/gnome-extension/` (fuente de verdad en el repo).
- [x] Extensión: clic izq abre vía `systemctl --user start pc-ai-monitor-gnome.service`; menú sólo en
      clic der/secundario; sacar separador y `Actualizando…` del menú; status → `global.log`.
- [x] Añadir `gui/systemd/pc-ai-monitor-gnome.service` (`Type=simple`, `Restart=no`, `Environment=PYTHONPATH=`).
- [x] `install.sh`: instalar extensión (con `gnome-extensions install --force`), instalar y habilitar la
      unidad, y pasar `pc-ai-monitor-gnome` a rutas `$HOME`.
- [x] Verificar: `gjs -c` para sintaxis JS, `journalctl --user -u pc-ai-monitor-gnome` para el arranque.

## Verificación

- Sintaxis JS sin errores y extensión recargada sin fatal en `journalctl`.
- El pill sigue mostrando métricas (no romper lo que anda).
- `systemctl --user start pc-ai-monitor-gnome.service` levanta la app visible (prueba manual del usuario).

## Riesgos conocidos

- Recargar gnome-shell en Wayland **no** cierra la sesión; en X11 sí mataría las apps. Confirmar el
  compositor antes de recargar.
- Si el servicio ya está corriendo, `start` es no-op → la app queda en segundo plano. Mitigación: la app
  ya trae lógica de instancia única en `do_activate`; si no alcanza, usar `BusName=` + `Activate()`.

## Resultados

- Copia versionada en `gui/gnome-extension/` (antes s6lo exista en ~/.local/share).
- Extensin instalada por **copia de directorio**: `gnome-extensions install` slo
  acepta zip y `gnome-extensions pack` **segfaultea** en esta mquina.
- Unidad systemd verificada en la sesin real: `active (running)`, Main PID python3.
- `node --check` OK sobre extension.js; 14 tests de la app siguen en OK.
- `PcAiMonitorExtension` debe seguir llamandose as (lo usa el reload de shell).

## Falta hacer (accin manual del usuario)

- Recargar GNOME Shell: `Alt+F2` y despus `r`. Sin eso el clic izquierdo sigue
  abriendo el men viejo.
