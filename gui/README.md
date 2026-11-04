# PC-AI Monitor — app GNOME

Frontend nativo (GTK4 + libadwaita, Python) sobre el stack local de IA. Reemplaza
a la app Tauri, que sigue instalada hasta que la retires.

## Requisitos

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adwaita-1
```

`python3-gi-cairo` es el que más se olvida: sin ese puente la app abre y muestra
los datos, pero **ningún gráfico dibuja** (los `Gtk.DrawingArea` no pueden recibir
el `cairo.Context`). `install.sh` verifica las tres dependencias y te dice qué
falta.

## Instalar y ejecutar

```bash
./install.sh                    # paquete, lanzador y .desktop en ~/.local
pc-ai-monitor-gnome             # o "PC-AI Monitor (GNOME)" en el menú
./bin/pc-ai-monitor-gnome       # o directo desde el repo, sin instalar
```

## Configuración

Se crea sola en `~/.config/pc-ai-monitor/config.toml` con las rutas de los
colectores, el helper de puertos y los intervalos de refresco. Si un colector no
existe, esa sección queda en "sin datos" y el resto sigue funcionando.

## MacacoView

La app se llama **MacacoView** (ventana y cabeceras; los identificadores y las
rutas de config no cambian). La sección **Home**, primera y por defecto, será el
cockpit del diseño 07; de momento muestra «Home: en construcción». La barra
lateral lleva badges vivos —motor activo, RAM libre, % local del router y
puertos expuestos— y cada cabecera una pill con la edad de los datos de su
sección (verde < 10 s, ámbar desde 10 s); si el colector de una sección falla,
un banner lo anuncia arriba.

## Temas

Cuatro, desde la cabecera: **Gentle** (default, el tema activo de gentle-ai),
**Gentleman Cute**, **Gentleman Sexy** y **Ghostly** (derivado de tu tema de
Ghostty `Liquid Carbon Transparent`). Las paletas están embebidas y se refrescan
desde el paquete gentle-pi cuando está presente.

## Estructura

```text
puertos.py        parser de `ss` (sin GTK, testeable)
widgets/          base (contrato de gráficos) · barras · chips · graficos · tabla
blocks/           Block, Dashboard, catalogue() + un módulo por tarjeta
                  blocks/residente.py + residente_logica.py: tarjeta de cambio
                  Abito ⇄ Gufo (nunca los dos en memoria) y su lógica pura
                  (parseo de `local-ai-models resident`, texto de confirmación,
                  códigos de salida de `switch`); sin GTK, testeable sin ventana
                  blocks/engine.py + engine_logica.py: tarjeta de actualización
                  del motor Gufo y su lógica pura (parseo de `local-ai-models
                  engine status` con su dictamen de admisión de memoria —
                  si la máquina puede hospedar el motor ahora, o cuántos GiB
                  faltan, sin umbrales inventados y con el dictamen
                  "desconocido" si el controlador no lo informa; un aviso
                  aparte avisa que el motor se negará a arrancar sin bloquear
                  la actualización, que son cosas distintas), regla del botón
                  de actualización, textos); sin GTK, testeable sin ventana
                  blocks/rendimiento.py + rendimiento_logica.py: tarjeta de
                  rendimiento del motor Gufo y su lógica pura (parseo tolerante
                  de `/metrics`, `/v1/models` y `/slots` del propio motor, los
                  números de memoria de `gufo-flash status`, pisos del guard
                  de 15/12 GiB, velocidades por delta de contadores); solo
                  lecturas al motor, nunca `podman` ni `systemctl`; una lectura
                  fallida conserva los últimos valores marcados desactualizados
pages/            un dashboard = una lista de bloques
app.py            shell: sidebar, navegación, temas, registro de dashboards
```

**Para montar un dashboard nuevo** alcanza con una clase y una entrada en el
registro de `app.py`:

```python
# pages/mipanel.py
class MiPanel(Dashboard):
    blocks = (KpiBlock, HistoryBlock, GroupsBlock)

# app.py
DASHBOARDS = {..., "mipanel": MiPanel}
```

Cada bloque se actualiza solo: implementa `update_snapshot(snapshot)` y, si dibuja
con Cairo, `redraw()` para repintar al cambiar de tema.

## Tests

```bash
xvfb-run -a python3 -m unittest discover -s tests
```

Cubren formato, el parser de `ss`, el layout del treemap, la construcción de
**las cuatro páginas** con un snapshot, la lógica pura del cambio Abito ⇄ Gufo
(parseo de `resident`, texto de confirmación y códigos de salida de `switch`),
la lógica pura de la actualización del motor (parseo del estado con el
dictamen de admisión de memoria, regla del botón, texto de confirmación y
fallos de `local-ai-models engine`) y la lógica
pura del rendimiento del motor (parseo de `/metrics`, mates de caché, ventana
y especulación, pisos de memoria del guard y comportamiento de últimos valores
conocidos ante una lectura fallida): un dashboard roto se detecta sin abrir la
app ni mirar la pantalla.
