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

Cubren formato, el parser de `ss`, el layout del treemap y la construcción de
**las cuatro páginas** con un snapshot: un dashboard roto se detecta sin abrir la
app ni mirar la pantalla.
