# Current pc-ai-monitor app — UI inventory (prototype spec for 07)

Source: main checkout `/home/alvaro/Apps/proyect_local/pc-ai-monitor/gui/pc_ai_monitor/`, including
uncommitted files, read-only. Line numbers are approximate where the explorer marked them «~».
Motores is covered by its own blocks (`motores.py`, `residente*`, `engine*`, `rendimiento*`).

## Shell (`app.py`)
- Title «PC-AI Monitor» (redesign: «MacacoView»), 1180×800, dark forced. App id `local.pcai.monitor.gnome` stays.
- `Adw.OverlaySplitView`, sidebar 150–180 px, **starts collapsed**; toggle button «Mostrar u ocultar la barra lateral» in every section header.
- Sidebar order: Motores, Recursos, Router, Tokens, Puertos, Procesos, separator, Configuración. Default section at launch: **Recursos** (no row highlighted at launch — a bug to fix in the redesign).
- No drill-down pages in use: groups and models expand **inline** (revealers, accordion).
- Theme only from Configuración → Tema (Gentle, Cute, Sexy, Ghostly). Icon change also rewrites the `.desktop` file.
- Refresh: Recursos 1 s, Puertos 2 s, Tokens 10 s; router `/status` + `/metrics` on :11009 at the Recursos interval; history 300 samples at 1 Hz.
- `Snapshot.errors` is never shown (gap the redesign can fill).

## Recursos — blocks in order
1. KPIs (no title): «RAM EN USO» `62%`, «RAM TOTAL» `122.0G`, «MODELOS CARGADOS», «PROCESOS VIGILADOS».
2. «MEMORIA DEL SISTEMA»: meters RAM `x / y GiB` (accent), SWAP `x / y GiB` (blue), LIBRE `x GiB` (green).
3. «APLICACIONES / GRUPOS (RSS)»: donut 170 px, center `NN.NNG` «RSS total». Legend rows clickable: «🥧 Pi», «🪽 Hermes», «🦊 Firefox», «⚙️ Sistema (root)», «📦 Otros» with `x.xxG` and %. Click → inline «PROCESOS DE <GRUPO>»: up to 10 processes by RSS, each `PID n`, `x.xxG`, `x.x%`. Empty: «Este grupo no tiene procesos ahora mismo.» Hidden groups (Configuración → Monitoreo) disappear.
4. «MODELOS LOCALES»: hint «Hacé clic en un modelo y el detalle se abre acá mismo.»; empty «Sin modelos locales cargados.» Card header `🤖/🧠/🧩 alias` + `GTT x.xG · RSS x.xG`; click toggles (accordion) → meters GTT, RSS, CPU and line `<model> · PID <pid> · puerto <port|—>`.
5. «HISTORIAL (5 MINUTOS)»: sparklines «RAM usada», «RAM disponible», «🥧 Pi», «🦊 Firefox» with last value `x.xG`; note «Acumulando historial…» then «Ventana: N muestras a 1 Hz».

## Router — 9 read-only blocks in order
1. «CONFIRMACIONES»: empty «nada pendiente de tu decisión»; item = bold kind, amber `id · caduca en Ns`, reason, options (`route` + note), «sin respuesta: <default>».
2. «SYSTEM»: MemAvailable (tier colour), MemTotal, swap en uso, swap libre, PSI some avg10, PSI full avg10, flash guard («DISPARADO» / «vigilando (<10 GiB)»), alertas («ninguna»).
3. «ABITO / FLASH»: per engine salud («● vivo»/«○ parado»), puerto, ctx `N (útil M)`, slots `b/s ocupados`, cola, prompt, MTP (3 decimals). Empty «sin datos del router».
4. «ROUTER»: salud, versión, modo («shadow (no manda)» / «ACTIVO»), sesiones activas, confianza mínima, uptime, decisiones totales, última ruta, última confianza, latencia última, decidió, % local, % cloud, % cola, latencia media, confianza media; line `rutas: k=v  k=v`. Empty «router no responde en :11009».
5. «CICLO DE VIDA DE FLASH»: estado, descarga tras `N min ocioso`, ocioso ahora, libre ahora, coste de Flash, quedarían `N GiB (margen exigido M)`, ¿se puede autocargar? (sí / «no: haría falta tu decisión»), autocarga / autodescarga, modo shadow, cargas / descargas, último evento.
6. «JEV»: estado («● con clave» / «○ sin clave» + nota «routing determinista sigue funcionando»), endpoint, modelo, umbral confianza, tokens última decisión, latencia última, tokens acumulados, errores, coste estimado `$x.xx USD (local)`, fórmula `input_tokens × 42 / 1e9`.
7. «CLOUD»: NAN (configurado / no configurado), OpenAI (configurado / sin credencial), nota OpenAI, LiteLLM («● vivo» / «○ caído»), aliases publicados, últimos desbordes.
8. «ENGRAM»: MCP alcanzable, servicio, endpoint, versión, estado; hint «memoria del agente; no es telemetría».
9. «SESSIONS»: empty «sin sesiones activas»; table id · workflow · ruta · ctx · edad · cola (up to 12).

