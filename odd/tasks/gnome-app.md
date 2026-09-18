# Feature: App GNOME nativa (Python + GTK4/libadwaita)

## Objetivo

Migrar PC-AI Monitor de Tauri (webview) a una **app GNOME nativa** en Python + GTK4 +
libadwaita, instalable con `.desktop` e `install.sh`, con las rutas y servicios en un archivo de
config. Se migra **pestaña por pestaña**; la app Tauri queda como referencia y rollback hasta que
la GNOME cubra todo.

## Decisiones del usuario

- Stack: **Python + GTK4/libadwaita** (la app GTK anterior era un script con `Gtk.TextView`, no
  sirve como base de UI).
- Portabilidad: **instalable + config**, sin prometer portabilidad total. En otra máquina arranca
  y muestra lo que pueda; el resto queda en "sin datos".
- Convivencia: **migrar por partes** y retirar la Tauri al final.

## Entorno verificado

| Componente | Versión |
| --- | --- |
| Python | 3.14.4 |
| GTK | 4.22.4 |
| libadwaita | 1.9.1 |
| pycairo | 1.27.0 |
| xvfb | disponible (prueba headless) |

## Arquitectura

```text
gui/
  pc_ai_monitor/
    app.py            Adw.Application + ToolbarView + ViewStack (pestañas en el header)
    config.py         TOML en ~/.config/pc-ai-monitor/config.toml con defaults portables
    datos.py          fuente de datos en hilo aparte + señal; nunca bloquea el loop de GTK
    formato.py        formato compartido (GiB, tokens, dinero, conteos)
    widgets/graficos.py   Cairo: Barra, Dona, Columnas, Treemap, Sparkline (con hit-testing)
    pages/recursos.py
  bin/pc-ai-monitor-gnome
  install.sh
  tests/
```

## Invariantes

- La UI **nunca** bloquea: toda recolección corre en un hilo y los resultados llegan al loop
  principal por `GLib.idle_add`.
- Cada fuente es independiente: si falla, esa sección queda en "sin datos" y el resto sigue.
- Los tokens se refrescan cada 10 s; recursos y puertos cada 1 s (igual que en la app Tauri).
- Nada de rutas `/home/alvaro` escritas en el código: salen de la config, con defaults derivados
  de `Path.home()`.

## Reorganización de componentes (acordada, sin ejecutar)

**Decisión del usuario**: catálogo de bloques + dashboards declarativos, **sin** mover el paquete
completo en capas.

### Problema actual

- `widgets/barras.py` mezcla medidores (`MeterRow`), KPIs (`KpiTile`) y cosas que no son barras
  (`chip`, `chips`, `card`, `section_heading`, `note`).
- `widgets/graficos.py` mezcla los gráficos con el tipo `Slice` y los helpers de dibujo de texto.
- `pages/recursos.py` arma la página entera a mano: para agregar un dashboard hay que copiar y pegar.
- `app.py` sabe del shell y de cómo construir cada página.

### Contrato de bloque (la pieza que falta)

Un bloque es un `Gtk.Widget` con:

```python
class Block(Gtk.Box):
    title = "..."                # título de la tarjeta
    def update_snapshot(self, snapshot: Snapshot) -> None: ...
    def redraw(self) -> None: ...   # opcional: repintar los gráficos al cambiar de tema
```

Una página pasa a ser **sólo una lista de bloques**:

```python
class Dashboard(Gtk.ScrolledWindow):
    blocks = (KpiBlock, MemoryBlock, ModelsBlock, GroupsBlock, HistoryBlock)
```

### Movimiento de archivos (paso 1, sin cambios de comportamiento)

| Archivo | Qué queda adentro |
| --- | --- |
| `widgets/base.py` (nuevo) | `Slice`, `_Chart`, `_text` |
| `widgets/barras.py` | sólo `MeterRow`, `KpiTile` |
| `widgets/chips.py` (nuevo) | `chip`, `chips`, `card`, `section_heading`, `note` |
| `widgets/graficos.py` | `Dona`, `Columnas`, `Treemap`, `Sparkline` (importan de `base`) |

