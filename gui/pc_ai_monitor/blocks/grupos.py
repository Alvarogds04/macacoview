"""Process groups: one donut plus a legend with exact values."""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import GROUP_KEYS, Snapshot
from pc_ai_monitor.formato import gib, percent, safe, whole
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.graficos import Dona, Slice

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
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        self._donut = Dona(size=170, thickness=22)
        row.append(self._donut)

        legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._rows: dict[str, tuple[Gtk.Box, object, object]] = {}
        for key, label in GROUP_LABELS.items():
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("legend-row")

            name = Gtk.Label(label=label, xalign=0)
            name.set_hexpand(True)
            line.append(name)

            value = Gtk.Label(label="—", xalign=1)
            value.add_css_class("mono")
            value.set_size_request(90, -1)
            line.append(value)

            pct = Gtk.Label(label="—", xalign=1)
            pct.add_css_class("mono")
            pct.set_size_request(48, -1)
            line.append(pct)

            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._on_row_pressed, key)
            line.add_controller(gesture)

            legend.append(line)
            self._rows[key] = (line, value, pct)

        row.append(legend)
        self.body.append(row)

        self._detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._revealer = Gtk.Revealer()
        self._revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._revealer.set_child(self._detail)
        self.body.append(self._revealer)

        self._snapshot = None
        self._selected = ""

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        groups = snapshot.groups()
        hidden = set(snapshot.hidden_groups)
        visible_keys = [key for key in GROUP_KEYS if key not in hidden]
        values = {
            key: safe((groups.get(key) or {}).get("rss_gib")) for key in GROUP_KEYS
        }
        # El color de cada serie queda anclado a su posición en GROUP_KEYS
        # aunque se oculten entradas: la paleta no se corre.
        total = sum(values[key] for key in visible_keys)

        slices = [
            Slice(
                label=GROUP_LABELS[key],
                value=values[key],
                color=theme.active().chart_series(index),
            )
            for index, key in enumerate(GROUP_KEYS)
            if key not in hidden
        ]
        self._donut.set_data(slices, f"{gib(total, 2)}G", "RSS total")

        if self._selected and self._selected in hidden:
            self._selected = ""
            self._revealer.set_reveal_child(False)
        elif self._revealer.get_reveal_child() and self._selected:
            self._render_detail(self._selected)

        for key in GROUP_KEYS:
            line, value, pct = self._rows[key]
            line.set_visible(key not in hidden)
            value.set_text(f"{gib(values[key], 2)}G")
            pct.set_text(percent(values[key], total))

    def redraw(self) -> None:
        self._donut.queue_draw()

    # -- detalle en la misma pantalla ---------------------------------------

    def _on_row_pressed(
        self, _gesture, _count: int, _x: float, _y: float, key: str
    ) -> None:
        if self._selected == key and self._revealer.get_reveal_child():
            self._revealer.set_reveal_child(False)
            self._selected = ""
            return

        self._selected = key
        self._render_detail(key)
        self._revealer.set_reveal_child(True)

    def _render_detail(self, key: str) -> None:
        while (child := self._detail.get_first_child()) is not None:
            self._detail.remove(child)

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
        found = [p for p in self._snapshot.processes() if whole(p.get("pid")) in wanted]
        return sorted(found, key=lambda p: safe(p.get("rss_kb")), reverse=True)[:10]
