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
rutas de config no cambian). La sección **Home**, primera y por defecto, es el
cockpit del diseño 07: bloques de RAM con la reserva rayada (Gufo ≈ 85 GiB,
Abito ≈ 20 GiB, figura «en uso» medida), banda de motores de 15 minutos con
marcadores de cambio/caída/actualización, mini tarjetas de motor con el botón
compacto «Cambiar a …» (Ctrl E), lanes decode/prefill con cursor compartido,
anillos de caché y ventana KV, dial MTP, lámpara de GPU, inventario compacto
de motores con margen RAM (disponible / conservadora con sus mínimos) y la
tarjeta de actualización de un solo estado con Ctrl U. Todo el cockpit se
alimenta de un poller compartido (5 s) que reutiliza las mismas lecturas que
el panel Motores (`local-ai-models`) y del propio motor Gufo (`:11070`). La
sección **Motores** es el detalle del diseño 07 en dos columnas: a la
izquierda «Motor residente» (tarjetas Abito/Gufo con lámpara, estado, ctx y
las dos cifras de RAM — «≈ 85 GiB reservados · 49.2 GiB en uso» — y el botón
«Cambiar a …» sólo en la inactiva) y «Actualizaciones del PC», la lista de
todo lo actualizable según `local-ai-models updates --json` con píldora de
estado (Al día / Nueva X / Sin canal / No se pudo consultar), eta, botón
«Actualizar» por fila con doble confirmación cuando hay que compilar
llama.cpp (`--build`: decenas de minutos, reinicia Abito) y «Actualizar todo
(N)» secuencial; a la derecha «Rendimiento del motor» como instrumentos
(lámpara de salud, contexto servido, pedidos, sparklines decode/prefill,
dial MTP, anillo de caché, barra de ventana KV, lámpara GPU, barras de
memoria con sus mínimos y «Actualizar» manual) y «Motores locales», el
inventario dinámico de siempre.

La barra lateral lleva badges vivos —motor activo, RAM libre, % local del router
y puertos expuestos— y cada cabecera una pill con la edad de los datos de su
sección (verde < 10 s, ámbar desde 10 s); si el colector de una sección falla,
un banner lo anuncia arriba.

## RAM por programa

En **Configuración → RAM por programa** puede elegir programas observables y
activar **Mostrar programas seleccionados**. Ese interruptor controla dos sitios:
la barra superior y la lista «Programas seleccionados · no sumar» de **Recursos**,
bajo la leyenda de grupos. Apagado, no aparecen en ninguno de los dos. Viene
apagado, con la selección vacía; los grupos y modelos existentes no cambian. Se
guarda en `[bar]` de `~/.config/macacoview/config.toml`, como
`programs_enabled = true` y `program_ids = ["program:v1:…"]`. Un config que no
tiene esas claves carga igual, con la opción apagada.

La lista usa los snapshots habituales: abrirla no inicia otro escaneo. Cada fila
muestra el ejecutable y un ID opaco corto que distingue intérpretes con el mismo
nombre; nunca los argumentos. El ID no depende del PID. Una selección ausente
queda como «No activo o no observable», se puede desmarcar y vuelve a aparecer
cuando el programa reaparece. Apagar el interruptor no borra la selección.

Primero se descubren y agrupan todos los procesos observables con su RSS.
**PSS** se lee solo para los IDs guardados, incluidos los miembros de la misma
aplicación agrupados con ellos; nunca para otros descendientes. Sin selección no
se lee smaps. El interruptor solo controla qué se muestra: no borra IDs ni cambia
lo que se muestrea.

**PSS** reparte las páginas compartidas entre procesos. Si no cubre todo el grupo,
se usa **RSS (PSS parcial)**, **RSS (PSS inaccesible)**, **RSS (PSS no muestreada)**
o **RSS (PSS: proceso cambiado)**. Estas etiquetas describen a PSS; no quieren
decir que el RSS esté incompleto. RSS puede contar dos veces las páginas
compartidas. La RAM usada del sistema es **total − disponible**; la caché y el
kernel no se atribuyen por completo a programas. Los programas se superponen con
los grupos, los modelos y GPU/GTT, así que nunca se suman al «RSS total» de
Recursos ni a ninguna otra medida.

