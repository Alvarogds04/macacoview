# PC-AI Monitor

Monitor de escritorio para el stack local de IA: recursos de la maquina, consumo
de tokens, puertos y procesos.

## Estado real por plataforma

- **Linux — producto instalable.** La app GNOME es Python + GTK4/libadwaita: lo
  distribuible es el arbol de fuentes + `gui/install.sh`. No hay binario, y no
  hace falta: el instalador deja todo andando en `~/.local` sin sudo.
- **macOS — daemon descargable.** El producto es el binario `macacoview-serve`:
  un solo archivo sin dependencias (no Node, no Python, no GTK) que sirve el
  mismo tablero en `http://127.0.0.1:8787` con el frontend React incrustado.
  Va sin firmar: Gatekeeper lo frena la primera vez (ver la seccion de macOS).
  No hay app GNOME ni instalacion en `~/.local` de ese lado.

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

El repositorio es publico, asi que no hace falta ninguna credencial para bajar
ni la release de macOS ni el instalador:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Alvarogds04/macacoview/main/gui/install.sh \
  -o install.sh

bash install.sh --from-release          # ultima release
# bash install.sh --from-release v0.2.0-rc2 # o una version puntual
```

El modo `--from-release` detecta la plataforma, baja el artefacto del tag y su
checksum `.sha256`, **verifica el SHA256 antes de instalar nada**, y recien
entonces corre el instalador. Si el checksum no coincide, aborta.

### Opcion B: desde el codigo fuente

```bash
git clone https://github.com/Alvarogds04/macacoview.git
cd macacoview
bash gui/install.sh
```

### Que deja instalado

```text
~/.local/lib/macacoview/pc_ai_monitor            la app
~/.local/bin/macacoview                          lanzador
~/.local/bin/macacoview-stats, macacoview-tokens, macacoview-bar   colectores
~/.local/share/applications/macacoview.desktop
~/.local/share/gnome-shell/extensions/macacoview@alvaro   pill del panel
~/.config/systemd/user/macacoview-gnome.service
~/.config/macacoview/config.toml                 config (solo si no existe)
```

Todo sin sudo. Los pasos opcionales degradan: sin `gnome-extensions` se salta la
extension, sin sesion systemd la unidad queda instalada pero no arrancada, y sin
el helper root de AMD (`scripts/root/amdgpu-gem-info-read`, opcional) la memoria
de GPU sale en 0 con un aviso en vez de un cero mudo.

## macOS: bajar y correr el daemon

En Mac el producto es **un solo binario**: `macacoview-serve`. No necesita
Node, ni Python, ni GTK, ni instalacion: se baja, se corre, y sirve el mismo
tablero en el navegador. Esta seccion esta escrita para alguien que no
programa.

### Paso 1: saber que Mac tenes

Menu Apple (la manzanita arriba a la izquierda) -> **Acerca de este Mac**:

- Si dice **Chip: Apple M1 / M2 / M3 / M4** -> es Apple Silicon. Bajate el
  archivo que termina en **`aarch64-apple-darwin`**.
- Si dice **Procesador: Intel ...** -> es Intel. Bajate el que termina en
  **`x86_64-apple-darwin`**.

No son intercambiables: el archivo equivocado no arranca (macOS lo rechaza con
un error de "CPU type"). Si no estas seguro, casi seguro es Apple Silicon: las
Mac con Intel se dejaron de vender en 2020.

### Paso 2: bajar, descomprimir y verificar

En la pagina de **Releases** del repositorio, de la ultima version baja de la
seccion Assets dos archivos: el `macacoview-serve-<tu-arquitectura>.tar.gz`
y su `.sha256` (el repositorio es publico: se bajan sin ninguna credencial).

Despues, en Terminal (la abris con Spotlight: Command + Espacio, escribis
"Terminal"):

```bash
cd ~/Downloads
shasum -a 256 -c macacoview-serve-aarch64-apple-darwin.tar.gz.sha256
tar -xzf macacoview-serve-aarch64-apple-darwin.tar.gz
```

(la version Intel usa `x86_64-apple-darwin` en el nombre). El primer comando
tiene que responder `OK`: si el checksum no coincide, no sigas -- el archivo
se corrompio al bajar o no es el que crees.

### Paso 3: la primera vez, macOS lo va a frenar

El binario **va sin firmar** (no hay certificado de desarrollador de Apple
atras), asi que Gatekeeper lo bloquea en la primera corrida con un cartel de
"no se puede verificar el desarrollador". Es lo esperado, no un virus. Dos
salidas, cualquiera alcanza:

- En Terminal, quitarle la cuarentena:

  ```bash
  xattr -d com.apple.quarantine ./macacoview-serve
  ```

- O en Finder, clic derecho sobre `macacoview-serve` -> **Abrir** ->
  **Abrir** de nuevo en el dialogo que aparece.

Despues de esa primera vez arranca sin volver a preguntar.

### Paso 4: correrlo

```bash
./macacoview-serve
```

Y abrir **http://127.0.0.1:8787** en el navegador (Safari, Chrome, el que
uses). Se corta con Ctrl-C en la Terminal. Si el puerto esta ocupado, se
cambia asi: `PC_AI_PORT=9000 ./macacoview-serve`.

### Que vas a ver (y que NO vas a ver)

Expectativas honestas: en una Mac recien instalada, el tablero queda **casi
vacio**, y eso es correcto, no un error.

- **Tokens**: aparecen los de **Claude Code** y **Codex** si esas herramientas
  estan instaladas y se usaron (el daemon lee sus transcripts locales). Cualquier
  otro proveedor no aparece si no lo configuraste.
- **Modelos locales**: la lista queda **vacia** si no hay un servidor de
  modelos corriendo (por ejemplo Ollama). Si no hay nada corriendo, no hay nada
  que detectar.
- **Grupos de procesos**: quedan vacios hasta que configures que queres
  monitorear.

Lo que si aparece sin configurar nada: memoria, CPU, GPU y puertos de la
maquina.

## Releases

Empujar un tag `v*` dispara el workflow `release`, que empaqueta el arbol
instalable (`gui/` + `scripts/`), lo verifica instalando en un HOME limpio del
runner, y lo adjunta a la release de GitHub como
`macacoview-<tag>-linux.tar.gz` + `.sha256`.

La misma release lleva los binarios del daemon para Mac:
`macacoview-serve-aarch64-apple-darwin.tar.gz` (Apple Silicon) y
`macacoview-serve-x86_64-apple-darwin.tar.gz` (Intel), cada uno con su
`.sha256`. El workflow los compila DESPUES de compilar el frontend (que queda
incrustado en el binario) y falla si el build no confirma que los assets
fueron incrustados: un daemon sin frontend compila igual y sirve un
placeholder indistinguible desde afuera.