### Bloques (paso 2)

| Bloque | Contenido que hoy vive en `recursos.py` |
| --- | --- |
| `blocks/kpis.py` | la franja de 4 KPIs |
| `blocks/memoria.py` | barras de RAM y SWAP |
| `blocks/modelos.py` | tarjetas por modelo + drill-down a `ModelDetail` |
| `blocks/grupos.py` | dona de grupos + leyenda + drill-down a `GroupDetail` |
| `blocks/historial.py` | las 4 sparklines y la nota de ventana |

`blocks/__init__.py` expone `CATALOGO: dict[str, type[Block]]` y `build(name) -> Block`, así un
dashboard se declara por nombre y el shell no necesita conocer las clases.

### Orden de ejecución (paso 3)

1. `widgets/base.py` + `widgets/chips.py`, y adelgazar `barras.py` / `graficos.py`.
2. Los 5 bloques, cada uno recibiendo lo que necesita por constructor (`on_drill`, etc.).
3. `pages/recursos.py` reducido a la lista de bloques.
4. `app.py` construye dashboards desde el catálogo (`build(name)`), no con un `if/else` por sección.
5. Smoke test con xvfb + prueba de drill-down.

### Cómo probar (dos trampas ya conocidas)

```bash
P=$(printf 'pc_ai%s' _monitor); pkill -f "$P"; sleep 1
cd gui && xvfb-run -a timeout 30 python3 -m "$P" --self-test
```

`Gio.Application` es de instancia única: si la app está corriendo, la prueba sale con exit 0 **sin
imprimir nada** y no prueba nada. Y `pkill -f` se mata a sí mismo si el patrón aparece literal en
la línea de comando (por eso el `printf`).

## Tareas

- [x] 1. Documento de feature y estructura del proyecto
- [x] 2. `config.py`, `formato.py`, `datos.py` (hilo + señal, sin GUI)
- [x] 3. `widgets/graficos.py` con Cairo: dona, columnas, treemap, sparkline (con hit-testing)
- [x] 4. `tema.py`: los 3 temas de gentle-ai + modo Ghostly, CSS por `@define-color`
- [x] 5. `widgets/barras.py`: medidores con `Gtk.ProgressBar` nativo, chips, KPIs y tarjetas
- [x] 6. `__init__.py` con `require_version` (GTK4 garantizado para todo submodulo)
- [x] 7. Stubs `gui/typings/gi` + `gui/pyrightconfig.json` para que el analizador resuelva PyGObject
- [x] 8. Página **Recursos** (`pages/recursos.py`): KPIs, memoria, tarjetas de modelos, grupos, historial
- [x] 9. `app.py` (Adw.ToolbarView + ViewStack + selector de tema) y `__main__.py`
- [x] 10. Lanzador `bin/pc-ai-monitor-gnome` e `install.sh` con `.desktop` propio
- [x] 11b. **Puente cairo desbloqueado** con `python3-gi-cairo` (autorizado por el usuario)
- [x] 11. Tests unitarios sin GUI (`tests/test_core.py`: formato, parser de `ss`, treemap y construcción de las cuatro páginas)
- [x] 12. Página **Tokens** (KPIs, dona por proveedor clickeable, columnas por modelo, ventanas de Codex)
- [x] 13. Página **Puertos** (KPIs, clasificación por acceso, filtro de expuestos y orden por columna)
- [x] 14. Página **Procesos** (tabla ordenable por PID/RAM/CPU)
- [ ] 15. Retirar la app Tauri cuando la GNOME cubra las cuatro pestañas

## Verificado hasta ahora

La app **construye, corre, dibuja y sale con 0** (prueba headless con xvfb, `--self-test`):

```text
self-test: stats True | tokens True | historial 3 | errores ninguno
self-test: ventana construida, paginas 1 | tema Gentle
exit=0
```

## Bloqueo del entorno: puente cairo (RESUELTO)

Los gráficos Cairo no dibujaban:

```text
TypeError: Couldn't find foreign struct converter for 'cairo.Context'
require_foreign('cairo') -> ImportError: No module named 'gi._gi_cairo'
```

