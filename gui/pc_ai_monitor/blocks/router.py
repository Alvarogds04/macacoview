"""Router reconstruido (diseño 07, T5, S8/S11/S12).

El panel viejo de texto se reemplaza por el artboard «Router»:

* franja de KPIs (decisiones, % local/cloud/cola, latencia, confianza);
* «Rutas en el tiempo» + «Decisiones» lado a lado, con filtros compartidos
  (ruta, decisor, ventana 5/15/60 min, búsqueda) que filtran tabla Y gráfico;
* dos tarjetas de motor — Abito (:11002, del ``/status`` del router) y Gufo
  (:11070, de sondas vivas al propio motor con los parseos de
  ``rendimiento_logica``, en un worker, nunca en el hilo GTK) — y el «flash»
  del registro del router como línea apagada «motor retirado» (L9), jamás
  tarjeta ni ctx desactualizado del payload;
* confirmaciones con cuenta regresiva «caduca en Ns»;
* ciclo de vida de flash como línea compacta marcada como legado;
* teselas JEV/CLOUD/ENGRAM (lámpara + un valor, detalle en tooltip) y
  sesiones compactas.

La página se refresca con cada snapshot y, si el router no responde en
:11009, se atenúa entera con el banner de corte.
"""

# pyright: reportAttributeAccessIssue=false

import threading
import time

