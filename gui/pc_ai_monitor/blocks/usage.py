"""Token consumption: totals, providers, models and the Codex windows.

Everything comes from the external collector (``macacoview-tokens``); this module only
presents it. Providers and models are aggregated here, so no extra plumbing is
needed in the data layer.
"""

# pyright: reportAttributeAccessIssue=false

from typing import ClassVar

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.tokens_vista import (
    codex_note,
    model_columns,
    provider_slices,
    provider_totals,
    uncounted_note,
    window_colour,
    window_tooltip,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, money, percent, safe
from pc_ai_monitor.formato import tokens as fmt_tokens
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.barras import KpiTile
from pc_ai_monitor.widgets.graficos import Columnas, Dona, Slice


class TokenKpiBlock(Block):
    """Headline consumption of the Pi sessions."""

    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        self._tiles = {
            "total": KpiTile("—", "TOKENS EN PI"),
            "turns": KpiTile("—", "TURNOS"),
            "cost": KpiTile("—", "COSTO ACUMULADO"),
            "cache": KpiTile("—", "LEÍDOS DE CACHÉ"),
        }
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.set_homogeneous(True)
        tips = {
            "total": "Tokens que Pi acumuló hoy.",
            "turns": "Turnos de Pi hoy.",
            "cost": "Costo acumulado de hoy, en USD.",
            "cache": "Tokens servidos desde la caché de prompt.",
        }
        for name, tile in self._tiles.items():
            tile.set_hexpand(True)
            tile.set_tooltip_text(tips[name])
            strip.append(tile)
        self.body.append(strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        self._tiles["total"].set_value(fmt_tokens(safe(pi.get("total"))))
        self._tiles["turns"].set_value(count(safe(pi.get("turns"))))
        self._tiles["cost"].set_value(money(safe(pi.get("cost_usd"))))
        self._tiles["cache"].set_value(fmt_tokens(safe(pi.get("cache_read"))))


class CliKpiBlock(Block):
    """Headline consumption of one external CLI section, as a stat grid.

    The collector (``macacoview-tokens``) emits ``codex_cli`` and ``claude_code``
    with the same normalized fields, so the only per-CLI difference is which
    section to read: reasoning (thinking in Claude Code), cache_write (cache
    creation) and cache_read are metrics Pi's block does not show.
    The six tiles keep the ``_tiles`` interface test_core pins; the layout is
    the design's stat grid (3 columns x 2 rows).
    """

    title = ""
    section = ""

    TILE_TIPS: ClassVar[dict[str, str]] = {
        "total": "Tokens de entrada, salida y caché acumulados en el CLI.",
        "reasoning": "Tokens de razonamiento (thinking en Claude Code).",
        "cache_write": "Tokens escritos a la caché de prompt.",
        "cache_read": "Tokens leídos de la caché de prompt.",
        "turns": "Turnos de conversación registrados.",
        "cost": "Costo estimado acumulado, en USD.",
    }

    def __init__(self) -> None:
        super().__init__()
        self._tiles = {
            "total": KpiTile("—", "TOKENS"),
            "reasoning": KpiTile("—", "RAZONAMIENTO"),
            "cache_write": KpiTile("—", "ESCRITO EN CACHÉ"),
            "cache_read": KpiTile("—", "LEÍDO DE CACHÉ"),
            "turns": KpiTile("—", "TURNOS"),
            "cost": KpiTile("—", "COSTO"),
        }
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        for index, (name, tile) in enumerate(self._tiles.items()):
            tile.set_hexpand(True)
            tile.set_tooltip_text(self.TILE_TIPS[name])
            grid.attach(tile, index % 3, index // 3, 1, 1)
        self.body.append(grid)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        data = snapshot.tokens_section(self.section)
        self._tiles["total"].set_value(fmt_tokens(safe(data.get("total"))))
        self._tiles["reasoning"].set_value(fmt_tokens(safe(data.get("reasoning"))))
        self._tiles["cache_write"].set_value(fmt_tokens(safe(data.get("cache_write"))))
        self._tiles["cache_read"].set_value(fmt_tokens(safe(data.get("cache_read"))))
        self._tiles["turns"].set_value(count(safe(data.get("turns"))))
        self._tiles["cost"].set_value(money(safe(data.get("cost_usd"))))


class CodexCliKpiBlock(CliKpiBlock):
    """Codex CLI sessions: adds reasoning and cache-write tokens."""

    title = "CODEX CLI"
    section = "codex_cli"


class ClaudeCodeKpiBlock(CliKpiBlock):
    """Claude Code sessions: thinking maps to reasoning, cache_creation to
    cache_write (the collector normalizes both schemas)."""

    title = "CLAUDE CODE"
    section = "claude_code"


class ProviderDonutBlock(Block):
    """Where the tokens went, grouped by provider."""

    title = "POR PROVEEDOR"

    def __init__(self) -> None:
        super().__init__()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self._donut = Dona(size=180, thickness=24, on_select=self._focus)
        row.append(self._donut)

        legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # Columna acotada como en el diseño (290 px): los valores no se van
        # al borde derecho del card. El hexpand=False explícito corta la
        # propagación del hexpand del nombre (GTK4 la sube por los padres).
        legend.set_hexpand(False)
        legend.set_size_request(300, -1)
        legend.set_valign(Gtk.Align.CENTER)
        self._legend = legend
        row.append(legend)
        self.body.append(row)

        self._note = chips.note("Hacé clic en un proveedor para ver sus modelos.")
        self.body.append(self._note)
        self._focused = ""

    def _focus(self, slice_: Slice) -> None:
        """Clic en una porción del donut: filtra; clic de nuevo, limpia."""
        self._focused = "" if slice_.label == self._focused else slice_.label
        self._render()

    def _legend_row(self, name: str, value: float, grand: float) -> Gtk.Box:
        """Una fila de leyenda clicable: pulsa el mismo filtro que el donut."""
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.add_css_class("legend-row")

        index = list(self._totals).index(name)
        swatch = Gtk.Label()
        swatch.set_markup(
            f'<span foreground="{theme.active().chart_series(index)}">●</span>'
        )
        line.append(swatch)

        label = Gtk.Label(label=name, xalign=0)
        label.set_hexpand(True)
        label.set_ellipsize(3)
        line.append(label)

        amount = Gtk.Label(label=fmt_tokens(value), xalign=1)
        amount.add_css_class("mono")
        amount.set_size_request(80, -1)
        line.append(amount)

        pct = Gtk.Label(label=percent(value, grand), xalign=1)
        pct.add_css_class("mono")
        pct.add_css_class("muted")
        pct.set_size_request(48, -1)
        line.append(pct)

        line.set_tooltip_text(
            f"{name} · {fmt_tokens(value)} tokens"
            + (" · clic para volver a todos" if name == self._focused else "")
        )

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self._on_row_pressed, name)
        line.add_controller(gesture)
        return line

    def _model_row(self, provider: str, name: str) -> Gtk.Box:
        """Sub-fila del proveedor enfocado: sus modelos con su total."""
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.add_css_class("legend-row")
        line.set_margin_start(18)

        index = list(self._totals).index(provider)
        swatch = Gtk.Label()
        swatch.set_markup(
            f'<span foreground="{theme.active().chart_series(index)}">●</span>'
        )
        line.append(swatch)

        label = Gtk.Label(label=name, xalign=0)
        label.set_hexpand(True)
        label.set_ellipsize(3)
        line.append(label)

        amount = Gtk.Label(
            label=fmt_tokens(self._models.get((provider, name), 0.0)), xalign=1
        )
        amount.add_css_class("mono")
        amount.add_css_class("muted")
        amount.set_size_request(80, -1)
        line.append(amount)

        pad = Gtk.Label(xalign=1)
        pad.set_size_request(48, -1)
        line.append(pad)
        return line

    def _on_row_pressed(
        self, _gesture, _count: int, _x: float, _y: float, name: str
    ) -> None:
        self._focus(Slice(label=name, value=0.0, color=""))

    def _render(self) -> None:
        while (child := self._legend.get_first_child()) is not None:
            self._legend.remove(child)

        totals = self._totals
        grand = sum(totals.values())
        shown = sorted(totals.items(), key=lambda item: item[1], reverse=True)

        for name, value in shown:
            if self._focused and name != self._focused:
                continue
            self._legend.append(self._legend_row(name, value, grand))
            # El diseño muestra los modelos del proveedor enfocado: sub-filas.
            if name == self._focused:
                for model_name in self._models_by_provider.get(name, []):
                    self._legend.append(self._model_row(name, model_name))

        if self._focused:
            self._note.set_text(
                f"Filtrado por {self._focused}. Clic de nuevo para ver todo."
            )
        else:
            self._note.set_text("Hacé clic en un proveedor para ver sus modelos.")

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        totals = provider_totals(pi.get("models") or [])
        self._totals = totals

        # (proveedor, modelo) -> tokens, para las sub-filas del enfocado.
        self._models = {
            (
                str(m.get("provider") or "sin proveedor"),
                str(m.get("name") or "?"),
            ): safe(m.get("total"))
            for m in (pi.get("models") or [])
        }
        self._models_by_provider: dict[str, list[str]] = {}
        for m in sorted(
            pi.get("models") or [], key=lambda m: safe(m.get("total")), reverse=True
        ):
            provider = str(m.get("provider") or "sin proveedor")
            if safe(m.get("total")) > 0:
                self._models_by_provider.setdefault(provider, []).append(
                    str(m.get("name") or "?")
                )

        grand = sum(totals.values())
        slices = provider_slices(
            totals,
            colour_for=lambda _name, index: theme.active().chart_series(index),
        )
        self._donut.set_data(slices, fmt_tokens(grand), "tokens")
        if self._focused and self._focused not in totals:
            self._focused = ""
        self._render()

    def redraw(self) -> None:
        self._donut.queue_draw()


class ModelTokensBlock(Block):
    """Token totals per model: top N by total, the rest grouped (L21.5).

    Con 53 modelos el gráfico era una franja ilegible: quedan los top N y
    «resto (M)». Los nombres se recortan con elipsis y el nombre completo
    vive en el tooltip de cada columna.
    """

    title = "POR MODELO"

    def __init__(self) -> None:
        super().__init__()
        self._columns = Columnas(height=170)
        self.body.append(self._columns)
        self._tooltips: list[str] = []
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self._columns.add_controller(motion)
        self._note = chips.note("")
        self.body.append(self._note)

    def _on_motion(self, _controller, x: float, y: float) -> None:
        """Tooltip por columna: el nombre completo vive acá, no en la barra."""
        text = ""
        for index, (item, rx, ry, rw, rh) in enumerate(
            getattr(self._columns, "_rects", [])
        ):
            del item
            if rx - 4 <= x <= rx + rw + 4 and ry - 14 <= y <= ry + rh + 16:
                text = self._tooltips[index] if index < len(self._tooltips) else ""
                break
        self._columns.set_tooltip_text(text)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        models = pi.get("models") or []

        slices, self._tooltips = model_columns(
            models,
            colour_for=lambda _name, index: theme.active().chart_series(index),
        )
        self._columns.set_data(slices)
        self._note.set_text(uncounted_note(models))

    def redraw(self) -> None:
        self._columns.queue_draw()


class CodexBlock(Block):
    """Subscription windows: percentages, not tokens."""

    title = "SUSCRIPCIÓN CODEX"

    def __init__(self) -> None:
        super().__init__()
        self._row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.body.append(self._row)
        self._note = chips.note("La suscripción se mide como porcentaje de la ventana.")
        self.body.append(self._note)
        self._donuts: dict[str, Dona] = {}

    def _pressure(self, used: float) -> str:
        """Umbral compartido con tokens_vista (≥70 ámbar, ≥90 rojo)."""
        palette = theme.active()
        return window_colour(
            used, error=palette.error, warning=palette.warning, green=palette.green
        )

    def update_snapshot(self, snapshot: Snapshot) -> None:
        codex = snapshot.tokens_section("codex")
        windows = codex.get("windows") or []

        while (child := self._row.get_first_child()) is not None:
            self._row.remove(child)
        self._donuts = {}

        self._note.set_text(codex_note(codex.get("plan"), windows))
        for window in windows:
            used = safe(window.get("used_percent"))
            label = str(window.get("label") or "ventana")
            donut = Dona(size=140, thickness=18)
            donut.set_data(
                [
                    Slice(label="consumido", value=used, color=self._pressure(used)),
                    Slice(
                        label="disponible", value=max(0.0, 100 - used), color="#2c3a52"
                    ),
                ],
                f"{used:.0f}%",
                label,
            )
            donut.set_tooltip_text(window_tooltip(label, used))
            self._donuts[label] = donut
            self._row.append(donut)

    def redraw(self) -> None:
        for donut in self._donuts.values():
            donut.queue_draw()