El colector pide `ps -eo comm:64=,pid=,…`, con el nombre primero. Una RSS de 7
cifras con un %CPU de 4 se come el relleno de las columnas que van detrás, y así
un nombre de ancho fijo puesto detrás se partía en «pi … p» + «i». Con el nombre
primero, ningún desborde le quita espacio. La captura real está en
`tests/fixtures/ps_2026-11-09/`.

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
                  blocks/actualizaciones.py + actualizaciones_logica.py:
                  «Actualizaciones del PC» de la página Motores (S6) y su
                  lógica pura (contrato L8/L11/L12/L14: filas con píldora,
                  eta y botón; resultados de `update <id> --json` — 0
                  verificado, 0 no-op, 10 rechazado o build_required,
                  1 rollback con JSON `restored: true` — a mensajes de
                  usuario); sin GTK, testeable sin ventana
                  blocks/motores_vista.py: vista rediseñada de la página
                  Motores (T4): tarjetas de residente con las cifras S9 y
                  el rendimiento como instrumentos, reutilizando
                  residente_logica, rendimiento(_logica), home_logica y
                  los instrumentos de T2; el motor retirado «flash» jamás
                  aparece
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

## Página Router

La página **Router** (diseño 07) se arma con cinco bloques que leen el Snapshot del
colector y se refrescan con cada sondeo: franja de KPIs, «Rutas en el tiempo» +
«Decisiones» con filtros compartidos (ruta, decisor, ventana de 5/15/60 min y
búsqueda: filtran la tabla y el gráfico a la vez), tarjetas de Abito (del `/status`
de :11009) y Gufo (de sondas vivas a :11070, en un worker), confirmaciones con
cuenta regresiva, el ciclo de vida de flash como línea histórica, teselas
JEV/CLOUD/ENGRAM y sesiones. El historial de decisiones que alimenta el gráfico lo
acumula el colector en `Snapshot.decisions` (sin repetidos por `(ts, sesión)`, hasta
2000 entradas y 60 minutos). «flash» es el motor retirado: línea apagada, nunca una
tarjeta. Sin router en :11009 la página se atenúa entera con su aviso.

## Recursos y Tokens

**Recursos** se lee de un vistazo: franja de KPIs, medidores de RAM, SWAP y
LIBRE (cada uno con su color), un treemap del RSS por grupo —un clic en una
ficha o en la leyenda abre sus procesos en la misma tarjeta—, los modelos
locales en acordeón y el historial de 5 minutos. **Tokens** resume Pi, Codex
CLI y Claude Code en tarjetas, un donut por proveedor que filtra la leyenda al
clic (y la limpia al repetirlo), columnas por modelo y las ventanas de la
suscripción Codex coloreadas por presión (≥ 70 % ámbar, ≥ 90 % rojo). Los
números exactos quedan en las leyendas; las frases, en los tooltips.

## Puertos, Procesos y Configuración

**Puertos** conserva sus tres KPIs, el filtro «sólo expuestos» y las columnas
PUERTO/PID ordenables; el acceso ahora es una píldora de color (🔒 Local,
🏠 LAN, 🔷 Tailscale, 🌐 Todas, 📡 Interfaz) con la explicación en el tooltip,
y las filas expuestas a todas las interfaces llevan una barra ámbar.
**Procesos** muestra una barra de RAM por fila, escalada al proceso más pesado,
con la línea de comando completa en el tooltip. **Configuración** es la misma
página de preferencias más una vista previa de la pill de la barra superior de
GNOME: sus chips salen de la configuración actual y de la última medición,
nunca de datos inventados; se abre con la última medición disponible.
