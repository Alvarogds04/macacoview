#!/usr/bin/env bash
# Installs the native GNOME app under ~/.local.
#
# Two modes:
#   bash install.sh                        install from a checkout (this file's
#                                          directory must sit next to ../scripts)
#   bash install.sh --from-release [TAG]   download the release artifact for TAG
#                                          (default: latest), verify its SHA256
#                                          and only then install from it
#
#   ~/.local/lib/pc-ai-monitor/pc_ai_monitor        the package
#   ~/.local/bin/pc-ai-monitor-gnome                launcher
#   ~/.local/share/applications/pc-ai-monitor-gnome.desktop
#   ~/.config/macacoview/config.toml             config template (first run)
#
# It does not touch the Tauri app (pc-ai-monitor-gui / PC-AI Monitor), which
# stays installed until the migration is complete.
set -euo pipefail

# --- modo bajar-y-correr -------------------------------------------------------
# Para una maquina nueva sin el repositorio: baja el artefacto de la release,
# verifica el SHA256 y recien despues instala. El repositorio es privado, asi
# que hace falta un token de GitHub de solo lectura, leido del entorno
# (PC_AI_GITHUB_TOKEN, luego GH_TOKEN, luego GITHUB_TOKEN) -- nunca por argumento
# de linea de comandos, que quedaria en el historico de la shell.
if [ "${1:-}" = "--from-release" ]; then
  if [ $# -ge 2 ]; then
    tag="$2"
  else
    tag="latest"
  fi

  case "$(uname -s)" in
    Linux) ;;
    Darwin)
      # Honestidad antes que promesas: hoy no hay producto instalable en macOS.
      # El colector Rust compila y el frontend React tambien, pero no hay UI
      # servida ni modelos, y los colectores leen /proc. El artefacto de las
      # releases empaqueta la app Linux (Python + GTK4).
      cat >&2 <<'MAC'
Todavia no hay producto instalable para macOS: el artefacto de las releases es
la app Linux (Python + GTK4). El colector Rust compila en Mac, pero no hay UI
servida ni modelos.

Cuando exista un binario para Mac sin firmar, macOS lo marcara con cuarentena y
habra que liberarlo antes de la primera corrida:
  xattr -d com.apple.quarantine <binario>
MAC
      exit 1
      ;;
    *)
      echo "plataforma no soportada: $(uname -s) (solo Linux hoy)" >&2
      exit 1
      ;;
  esac

  repo="${PC_AI_RELEASE_REPO:-Alvarogds04/pc-ai-monitor}"
  token="${PC_AI_GITHUB_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
  # El repositorio es PUBLICO, asi que la descarga funciona sin credenciales.
  # El token queda opcional: sube el limite de la API y sigue sirviendo si
  # apuntas PC_AI_RELEASE_REPO a un fork privado.
  auth=()
  [ -n "$token" ] && auth=("${auth[@]}")
  wgetauth=()
  [ -n "$token" ] && wgetauth=("${wgetauth[@]}")

  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "necesito curl o wget para bajar la release" >&2
    exit 1
  fi

  # gh_get <url>                cuerpo por stdout (API, JSON)
  # gh_download <url> <destino> binario a archivo
  # Con curl, el header Authorization se suelta solo al seguir el redirect
  # hacia el storage de la release (curl >= 7.58), que es lo que hace falta.
  gh_get() {
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL \
        "${auth[@]}" \
        -H "Accept: application/vnd.github+json" "$1"
    else
      wget -q "${wgetauth[@]}" \
        --header="Accept: application/vnd.github+json" -O - "$1"
    fi
  }
  # Los assets de una release privada no se bajan por la URL web: github.com/...
  # /releases/download/ responde 404 aunque el token sea valido. Se bajan por la
  # API, que exige el id del asset y Accept: application/octet-stream.
  gh_download() {
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "${auth[@]}" \
        -H "Accept: application/octet-stream" -o "$2" "$1"
    else
      wget -q "${wgetauth[@]}" \
        --header="Accept: application/octet-stream" -O "$2" "$1"
    fi
  }

  api="https://api.github.com/repos/$repo"
  if [ "$tag" = "latest" ]; then
    echo "Buscando la ultima release de $repo..."
    tag="$(gh_get "$api/releases/latest" | python3 -c '
import json, sys
print(json.load(sys.stdin)["tag_name"])
')"
  fi

  tarball="pc-ai-monitor-$tag-linux.tar.gz"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  release_json="$(gh_get "$api/releases/tags/$tag")"
  asset_id() {
    printf '%s' "$release_json" | python3 -c '
import json, sys
name = sys.argv[1]
for asset in json.load(sys.stdin).get("assets", []):
    if asset["name"] == name:
        print(asset["id"])
        break
' "$1"
  }
  api_asset="https://api.github.com/repos/$repo/releases/assets"

  echo "Bajando $tarball..."
  for file in "$tarball" "$tarball.sha256"; do
    id="$(asset_id "$file")"
    [ -n "$id" ] || { echo "no encontre $file en la release $tag" >&2; exit 1; }
    gh_download "$api_asset/$id" "$tmp/$file"
  done

  echo "Verificando SHA256..."
  ( cd "$tmp" && sha256sum -c "$tarball.sha256" )

  echo "Instalando desde $tag..."
  tar -xzf "$tmp/$tarball" -C "$tmp"
  bash "$tmp/pc-ai-monitor-$tag/gui/install.sh"
  exit 0
fi

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

# The collectors: the app shells out to these, so an install without them opens
# with empty dashboards. They live in ../scripts, not in this directory.
for tool in pc-ai-stats pc-ai-tokens pc-ai-bar; do
  install -m 755 "$root/../scripts/$tool" "$bin/$tool"
done

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

# AMD GPUs read GTT through a root-owned helper. Without it the app degrades to
# 0 rather than failing, but the number is the reason the pill is useful, so we
# say what is missing instead of showing a silent zero. Installing it needs
# root, which this script does not assume:
#   sudo install -m 755 scripts/root/amdgpu-gem-info-read /usr/local/bin/
#   echo "$USER ALL=(root) NOPASSWD: /usr/local/bin/amdgpu-gem-info-read" | sudo tee /etc/sudoers.d/pc-ai-monitor
if ! sudo -n /usr/local/bin/amdgpu-gem-info-read >/dev/null 2>&1; then
  echo "aviso: sin amdgpu-gem-info-read, la memoria de GPU AMD saldra en 0" >&2
fi

# Config template, never overwriting an existing file.
PYTHONPATH="$lib" python3 -c \
  "from pc_ai_monitor.config import write_template; write_template()" || true

echo "Instalado:"
echo "  $lib"
echo "  $bin/pc-ai-monitor-gnome"
echo "  $bin/pc-ai-stats, pc-ai-tokens, pc-ai-bar"
echo "  $apps/pc-ai-monitor-gnome.desktop"
echo "  extension pc-ai-monitor@alvaro"
echo "  systemd --user pc-ai-monitor-gnome.service"
echo "Config en ~/.config/macacoview/config.toml"
