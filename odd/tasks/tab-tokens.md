# Feature: Pestaña de tokens (consumo por fuente)

## Objetivo

Nueva pestaña **Tokens** que muestre cuántos tokens se consumen y dónde: litellm (modelos
remotos), Pi, modelos locales (llama.cpp) y la suscripción de Codex.

## Decisiones del usuario

- Fuentes: **las cuatro** (litellm remotos, Pi, modelos locales, Codex %).
- Ventana: **desde el arranque de cada servicio** (contadores acumulados tal como los reportan
  los servicios; sin cálculo de tendencia).
- Estilo: **barras apiladas por origen + ranking de modelos** con desglose input/output/caché/reasoning.

## Fuentes de datos verificadas

| Fuente | Endpoint / archivo | Estado |
| --- | --- | --- |
| litellm | Prometheus `127.0.0.1:9090`, métricas `litellm_{input,output,input_cached,output_reasoning,total}_tokens_metric_total` y `litellm_spend_metric_total` por `model` | up |
| Pi | `~/.pi/agent/sessions/**/*.jsonl`, campo `message.usage` por turno | disponible |
| Modelos locales | `http://127.0.0.1:<puerto>/metrics` de llama-server (`llamacpp:*`) | habilitado con `--metrics` |
| Codex | `/home/alvaro/.local/bin/codexbar --provider codex --source auto --format json` (porcentaje de ventana, no tokens) | disponible |

## Tareas

- [x] 1. Script `scripts/pc-ai-tokens` (Python stdlib) que agrega las cuatro fuentes en un JSON
- [x] 2. Agregar `--metrics` a las units de llama.cpp y reiniciar las activas
- [x] 3. Collector Rust: nueva sección `tokens` (poll cada 10 s, no cada 1 s)
- [x] 4. Tipos TypeScript espejo del payload
- [x] 5. Componente `Tokens.tsx` gráfico (apiladas por origen + ranking por modelo)
- [x] 6. Nueva pestaña en `App.tsx`
- [x] 7. Build con el CLI de Tauri, instalar y verificar
- [x] 8. Separar el consumo de Pi **por modelo** (a pedido del usuario)
- [x] 9. Rediseño gráfico: donut + columnas verticales, y separar consumidores de servidores
- [x] 10. Filtros visuales: por proveedor, servicio, origen, orden y "sin conteo"

## Filtros y agrupación (frontend puro)

No hizo falta tocar el colector: cada modelo de Pi ya trae `provider`, y de ahí se derivan
servicio y origen.

- Controles: **Ver por** (Modelo / Proveedor / Servicio), **Ordenar** (Tokens / Costo / Turnos),
  **Origen** (Todos / Local / Remoto) y el interruptor **mostrar sin conteo**.
- Los filtros se aplican con **clic**: en la leyenda, en el treemap y en las filas del detalle.
  El filtro activo se muestra como chip con ✕ y con el conteo `visibles de total`.
- Los gráficos son el filtro, no un adorno: `Donut`, `Treemap` (área proporcional) y `Columns`.
- **Drill-down**: filtrar por proveedor o servicio reagrupa por modelo (lo interesante es ver qué
  modelos hay adentro). Filtrar un modelo cambia los gráficos por la **composición** de ese modelo
  (caché leída, entrada, salida, razonamiento, caché escrita).
- Mapa de proveedores locales (`llamacpp`, `abito-direct`, `ollama`, `magnitude`); cualquier otro
  cuenta como API remota, así un proveedor nuevo cae del lado seguro.
- Franja de **KPIs gigantes** que responde a los filtros: tokens, modelos, turnos, costo y caché leída.
- Dato real que muestra el valor del filtro: en **Local** hay **0 tokens pero 655 turnos**, así que
  ordenar por **Turnos** con origen Local es la única forma de ver ese trabajo.

### Agregados reales (verificados)

| Proveedor | Tokens | Turnos | Costo |
| --- | --- | --- | --- |
| openai-codex | 2.311.901.206 | 16.538 | $1866,88 |
| nan | 159.274.954 | 642 | $0 |
| anthropic | 157.905.330 | 530 | $132,02 |
| llamacpp | 0 | 358 | $0 |
| abito-direct | 0 | 295 | $0 |

Por servicio: Codex 2,31 B · API remota 317,2 M · Local 0 (655 turnos).