from gi.repository import GLib, Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block, router_vista
from pc_ai_monitor.blocks import router_logica as logica
from pc_ai_monitor.blocks.rendimiento import _http_text
from pc_ai_monitor.blocks.rendimiento_logica import (
    parse_metrics,
    parse_served_context,
    parse_slots,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count

REFRESH_INTERVAL_S = 5.0
MAX_TABLE_ROWS = 12


def truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


class _Card(Gtk.Box):
    """Tarjeta del artboard: título pequeño en mayúsculas + cuerpo."""

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("card")
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("section-head")
        self.append(heading)


class KpisBlock(Block):
    """Franja de seis KPIs, viva con cada snapshot."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        self._strip = router_vista.KpiStrip()
        self.body.append(self._strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        self._strip.set_values(logica.kpi_values(router, snapshot.telemetry()))


class DecisionesBlock(Block):
    """«Rutas en el tiempo» + «Decisiones» con filtros compartidos (S8)."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)

        # -- gráfico -----------------------------------------------------
        chart_card = _Card("RUTAS EN EL TIEMPO")
        chart_card.set_hexpand(True)
        self._chart = router_vista.ColumnasApiladas()
        self._chart_empty = Gtk.Label(label=logica.EMPTY_DECISIONS_TEXT, xalign=0)
        self._chart_empty.add_css_class("muted")
        self._chart_empty.set_visible(False)
        chart_card.append(self._chart)
        chart_card.append(self._chart_empty)

        # -- tabla con filtros -------------------------------------------
        decisions_card = _Card("DECISIONES")
        decisions_card.set_hexpand(True)

        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._route_values: list[str] = [""]
        self._route_dd = Gtk.DropDown(model=Gtk.StringList(strings=["todas las rutas"]))
        self._route_dd.set_tooltip_text("filtrar por ruta")
        self._decider_values: list[str] = [""]
        self._decider_dd = Gtk.DropDown(model=Gtk.StringList(strings=["todos los decisores"]))
        self._decider_dd.set_tooltip_text("filtrar por decisor")
        self._win_buttons: list[Gtk.ToggleButton] = []
        for minutes in logica.WINDOW_MINUTES:
            button = Gtk.ToggleButton(label=f"{minutes} min")
            button.set_tooltip_text(f"ventana de los últimos {minutes} minutos")
            if minutes == logica.DEFAULT_WINDOW_MIN:
                button.set_active(True)
            if self._win_buttons:
                button.set_group(self._win_buttons[0])
            button.connect("toggled", self._on_window)
            filters.append(button)
            self._win_buttons.append(button)
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("buscar sesión…")
        self._search.set_tooltip_text("buscar por sesión, ruta o decisor")
        self._search.set_hexpand(True)
        # notify::text responde al instante (search-changed tiene retardo).
        self._search.connect("notify::text", self._on_search)
        self._route_dd.connect("notify::selected", self._on_dropdown)
        self._decider_dd.connect("notify::selected", self._on_dropdown)
        filters.append(self._route_dd)
        filters.append(self._decider_dd)
        filters.append(self._search)
        decisions_card.append(filters)

        grid = Gtk.Grid(column_spacing=10, row_spacing=2)
        for column, header in enumerate(("HACE", "SESIÓN", "RUTA", "DECISOR", "CONFIANZA", "LATENCIA")):
            label = Gtk.Label(label=header, xalign=0)
            label.add_css_class("muted")
            grid.attach(label, column, 0, 1, 1)
        self._grid = grid
        self._grid_top = 1
        decisions_card.append(grid)

        self._table_empty = Gtk.Label(label=logica.EMPTY_DECISIONS_TEXT, xalign=0)
        self._table_empty.add_css_class("muted")
        self._table_empty.set_visible(False)
        decisions_card.append(self._table_empty)

        row.append(chart_card)
        row.append(decisions_card)
        self.body.append(row)

        self._snapshot: Snapshot | None = None
        self._data_rows: list[Gtk.Widget] = []
        self._win = logica.DEFAULT_WINDOW_MIN

    # -- snapshot y filtros ------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._rebuild_options()
        self._refresh_views()

    def _entries(self) -> list[dict]:
        """Decisiones con ``_seen``; sin historial aún, el ``recent`` del sondeo."""
        assert self._snapshot is not None
        decisions = list(self._snapshot.decisions)
        if decisions:
            return decisions
        return logica.with_seen(self._snapshot.recent_routes(), time.monotonic())

    def _route_value(self) -> str:
        index = self._route_dd.get_selected()
        return self._route_values[index] if index < len(self._route_values) else ""

    def _decider_value(self) -> str:
        index = self._decider_dd.get_selected()
        return self._decider_values[index] if index < len(self._decider_values) else ""

    def _on_search(self, *_args) -> None:
        self._refresh_views()

    def _on_dropdown(self, *_args) -> None:
        self._refresh_views()

    def _on_window(self, button: Gtk.ToggleButton) -> None:
        if not button.get_active():
            return
        for minutes, widget in zip(logica.WINDOW_MINUTES, self._win_buttons, strict=True):
            if widget is button:
                self._win = minutes
        self._refresh_views()

    def _rebuild_options(self) -> None:
        """Dropdowns construidos desde los datos: rutas y decisores vistos."""
        entries = self._entries()
        routes = sorted({str(entry.get("chosen") or "") for entry in entries} - {""})
        deciders = sorted({str(entry.get("decider") or "") for entry in entries} - {""})

        def _rebuild(values: list[str], options: list[str], dd: Gtk.DropDown, all_label: str) -> list[str]:
            previous = values[dd.get_selected()] if dd.get_selected() < len(values) else ""
            new_values = ["", *options]
            labels = [all_label]
            labels.extend(
                option if values is self._route_values else logica.decider_label(option)
                for option in options
            )
            dd.set_model(Gtk.StringList(strings=labels))
            dd.set_selected(new_values.index(previous) if previous in new_values else 0)
            return new_values

        self._route_values = _rebuild(self._route_values, routes, self._route_dd, "todas las rutas")
        self._decider_values = _rebuild(self._decider_values, deciders, self._decider_dd, "todos los decisores")

    def _refresh_views(self) -> None:
        entries = self._entries()
        filtered = logica.filter_decisions(
            entries,
            route=self._route_value(),
            decider=self._decider_value(),
            window_min=self._win,
            search=self._search.get_text(),
        )
        now = time.monotonic()

        # Tabla: la más nueva primero, a lo sumo MAX_TABLE_ROWS filas.
        for child in tuple(self._data_rows):
            self._grid.remove(child)
        self._data_rows = []
        rows = logica.decision_rows(filtered, now=now)[:MAX_TABLE_ROWS]
        palette = theme.active()
        route_colors = {
            "local": palette.accent,
            "cloud": palette.blue,
            "cola": palette.warning,
        }
        for row_index, row in enumerate(rows, start=self._grid_top):
            category = logica.route_category(row["ruta"])
            session_full = row["sesion"]
            cells = [
                (row["hace"], "mono muted", ""),
                (truncate_text(session_full, 26), "mono", session_full),
                ("", "", ""),  # la ruta va con marca de color, vía markup
                (row["decisor"], "muted", ""),
                (row["confianza"], "mono", ""),
                (row["latencia"], "mono", ""),
            ]
            row_widgets: list[Gtk.Widget] = []
            for column, (text, css, tooltip) in enumerate(cells):
                label = Gtk.Label(xalign=0)
                if column == 2:
                    color = route_colors[category]
                    label.set_markup(f'<span foreground="{color}">● {row["ruta"]}</span>')
                    label.set_tooltip_text(f"{category}: {row['ruta']}")
                else:
                    label.set_text(text)
                    if tooltip:
                        label.set_tooltip_text(tooltip)
                for css_class in css.split():
                    label.add_css_class(css_class)
                self._grid.attach(label, column, row_index, 1, 1)
                row_widgets.append(label)
            self._data_rows.extend(row_widgets)

        empty = not rows
        self._table_empty.set_visible(empty)

        # Gráfico: mismas decisiones filtradas, mismas ventanas.
        buckets = logica.minute_buckets(filtered, window_min=self._win, now=now)
        self._chart.set_data(buckets)
        total = sum(bucket["local"] + bucket["cloud"] + bucket["cola"] for bucket in buckets)
        self._chart_empty.set_visible(total == 0)

    def visible_rows(self) -> int:
        """Filas de decisión visibles en la tabla (para tests y lectores)."""
        return len(self._data_rows) // 6


class EnginesBlock(Block):
    """Dos tarjetas: Abito del /status y Gufo de sondas vivas; flash jamás (L9)."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        self._cards = {
            "abito": router_vista.EngineCard("ABITO · :11002"),
            "gufo": router_vista.EngineCard("GUFO · :11070"),
        }
        for card in self._cards.values():
            card.set_hexpand(True)
            row.append(card)
        self.body.append(row)

        self._flash = Gtk.Label(xalign=0)
        self._flash.add_css_class("muted")
        self.body.append(self._flash)

        self._gufo: dict = {}
        self._refreshing = False
        self._last_gufo = 0.0

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        flash_text = logica.flash_line(router) or ""
        self._flash.set_text(flash_text)
        self._flash.set_visible(bool(flash_text))

        self._cards["abito"].set_data(logica.abito_card(router.get("abito") or {}))
        self._refresh_gufo()
        self._render_gufo()

    # -- sonda Gufo (worker, nunca en el hilo GTK) --------------------------

    def _refresh_gufo(self) -> None:
        now = time.monotonic()
        if self._refreshing or now - self._last_gufo < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_gufo = now
        threading.Thread(target=self._gufo_worker, daemon=True).start()

    def _gufo_worker(self) -> dict:
        """Lecturas HTTP al motor Gufo; corre en un worker, entrega por idle_add."""
        metrics_text = _http_text("/metrics")
        metrics = parse_metrics(metrics_text) if metrics_text else None
        served = parse_served_context(_http_text("/v1/models") or "")
        slots = parse_slots(_http_text("/slots") or "")

        card = logica.gufo_card(metrics, served, slots)
        GLib.idle_add(self._apply_gufo, card)
        return card

    def _apply_gufo(self, card: dict) -> bool:
        """Pinta la última sonda viva; GLib.idle_add la trae al hilo GTK."""
        self._refreshing = False
        self._gufo = card
        self._render_gufo()
        return False

    def _render_gufo(self) -> None:
        self._cards["gufo"].set_data(self._gufo or logica.gufo_card(None, None, ()))


class ConfirmRowBlock(Block):
    """Confirmaciones con cuenta regresiva + ciclo de vida de flash (legado)."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)

        confirmations = _Card("CONFIRMACIONES")
        confirmations.set_hexpand(True)
        self._conf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        confirmations.append(self._conf_box)
        row.append(confirmations)

        flash = _Card(logica.FLASH_LEGACY_TITLE)
        flash.set_hexpand(True)
        self._flash_line = Gtk.Label(label=logica.UNKNOWN, xalign=0)
        self._flash_line.add_css_class("mono")
        self._flash_line.set_wrap(True)
        hint = Gtk.Label(
            label="el router aún lista flash (:11008); es el motor IQ3_XXS retirado — histórico, no Gufo",
            xalign=0,
        )
        hint.add_css_class("muted")
        hint.set_wrap(True)
        flash.append(self._flash_line)
        flash.append(hint)
        row.append(flash)

        self.body.append(row)

        self._pending: list[dict] = []
        self._countdown_labels: list[tuple[Gtk.Label, float]] = []
        self._tick_id = GLib.timeout_add_seconds(1, self._tick)
        self.connect("destroy", self._on_destroy)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        self._flash_line.set_text(logica.flash_lifecycle_line(router.get("flash_lifecycle") or {}) or logica.UNKNOWN)

        now = time.monotonic()
        self._pending = [
            {**item, "_expires_at": now + logica.safe(item.get("expires_in_s"))}
            for item in logica.pending_confirmations(router)
        ]
        self._render_conf()

    def _render_conf(self) -> None:
        for child in tuple(self._conf_box.observe_children()):
            self._conf_box.remove(child)
        self._countdown_labels = []

        if not self._pending:
            label = Gtk.Label(label=logica.NOTHING_PENDING_TEXT, xalign=0)
            label.add_css_class("muted")
            self._conf_box.append(label)
            return

        for item in self._pending:
            row = logica.confirm_row(item)
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            kind = Gtk.Label(label=row["kind"], xalign=0)
            kind.set_markup(f"<b>{row['kind']}</b>")
            countdown = Gtk.Label(label=row["countdown"], xalign=1)
            countdown.add_css_class("tier-warn")
            countdown.add_css_class("mono")
            countdown.set_hexpand(True)
            head.append(kind)
            head.append(countdown)
            self._countdown_labels.append((countdown, item["_expires_at"]))
            self._conf_box.append(head)

            session = Gtk.Label(label=row["id"], xalign=0)
            session.add_css_class("mono")
            self._conf_box.append(session)

            if row["reason"]:
                reason = Gtk.Label(label=row["reason"], xalign=0)
                reason.add_css_class("muted")
                reason.set_wrap(True)
                self._conf_box.append(reason)

            for option in row["options"]:
                option_label = Gtk.Label(label=f"  {option}", xalign=0)
                option_label.add_css_class("mono")
                self._conf_box.append(option_label)

            if row["default"]:
                default = Gtk.Label(label=f"sin respuesta: {row['default']}", xalign=0)
                default.add_css_class("muted")
                self._conf_box.append(default)

    def _tick(self) -> bool:
        now = time.monotonic()
        for label, expires_at in self._countdown_labels:
            label.set_text(logica.countdown_left(expires_at - now))
        # Seguir aunque la página no esté a la vista: devolver False dejaba la
        # cuenta congelada para siempre y el destroy quitaba una fuente muerta.
        return GLib.SOURCE_CONTINUE

    def _on_destroy(self, *_args) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0


class TilesRowBlock(Block):
    """Teselas JEV/CLOUD/ENGRAM + sesiones compactas."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)

        tiles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tiles.set_hexpand(True)
        tiles.set_homogeneous(True)
        self._tiles = {
            key: router_vista.StatusTile(title)
            for key, title in (("jev", "JEV"), ("cloud", "CLOUD"), ("engram", "ENGRAM"))
        }
        for tile in self._tiles.values():
            tiles.append(tile)
        row.append(tiles)

        sessions = _Card("SESIONES")
        sessions.set_size_request(300, -1)
        self._sessions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sessions.append(self._sessions_box)
        row.append(sessions)

        self.body.append(row)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        for tile in logica.tiles(router, snapshot.telemetry()):
            self._tiles[tile["key"]].set_data(tile)
        self._render_sessions(router.get("sessions") or [])

    def _render_sessions(self, sessions: list[dict]) -> None:
        for child in tuple(self._sessions_box.observe_children()):
            self._sessions_box.remove(child)

        rows = logica.sessions_rows(sessions)[:12]
        if not rows:
            label = Gtk.Label(label="sin sesiones activas", xalign=0)
            label.add_css_class("muted")
            self._sessions_box.append(label)
            return

        for row in rows:
            text = (
                f"{row['id']} · {row['route']} · hace {row['age']} · "
                f"ctx {count(row['ctx']) if row['ctx'] is not None else '—'} tok"
            )
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class("mono")
            label.set_tooltip_text(
                f"{row['id']} · workflow {row['workflow']} · ruta {row['route']}"
            )
            self._sessions_box.append(label)
