# PC-AI Monitor

Monitor de escritorio para el stack local de IA: recursos de la maquina, consumo
de tokens, puertos y procesos.

## Estado real por plataforma

- **Linux — producto instalable.** La app GNOME es Python + GTK4/libadwaita: lo
  distribuible es el arbol de fuentes + `gui/install.sh`. No hay binario, y no
  hace falta: el instalador deja todo andando en `~/.local` sin sudo.
- **macOS — todavia no hay producto instalable.** El colector Rust (`src-tauri/`)
  compila y el frontend React tambien, pero no hay UI servida ni modelos. Los
  colectores leen `/proc`, que no existe en Mac. No prometemos instalacion en
  macOS hasta que eso cambie.

## Requisitos (Linux)

```bash
# Debian/Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adwaita-1

# Fedora
sudo dnf install python3-gobject python3-gobject-base gtk4 libadwaita
```

`python3-gi-cairo` es el que mas se olvida: sin ese puente la app abre y muestra
los datos, pero ningun grafico dibuja. `install.sh` verifica las tres
dependencias y avisa cual falta (la instalacion igual sigue).

## Instalar en una maquina nueva

### Opcion A: bajar una release (recomendado)

El repositorio es privado, asi que el download pide un token de GitHub de solo
lectura. El token se pasa por **variable de entorno**, nunca como argumento de
linea de comandos (quedaria en el historico de la shell):

```bash
# fine-grained PAT con Contents: read, o classic con scope repo
export PC_AI_GITHUB_TOKEN="github_pat_xxx"

curl -fsSL -H "Authorization: Bearer $PC_AI_GITHUB_TOKEN" \
  https://raw.githubusercontent.com/Alvarogds04/pc-ai-monitor/main/gui/install.sh \
  -o install.sh

bash install.sh --from-release          # ultima release
# bash install.sh --from-release v0.1.0 # o una version puntual
```

El modo `--from-release` detecta la plataforma, baja el artefacto del tag y su
checksum `.sha256`, **verifica el SHA256 antes de instalar nada**, y recien
entonces corre el instalador. Si el checksum no coincide, aborta.

### Opcion B: desde el codigo fuente

```bash
git clone https://github.com/Alvarogds04/pc-ai-monitor.git
cd pc-ai-monitor
bash gui/install.sh
```

### Que deja instalado

```text
~/.local/lib/pc-ai-monitor/pc_ai_monitor         la app
~/.local/bin/pc-ai-monitor-gnome                 lanzador
~/.local/bin/pc-ai-stats, pc-ai-tokens, pc-ai-bar   colectores
~/.local/share/applications/pc-ai-monitor-gnome.desktop
~/.local/share/gnome-shell/extensions/pc-ai-monitor@alvaro   pill del panel
~/.config/systemd/user/pc-ai-monitor-gnome.service
~/.config/pc-ai-monitor/config.toml              config (solo si no existe)
```

Todo sin sudo. Los pasos opcionales degradan: sin `gnome-extensions` se salta la
extension, sin sesion systemd la unidad queda instalada pero no arrancada, y sin
el helper root de AMD (`scripts/root/amdgpu-gem-info-read`, opcional) la memoria
de GPU sale en 0 con un aviso en vez de un cero mudo.

## Nota para macOS (preparada, no vigente)

Todavia **no** hay binario para macOS; esta nota queda escrita para cuando lo
haya. Un binario sin firmar que se baja de internet llega con el atributo de
cuarentena de Gatekeeper y macOS lo bloquea en la primera corrida. Se libera asi:

```bash
xattr -d com.apple.quarantine <binario>
```

## Releases

Empujar un tag `v*` dispara el workflow `release`, que empaqueta el arbol
instalable (`gui/` + `scripts/`), lo verifica instalando en un HOME limpio del
runner, y lo adjunta a la release de GitHub como
`pc-ai-monitor-<tag>-linux.tar.gz` + `.sha256`.
