"""Process groups: a treemap plus a legend with exact values.

The treemap is the picture (biggest block = biggest RSS); the legend keeps
the exact numbers and is the click target: clicking a row — or a tile —
opens that group's process list right below, in place. The Pi group is the
exception (S17, design 08 tab 4): when the collector reports ``pi_sessions``
and the page listens through ``on_pi_sessions``, the block does not draw the
sessions itself — it hands them to the page, which shows the wide sessions
card next to this one. Without a listener, or with an older collector, Pi
keeps the process list like every other group.
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk, Pango

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks import programas_vista as programas
from pc_ai_monitor.blocks.recursos_vista import (
    GroupTreemap,
    top_processes,
    treemap_slices,
    visible_total,
)
from pc_ai_monitor.datos import GROUP_KEYS, Snapshot
from pc_ai_monitor.formato import gib, percent, safe, whole
from pc_ai_monitor.widgets import chips

# Ancho de contenido del treemap y de las columnas de la leyenda: normal
# (media página) y compacto (la ficha de 250 px junto a las sesiones de Pi).
_TREEMAP_WIDTH = {False: 560, True: 160}
_VALUE_WIDTH = {False: 90, True: 64}
_PCT_WIDTH = {False: 48, True: 40}
COMPACT_NOTE = (
    "Pi abierto a la izquierda; tocar la ficha Pi otra vez lo cierra. "
    "Los demás grupos siguen con su top 10 de procesos."
)

GROUP_LABELS = {
    "pi": "🥧 Pi",
    "hermes": "🪽 Hermes",
    "firefox": "🦊 Firefox",
    "system": "⚙️ Sistema (root)",
    "other": "📦 Otros",
}


class GroupsBlock(Block):
    title = "APLICACIONES / GRUPOS (RSS)"

    def __init__(self) -> None:
        super().__init__()
        # ``_donut`` conserva el nombre que test_core fija; hoy es el treemap
        # del diseño (misma interfaz: _slices / _center_value / _center_label).
        self._donut = GroupTreemap(on_select=self._on_tile_selected)
        self.body.append(self._donut)

        legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._rows: dict[str, tuple[Gtk.Box, Gtk.Label, Gtk.Label]] = {}
        self._names: list[Gtk.Label] = []
        for key, label in GROUP_LABELS.items():
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("legend-row")

            swatch = Gtk.Label()
            swatch.set_markup(
                f'<span foreground="{theme.active().chart_series(GROUP_KEYS.index(key))}">●</span>'
            )
            line.append(swatch)

            name = Gtk.Label(label=label, xalign=0)
            name.set_hexpand(True)
            line.append(name)
            self._names.append(name)

            value = Gtk.Label(label="—", xalign=1)
            value.add_css_class("mono")
            value.set_size_request(_VALUE_WIDTH[False], -1)
            line.append(value)

            pct = Gtk.Label(label="—", xalign=1)
            pct.add_css_class("mono")
            pct.set_size_request(_PCT_WIDTH[False], -1)
            line.append(pct)

            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._on_row_pressed, key)
            line.add_controller(gesture)

            legend.append(line)
            self._rows[key] = (line, value, pct)

        self.body.append(legend)

        self._compact_note = Gtk.Label(label=COMPACT_NOTE, xalign=0)
        self._compact_note.add_css_class("muted")
        self._compact_note.set_wrap(True)
        self._compact_note.set_max_width_chars(24)
        self._compact_note.set_visible(False)
        self.body.append(self._compact_note)

        # RAM por programa (L23): lista aparte, fuera del treemap y del «RSS
        # total» — PSS/RSS de programas se superponen con los grupos.
        self._programs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._programs.set_visible(False)
        self.body.append(self._programs)

        self._detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._revealer = Gtk.Revealer()
        self._revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._revealer.set_child(self._detail)
        self.body.append(self._revealer)

        self._snapshot = None
        self._selected = ""
        # Quién muestra las sesiones de Pi: la página (una sola vista, ancha).
        # Recibe (sesiones, RSS del grupo) al abrir y en cada snapshot, y
        # (None, 0.0) al cerrar.
        self.on_pi_sessions = None
        self._pi_open = False

    def set_compact(self, compact: bool) -> None:
        """Fit a narrow column (08 tab 4: 250 px beside the Pi sessions)."""
        self._donut.set_content_width(_TREEMAP_WIDTH[compact])
        # Natural widths capped too: a Gtk.Box hands spare room out by
        # natural size, so a long name or the note would widen the column.
        for name in self._names:
            name.set_ellipsize(
                Pango.EllipsizeMode.END if compact else Pango.EllipsizeMode.NONE
            )
            name.set_max_width_chars(8 if compact else -1)
        for _line, value, pct in self._rows.values():
            value.set_size_request(_VALUE_WIDTH[compact], -1)
            pct.set_size_request(_PCT_WIDTH[compact], -1)
        self._compact_note.set_visible(compact)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        groups = snapshot.groups()
        hidden = set(snapshot.hidden_groups)
        values = {
            key: safe((groups.get(key) or {}).get("rss_gib")) for key in GROUP_KEYS
        }
        # El color de cada serie queda anclado a su posición en GROUP_KEYS
        # aunque se oculten entradas: la paleta no se corre.
        total = visible_total(values, hidden)

        slices = treemap_slices(
            values,
            hidden=hidden,
            colour_for=lambda key: theme.active().chart_series(GROUP_KEYS.index(key)),
            label_for=lambda key: GROUP_LABELS[key],
        )
        self._donut.set_data(slices, f"{gib(total, 2)}G", "RSS total")

        if self._selected and self._selected in hidden:
            self._close()
        elif self._is_open():
            self._show(self._selected)

        for key in GROUP_KEYS:
            line, value, pct = self._rows[key]
            line.set_visible(key not in hidden)
            value.set_text(f"{gib(values[key], 2)}G")
            pct.set_text(percent(values[key], total))
            line.set_tooltip_text(
                f"{GROUP_LABELS[key]} · {gib(values[key], 2)} GiB · "
                f"{percent(values[key], total, 1)} del RSS visible"
            )

        self._render_programs(snapshot)

    def _render_programs(self, snapshot: Snapshot) -> None:
        while (child := self._programs.get_first_child()) is not None:
            self._programs.remove(child)
        rows = programas.selected_programs(
            snapshot.programs(), snapshot.shown_program_ids
        )
        self._programs.set_visible(bool(rows))
        if not rows:
            return

        heading = Gtk.Label(label=programas.RECURSOS_HEADING, xalign=0)
        heading.add_css_class("section-head")
        heading.set_tooltip_text(programas.RECURSOS_TIP)
        self._programs.append(heading)
        for key, program in rows:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("legend-row")
            if program is None:
                name = Gtk.Label(
                    label=f"Programa · {programas.short_id(key)}", xalign=0
                )
                value = Gtk.Label(label="ausente", xalign=1)
                line.set_tooltip_text(programas.ABSENT_SUBTITLE)
            else:
                name = Gtk.Label(label=programas.program_title(program, key), xalign=0)
                value = Gtk.Label(
                    label=f"{gib(programas.program_gib(program), 2)}G "
                    f"{programas.metric_label(program)}",
                    xalign=1,
                )
                line.set_tooltip_text(programas.program_subtitle(program))
            name.set_use_markup(False)
            name.set_hexpand(True)
            name.set_ellipsize(3)
            line.append(name)
            value.add_css_class("mono")
            value.add_css_class("muted")
            line.append(value)
            self._programs.append(line)

    def redraw(self) -> None:
        self._donut.queue_draw()

    # -- detalle en la misma pantalla ---------------------------------------

    def _toggle(self, key: str) -> None:
        if self._selected == key and self._is_open():
            self._close()
            return
        self._selected = key
        self._show(key)

    def _is_open(self) -> bool:
        return bool(self._selected) and (
            self._pi_open or self._revealer.get_reveal_child()
        )

    def _show(self, key: str) -> None:
        sessions = self._snapshot.pi_sessions() if self._snapshot is not None else None
        if key == "pi" and sessions is not None and self.on_pi_sessions is not None:
            self._revealer.set_reveal_child(False)
            self._clear_detail()
            self._pi_open = True
            pi = (self._snapshot.groups().get("pi") if self._snapshot else None) or {}
            self.on_pi_sessions(sessions, safe(pi.get("rss_gib")))
            return
        self._release_pi()
        self._render_detail(key)
        self._revealer.set_reveal_child(True)

    def _close(self) -> None:
        self._selected = ""
        self._revealer.set_reveal_child(False)
        self._release_pi()

    def _release_pi(self) -> None:
        if self._pi_open:
            self._pi_open = False
            if self.on_pi_sessions is not None:
                self.on_pi_sessions(None, 0.0)

    def _clear_detail(self) -> None:
        while (child := self._detail.get_first_child()) is not None:
            self._detail.remove(child)

    def _on_row_pressed(
        self, _gesture, _count: int, _x: float, _y: float, key: str
    ) -> None:
        self._toggle(key)

    def _on_tile_selected(self, slice_) -> None:
        for key, label in GROUP_LABELS.items():
            if label == slice_.label:
                self._toggle(key)
                return

    def _render_detail(self, key: str) -> None:
        self._clear_detail()

        heading = Gtk.Label(label=f"PROCESOS DE {GROUP_LABELS[key].upper()}", xalign=0)
        heading.add_css_class("section-head")
        self._detail.append(heading)

        processes = self._processes_of(key)
        if not processes:
            self._detail.append(chips.note("Este grupo no tiene procesos ahora mismo."))
            return

        for process in processes:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("legend-row")

            name = Gtk.Label(label=str(process.get("comm") or "—"), xalign=0)
            name.set_hexpand(True)
            name.set_ellipsize(3)
            name.set_tooltip_text(
                str(process.get("args") or process.get("comm") or "—")
            )
            line.append(name)

            for text, width in (
                (f"PID {process.get('pid', '—')}", 110),
                (f"{safe(process.get('rss_kb')) / 1024 / 1024:.2f}G", 80),
                (f"{gib(process.get('cpu'))}%", 70),
            ):
                label = Gtk.Label(label=text, xalign=1)
                label.set_size_request(width, -1)
                label.add_css_class("mono")
                label.add_css_class("muted")
                line.append(label)

            self._detail.append(line)

    def _processes_of(self, key: str) -> list:
        if self._snapshot is None:
            return []
        entry = self._snapshot.groups().get(key) or {}
        wanted = {whole(pid) for pid in (entry.get("pids") or [])}
        return top_processes(self._snapshot.processes(), wanted)