## Tokens — blocks in order
1. KPIs: «TOKENS EN PI», «TURNOS», «COSTO ACUMULADO» `$x.xx`, «LEÍDOS DE CACHÉ».
2. «CODEX CLI» and 3. «CLAUDE CODE»: «TOKENS», «RAZONAMIENTO», «ESCRITO EN CACHÉ», «LEÍDO DE CACHÉ», «TURNOS», «COSTO».
4. «POR PROVEEDOR»: donut, center total «tokens», top 8 + «resto (N)»; legend sorted; **click a slice filters the legend to that provider** (click again clears); note «Hacé clic en un proveedor para ver sus modelos.»
5. «POR MODELO»: columns per model with total > 0 (labels 10 chars); note «Sin conteo de tokens (Pi no los recibe): <name> (N turnos), …».
6. «SUSCRIPCIÓN CODEX»: note «Plan <plan> · resetea <resets_at>»; one donut per window (`N%`, label) with «consumido» (≥90 red, ≥70 amber, else green) and «disponible»; empty «Sin ventanas informadas por codexbar.»

## Puertos
1. KPIs: «TCP», «UDP», «EN TODAS LAS INTERFACES».
2. «PUERTOS ESCUCHANDO»: check «sólo expuestos a todas las interfaces» (off). Columns TIPO, PUERTO (sortable, default asc), ACCESO, SERVICIO, PROCESO, PID (sortable); ▲/▼; new key asc, same key toggles. Access badges «🔒 Local», «🏠 LAN», «🔷 Tailscale», «🌐 Todas», «📡 Interfaz»; exposed rows highlighted; max 80 rows. Note `N de M puertos · local <first>`; empty «Sin datos de puertos: falta el helper privilegiado.»

## Procesos
«PROCESOS POR CONSUMO»: columns PID (sortable), PROCESO, RAM (sortable, default desc), CPU (sortable), COMANDO; same key toggles, new key asc only for PID; top 40; note «Mostrando N de M procesos · ordenado por <ram|cpu|pid>». Rows not clickable.

## Configuración — applies on every change after 600 ms, rewrites the TOML
- **Apariencia**: «Tema» (Gentle / Cute / Sexy / Ghostly (Ghostty)); «Ícono (nombre del tema de iconos)» with 24 px preview and presets «Monitor», «PC», «Terminal», «Sistema».
- **Refresco** («Cada cuánto se consultan los colectores. Más seguido = más carga.»): «Recursos cada (s)» 1.0 (0.5–30), «Puertos cada (s)» 2.0 (0.5–60), «Tokens cada (s)» 10.0 (2–120).
- **Colectores** («Rutas de los scripts. Si falta uno, esa sección queda en «sin datos».»): «Recursos», «Tokens», «Helper de puertos» paths; switch «Usar sudo -n para los puertos» («Sin contraseña: si falta el helper, la pestaña queda vacía.»).
- **Pill del panel** (GNOME top-bar extension): «Ocultar valores en cero» (on), «Mostrar aunque estén en cero», «Mostrar SOLO (vacío = todo)», «Unidades» (GiB / Porcentaje), «Con varios modelos» (Un chip por modelo / Una métrica sumada), «Mostrar estado del router en la barra» (off, «A/F vivos · JEV · sesiones/slots · RAM libre · cola»).
- **Monitoreo**: one switch per watch group «🥧 pi», «🪽 hermes», «🦊 firefox», «⚙️ system», «📦 other» with subtitle «Coincide con: …» / «Regla fija del colector: …». Off → hidden in Recursos groups and history.

## Values for the simulation (this machine)
RAM 122 GiB; used ≈ 70.9, available ≈ 51.1; swap small. Groups RSS: modelos/llama-server 49.2 (Gufo), Pi 6.8, Otros 4.6, Firefox 4.3, Sistema 3.9, Hermes 2.1. Processes: llama-server 49.2G, firefox 2.8G, node (pi) 1.9G, hermes 1.6G, gnome-shell 1.1G … Ports (only these): 22 ssh (all), 5353 mdns (all, UDP), 631 CUPS, 7420 hermes, 7600 open-pencil, 9222 chrome devtools, 11009 router, 11012 embed-small, 11070 gufo. Router: active, 3 sessions, local 87 % / cloud 9 % / cola 4 %, latency ≈ 38 ms. Tokens today: Pi 18.4M, 1 284 turns, US$ 41.27, cache 14.9M; providers local 11.4M, openai-codex 4.4M, anthropic 2.6M; Codex windows 5 h 38 %, semana 61 %.