Estaba `python3-gi` 3.56.2 pero faltaba **`python3-gi-cairo`**, el paquete que aporta
`gi/_gi_cairo`. Resuelto con `sudo apt install python3-gi-cairo` (autorizado por el usuario;
candidato `3.56.2-1` de resolute/main).

**Detalle importante**: con el puente instalado, el draw function recibe un `cairo.Context`
**de pycairo**, no el envuelto por GTK. Por eso `cr.show_layout()` no existe: el texto se
dibuja con `PangoCairo.show_layout(cr, layout)` y hace falta
`gi.require_version("PangoCairo", "1.0")` en el `__init__` del paquete.

## Paletas

| Tema | Fondo | Acento | Origen |
| --- | --- | --- | --- |
| Gentle (activo) | `#06080f` | `#E0C15A` | gentle-ai `Gentle.json` |
| Gentleman Cute | `#060407` | `#F095C8` | gentle-ai `Gentleman-Cute.json` |
| Gentleman Sexy | `#060407` | `#F43888` | gentle-ai `Gentleman-Sexy.json` |
| Ghostly | `#000000` | `#7ac4cc` | Ghostty `Liquid Carbon Transparent` |

El tema activo de gentle-ai sale de `~/.pi/agent/settings.json` (`"theme": "Gentle"`), así que
Gentle es el default de la app. Las paletas están embebidas y `tema.load_external()` las refresca
desde el paquete gentle-pi cuando está presente. **No hay transparencia real**: GTK4 no tiene
backdrop blur y una ventana translúcida mostraría el escritorio sin desenfocar.

## Notas para el analizador (PyGObject)

- El checker **no resuelve imports relativos** en este proyecto: los módulos usan imports
  absolutos (`from pc_ai_monitor import tema`).
- `gi.require_version` vive en `pc_ai_monitor/__init__.py`: se ejecuta antes de cualquier
  submodulo y evita el import después de una sentencia (E402) en cada archivo.
- Los archivos que importan `gi.repository` llevan `# pyright: reportAttributeAccessIssue=false`;
  los stubs permisivos están en `gui/typings/gi` con `stubPath` en `pyrightconfig.json`.
- Las llamadas `int()`/`float()` sobre datos de entrada se evitan o van con guarda
  (`round()`, `formato.safe()`): el checker las marca como no verificadas.


## Estado: completo (a la espera de retirar la Tauri)

Las cuatro secciones están implementadas con el catálogo de bloques, 11 tests que
corren sin pantalla (`xvfb-run -a python3 -m unittest discover -s tests`) y la app
instalada en `~/.local`.

### Decisiones de UI tomadas sobre la marcha

- **Submenús en la misma pantalla**, no navegando: el detalle de un grupo de
  procesos y el de un modelo se despliegan con `Gtk.Revealer` dentro del bloque
  (acordeón: abrir uno cierra los otros). Se eliminó `pages/detail.py`, que era el
  drill-down por navegación.
- **Sidebar colapsado al arrancar**: el contenido usa todo el ancho y libadwaita
  mueve los botones de ventana a la cabecera del contenido mientras está oculto.
  Límite encontrado: los botones de ventana viven en la cabecera del sidebar, así
  que no puede bajar de ~150 px sin quedarse sin botón de cerrar — un riel de sólo
  íconos (~56 px) exige reubicar los botones de ventana de forma permanente.
- **Temas**: Gentle (default, leído de `~/.pi/agent/settings.json`), Cute, Sexy y
  Ghostly.

### Requisito de sistema que hay que documentar

`python3-gi-cairo`. Sin ese paquete la app abre pero **ningún gráfico dibuja**:
`gi.require_foreign("cairo")` falla con `No module named 'gi._gi_cairo'` y el
`cairo.Context` no llega a los draw functions. `install.sh` ya lo verifica.

### Lo único que queda

- [ ] 15. Retirar la app Tauri (decisión del usuario; la GNOME ya cubre las cuatro secciones)
- Revisión nativa del candidato: bloqueada por el relay `pi` del harness (0 bytes en 3 intentos)
