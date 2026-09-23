"""Five-minute history: one sparkline per series."""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import GROUP_KEYS, Snapshot
from pc_ai_monitor.formato import gib
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.graficos import Sparkline

SERIES = (
    ("used", "RAM usada"),
    ("avail", "RAM disponible"),
    ("pi", "🥧 Pi"),
    ("firefox", "🦊 Firefox"),
)


class HistoryBlock(Block):
    title = "HISTORIAL (5 MINUTOS)"

    def __init__(self) -> None:
        super().__init__()
        self._sparks: dict[str, Sparkline] = {}
        self._values: dict[str, Gtk.Label] = {}
        self._rows: dict[str, Gtk.Box] = {}

        for index, (key, label) in enumerate(SERIES):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self._rows[key] = row

            name = Gtk.Label(label=label, xalign=0)
            name.set_size_request(120, -1)
            name.add_css_class("muted")
            row.append(name)

            spark = Sparkline(height=34, color=theme.active().chart_series(index))
            row.append(spark)
            self._sparks[key] = spark

            value = Gtk.Label(label="—", xalign=1)
            value.add_css_class("mono")
            value.set_size_request(74, -1)
            row.append(value)
            self._values[key] = value

            self.body.append(row)

        self._note = chips.note("Acumulando historial…")
        self.body.append(self._note)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        samples = snapshot.history
        hidden = set(snapshot.hidden_groups)
        for key, _label in SERIES:
            if key in self._rows:
                # Ocultar es sólo visual: la muestra sigue trayendo la columna
                # completa (por posición sobre GROUP_KEYS), asi que si la
                # entrada vuelve, la serie retoma sin huecos.
                self._rows[key].set_visible(key not in hidden)
        self._note.set_text(
            f"Ventana: {len(samples)} muestras a 1 Hz"
            if samples
            else "Acumulando historial…"
        )
        if len(samples) < 2:
            return

        series: dict[str, list[float]] = {key: [] for key, _label in SERIES}
        for sample in samples:
            series["used"].append(sample.used_gib)
            series["avail"].append(sample.available_gib)
            # GROUP_KEYS completo siempre: el índice es por posición y ocultar
            # una entrada no puede correr los valores de las demás.
            for key in ("pi", "firefox"):
                index = GROUP_KEYS.index(key)
                series[key].append(
                    sample.groups[index] if index < len(sample.groups) else 0.0
                )

        for key, values in series.items():
            self._sparks[key].set_data(values)
            self._values[key].set_text(f"{gib(values[-1])}G")

    def redraw(self) -> None:
        for spark in self._sparks.values():
            spark.queue_draw()
