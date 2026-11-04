# MacacoView — design notes

The redesign of `pc-ai-monitor` as **MacacoView** now follows one family: **01 Cabina densa**, two
visual alternatives of it, **04** and **05**, and **06**, which combines the three, made from the user's canvas comment on 01 («más visual,
reducción de texto en el front de un 85 %, gráficas con mayor uso y una mejor CX»). Directions 02
(Adwaita nativo) and 03 (Riel de vigilancia) were discarded by the user; their files are kept in
`_descartados/` and are not on the canvas.

Each artboard shows the identity, the Motores page at real size (1180×800) with the engine switch,
update and live performance, the states that matter, and the other five pages. Values follow the
brief (Gufo on :11070, ctx 131072; Abito on :11002, ctx 65536; 122 GiB RAM).

## Confirmed data sources

The product owner confirmed the sources below; the artboards follow them.

**Performance panel (read-only)**

- `GET 127.0.0.1:11070/metrics`: `llamacpp:prompt_tokens_total`, `llamacpp:prompt_tokens_cached_total`,
  `llamacpp:kv_cache_usage_ratio`, `llamacpp:requests_processing`, `gufo_device_lost_total` and the
  `gufo_spec_*` counters when present.
- `GET /v1/models` → `context_length`; `GET /slots` → `n_ctx`, `is_processing`, `n_prompt_tokens`.
- `gufo-flash status` → `engine_ready`, `phase`, `mem_available_gib`, `conservative_gib`,
  `min_mem_available_gib`, `min_conservative_gib`.
- Prefill and decode tok/s are **not** a field: they are derived from counter deltas between two
  readings. With fewer than two readings the card shows «sin datos recientes», never a 0.

What that means for the design:

- **TTFT was dropped** (and every p50/p95): no listed source provides it. Open question: add it only if
  a source appears. The slot is now «Pedidos en curso» (`requests_processing` + `/slots`
  `is_processing` and `n_prompt_tokens`).
- Cache = `prompt_tokens_cached_total / prompt_tokens_total`; KV = `kv_cache_usage_ratio` against
  `context_length`; MTP acceptance is derived from `gufo_spec_*` (no «tok por paso»).
- GPU shows only health from `gufo_device_lost_total` (temperature, utilisation and GTT have no source).
- RAM headroom shows the two real pairs: available (minimum 20 GiB) and conservative (minimum 24 GiB).
  The gauge marker is the minimum.

**Update flow**

- `local-ai-models engine status --json` → `running{tag,image_id,version_label,revision_label,context,port,serving}`,
  `available{tag|error}`, `updatable`, `reason`, `inventory[]`.
- `local-ai-models engine update --to TAG --yes --json`: exit 0 = verified (health, `/v1/models` and
  context equal to `GUFO_CONTEXT`); exit 10 = confirmation missing, nothing done; exit 1 = failed and
  went back to the previous version.
- **Rollback criteria** are health, served context different from the configured one, or a pull
  failure. It is not about performance: a slow but healthy engine is not rolled back.
- The engine takes ≈ 85 s to load and every in-flight request is cut; the confirmation texts and the
  engine-switch cost say so.

**Illustrative values**: the tag/version/revision/image (`gufo:b6532` → `gufo:b6587`, `b6532`,
`a3f91c2`, `sha256:4be1…`), the conservative RAM figures (46.3 / 24 GiB), the 65 536 context of the
rolled-back run, the `registry no responde (timeout 10 s)` error and the log path are made up.

**Names**: the visible name is MacacoView; identifiers (`local.pcai.monitor.gnome`, `pc-ai-monitor`
paths) are unchanged. A rename is a separate migration.

## What each direction optimises for

| Direction | Optimises for | Gives up | Visible text |
|---|---|---|---|
| 01 Cabina densa | Density and implementation cost: today's shell, `.card`/`.kpi` CSS and four themes, with every value spelled out. | Scannability: everything is words and numbers of the same weight. | 6 625 chars (Motores 1 384) |
| 04 Cabina visual · medidores | Reading state at a glance: engines as blocks inside the 122 GiB RAM bar, arc gauges with the 5-min range for decode/prefill, rings for cache/KV, a version rail for updates, lamps with shape + colour. | New Cairo widgets to build; exact figures move to tooltips. | 817 chars (−88 %) (Motores ~200) |
| 05 Cabina visual · línea de tiempo | Seeing what changed and when: six lanes on one 15-min axis (motor band with events, decode, prefill, KV + cache, RAM with minimums, requests), a shared cursor, actions in a right rail; destructive actions preview their gap on the timeline. | The heaviest to build (lane stack, history buffer of 15 min); needs the app to keep history it does not keep today. | 767 chars (−88 %) (Motores ~200) |
| 06 Cabina combinada | The best of each: 01's shell, sidebar badges and exact values (update fields, inventory, Procesos table); 04's instruments for the present (RAM blocks with the switch, cache/KV rings, MTP, GPU lamp, version rail); 05's time view (15-min motor band with events, decode/prefill lanes with a shared cursor). Text target relaxed by the user to about 60 % less than 01, so every chart keeps a label and units. | Builds most of the new widgets of 04 and 05 (RAM blocks, rings, rail, motor band, lanes) and needs the 15-min history of 05. | 1 704 chars (−74 %) (Motores 539) |

