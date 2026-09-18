#!/usr/bin/env bash
# Installs the native GNOME app under ~/.local.
#
#   ~/.local/lib/pc-ai-monitor/pc_ai_monitor        the package
#   ~/.local/bin/pc-ai-monitor-gnome                launcher
#   ~/.local/share/applications/pc-ai-monitor-gnome.desktop
#   ~/.config/pc-ai-monitor/config.toml             config template (first run)
#
# It does not touch the Tauri app (pc-ai-monitor-gui / PC-AI Monitor), which
# stays installed until the migration is complete.
set -euo pipefail

# --- dependencias del sistema -------------------------------------------------
# GTK4 + libadwaita + el puente cairo de PyGObject. El puente es el que más se
# olvida: sin `python3-gi-cairo` la app abre pero ningún gráfico dibuja.
if ! python3 - <<'CHECK'
import sys

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_foreign("cairo")
except Exception:
    sys.exit(1)
CHECK
then
  echo "Faltan dependencias de Python. En Debian/Ubuntu:"
  echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adwaita-1"
  echo "En Fedora:"
  echo "  sudo dnf install python3-gobject python3-gobject-base gtk4 libadwaita"
  echo "(la app igual se instala; los gráficos quedan en blanco sin el puente cairo)"
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lib="$HOME/.local/lib/pc-ai-monitor"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"

rm -rf "$lib"
mkdir -p "$lib" "$bin" "$apps"
cp -r "$root/pc_ai_monitor" "$lib/"

cat > "$bin/pc-ai-monitor-gnome" <<LAUNCHER
#!/usr/bin/env bash
set -euo pipefail
exec env PYTHONPATH="$lib" python3 -m pc_ai_monitor "\$@"
LAUNCHER
chmod +x "$bin/pc-ai-monitor-gnome"

cat > "$apps/pc-ai-monitor-gnome.desktop" <<DESKTOP
[Desktop Entry]
Name=PC-AI Monitor
Comment=Monitor nativo de recursos, tokens, puertos y procesos
# systemctl arranca la unidad de usuario, que corre dentro de la sesion de
# login; llamar al lanzador directo deja a GTK sin WAYLAND_DISPLAY.
Exec=/usr/bin/systemctl --user start pc-ai-monitor-gnome.service
Icon=utilities-system-monitor
Terminal=false
Type=Application
Categories=System;Monitor;
StartupNotify=true
DESKTOP

# GNOME Shell extension: from the repo copy, never from a hand-edited install.
# `gnome-extensions install` wants a zip and `pack` segfaults on this stack, so
# the directory is copied into place -- which is what install would end up doing.
if command -v gnome-extensions >/dev/null 2>&1; then
  ext_dir="$HOME/.local/share/gnome-shell/extensions/pc-ai-monitor@alvaro"
  install -d "$ext_dir"
  cp -a "$root/gnome-extension/." "$ext_dir/"
  if [ "$(gnome-extensions info pc-ai-monitor@alvaro 2>/dev/null | awk -F': ' '/^State/{print $2}')" = "INITIALIZED" ]; then
    gnome-extensions enable pc-ai-monitor@alvaro
  fi
  echo "  extension copiada (recargar GNOME Shell para el cambio de clic)"
else
  echo "aviso: sin gnome-extensions, no se instal6 el pill del panel" >&2
fi

# Systemd user unit: la unica forma confiable de levantar la app desde un
# contexto sin entorno de sesion (la extension, o un atajo de teclado).
install -d "$HOME/.config/systemd/user"
install -m 644 "$root/systemd/pc-ai-monitor-gnome.service" \
  "$HOME/.config/systemd/user/pc-ai-monitor-gnome.service"
systemctl --user daemon-reload || true

# Config template, never overwriting an existing file.
PYTHONPATH="$lib" python3 -c \
  "from pc_ai_monitor.config import write_template; write_template()" || true

echo "Instalado:"
echo "  $lib"
echo "  $bin/pc-ai-monitor-gnome"
echo "  $apps/pc-ai-monitor-gnome.desktop"
echo "  extension pc-ai-monitor@alvaro"
echo "  systemd --user pc-ai-monitor-gnome.service"
echo "Config en ~/.config/pc-ai-monitor/config.toml"