## Rediseño gráfico y alcances que no se suman

El usuario pidió una versión mucho más gráfica. Decisión: **donut + columnas verticales**, y
**separar consumidores de servidores**.

- `Charts.tsx` (nuevo): `Donut` (arcos con `stroke-dasharray`), `Columns` (barras verticales
  escaladas), `ChartLegend`. SVG/CSS puros, sin librerías.
- La cola de modelos se agrupa en una rebanada "resto (N)" para que el donut no sea confeti; los
  miembros del resto se listan igual en texto, así no se pierde ningún número a la vista.
- **Motivo del cambio de estructura**: los tres "orígenes" anteriores no eran excluyentes. Pi
  consume a través de proveedores remotos y locales, así que el mismo tráfico aparecía en dos
  paneles: `deepseek-v4-flash` estaba en Pi con 153 M y en litellm con 57 M, con alcances
  distintos (cliente vs servidor). Sumar REMOTO + LOCAL + PI en un total único era engañoso.
  Ahora hay dos secciones separadas —CONSUMIDORES (Pi) y SERVIDORES (litellm, llama.cpp)— con
  una nota explícita, y ningún total mezclado.
- Se eliminó el CSS muerto del diseño anterior (`.origin-*`, `.rank-*`, `.pi-panel`, `.codex-row`).

## Desglose por modelo en Pi

El modelo sale del propio transcript: cada registro de uso trae `message.model` y
`message.provider`. No hay nada que configurar.

- El campo `usage` de cada turno se acumula en dos lugares a la vez: los contadores globales y
  los del modelo. La caché incremental guarda ambos por archivo.
- `PI_CACHE_VERSION = 2` invalida cachés de formato anterior y fuerza un re-parseo limpio
  (auto-sanado, sin borrar archivos a mano).
- Los transcripts de archivos que ya no existen se podan de la caché para que una sesión
  borrada deje de contar.
- **Honestidad de datos**: los modelos locales alcanzados vía Pi (`Abito-gpt`, `llama3.1:8b-llamacpp`)
  reportan turnos con **0 tokens**, porque Pi no recibe esos números. La UI los muestra como
  chips de turnos, separados de los modelos con conteo, en vez de inventar una cifra.
- Invariante verificado: la suma de los totales por modelo es igual al total global (17 modelos).

## Cambios fuera del repo

- `/home/alvaro/.config/systemd/user/llama-*.service`: se agregó `--metrics` al `ExecStart`
  (backups `.bak-pre-metrics-202609172038` al lado de cada unit). Reiniciados los activos
  (`llama-personal` 5 s, `llama-abito` 40 s). Los inactivos lo toman al arrancar.
- `/home/alvaro/.local/bin/pc-ai-tokens`: copia instalada del script versionado en `scripts/`.

## Invariantes

- Degradación limpia: si Prometheus, codexbar o un modelo local no responden, la pestaña
  muestra esa fuente como "sin datos" en vez de romper.
- Ningún secreto en el script: se usan Prometheus y los archivos locales; no se lee ni imprime
  la master key de litellm.
- El costo de recolección no crece con el historial: las sesiones de Pi se leen incremental por
  offset (4,35 s la primera vez, 0,059 s después).

## Verificación realizada

- `cargo test`: 52 tests OK (3 nuevos: round-trip del documento, fuente no disponible, y
  `set_tokens` llegando al snapshot).
- `npm run build`: tsc estricto + vite OK (26 módulos).
- Script: 4,35 s en frío / 0,059 s en caliente; datos reales de las cuatro fuentes.
- Extremo a extremo: con la app corriendo, el collector invocó el script (mtime del cache
  avanzó) y stderr quedó vacío.
- Desglose por modelo: 17 modelos detectados, suma por modelo == total global, 9 con tokens y
  8 sin conteo (reales). Re-parseo por cambio de versión de caché: 0,64 s; incremental: 0,060 s.
- Pendiente: confirmación visual del usuario.

## Bloqueo conocido

La revisión nativa del candidato (`review-56db8d1f242533cb`, tier high, 4 lentes) no puede
completarse: el relay `pi` del harness termina con **exit 0 y 0 bytes** (3 ocurrencias, ~60 s
cada una). La autoridad de revisión queda sin quemar; el candidato no fue juzgado.