CX moves shared by 04, 05 and 06: every action sits next to the instrument it changes, keyboard hint chips
on the two main actions, one age pill per view, green/amber/red used only for state, and the
confirmation shows the effect (RAM before → after, ≈ 85 s gap, context change) instead of describing it.

## Decorative vs functional

Functional in the three: tabular monospace numbers, the green/amber/red state colour, the RAM minimum
markers, the measurement age, «sin datos» instead of 0 for prefill/decode, and the switch/update
confirmation that states the cost.

Decorative, cuttable without losing information:

- 01: the coloured badges in the sidebar, the shimmer on loading tiles.
- 04: the needle (the number already says it), the 5-min area chart under the gauges, the hatched «other processes» block.
- 05: the striped restart segment, the per-process CPU sparklines, the route-tick strip on Router.
- All: the 128 px icons are concepts, not finished assets.

## GTK4 / libadwaita constraints the implementation will hit

- **Every chart is `Gtk.DrawingArea` + Cairo**, as `widgets/graficos.py` already does. Existing widgets
  reused: `Dona` (rings, donuts), `Treemap` (04 Recursos), `Sparkline` (areas, CPU lines), `Columnas`
  (Codex windows, 05 Router). New widgets: arc gauge with range band and needle (04), RAM memory blocks
  (04), RAM bar with two minimum markers (04, 05), version rail and 4-step stepper (04, 05), port
  matrix (04, 05), shared-axis lane stack with a state band and event markers (05), stacked area (05).
  Do not use `Gtk.LevelBar` for the minimum markers: it has offsets, not markers.
- **Tooltips carry what left the screen**: `Gtk.Widget.set_tooltip_text` on the drawing area works for
  the whole chart; per-segment tooltips (a block, a lane event) need `query-tooltip` with hit-testing
  in the widget. The accessible name of each chart has to be set with `update_property(Gtk.AccessibleProperty.LABEL, …)`.
- **Shared cursor (05)**: one `Gtk.EventControllerMotion` per lane stack, the cursor x shared through
  the parent widget and a single `queue_draw()`; keyboard movement through `Gtk.EventControllerKey`.
- **History (05)**: the lanes need a 15-minute ring buffer per metric in the collector; today
  `datos.py` keeps 5 minutes of memory history only.
- **Tabular numbers**: monospace family is already tabular; thin-space thousands come from `formato.py`.
- **Animations** (loading sweep, shimmer): GTK4 CSS animates opacity reliably, gradient positions not;
  Cairo sweeps need an `Adw.TimedAnimation`. Reduced motion → `Gtk.Settings:gtk-enable-animations`.
- **`Adw.AlertDialog`** replaces the deprecated `Gtk.MessageDialog` in `blocks/motores.py`; a visual
  diff inside it is an `extra-child` with a drawing area.
- **Icons**: the window icon on Wayland comes from the `.desktop` file; a real icon is an SVG in
  `hicolor/scalable/apps` plus a `-symbolic` variant. The app id stays `local.pcai.monitor.gnome`.

## What to cut first if implementation cost matters

1. The other five pages' restyling: keep their blocks and only rename the window and headers. The value is in Motores.
2. 05's lane stack beyond motor + decode + RAM (the history buffer is the expensive part).
3. 04's needles and arc range bands: a ring with the number in the middle says the same.
4. Per-segment tooltips: one tooltip per chart with all the details is enough to start.
5. Never cut: the measurement age, «sin datos» instead of 0, the RAM minimums, and the confirmation that
   states the cost. Those are what keep a live number from lying.

## Open questions

- Which of 01 / 04 / 05 / 06. 06 is the combination the user asked for; the user decides.
- Text level: the user relaxed the 85 % reduction to «un % más razonable»; 06 uses about 60 % as the target (it landed at 74 %). 04 and 05 still use the 85 % target.
