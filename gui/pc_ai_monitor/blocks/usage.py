"""Token consumption: totals, providers, models and the Codex windows.

Everything comes from the external collector (``pc-ai-tokens``); this module only
presents it. Providers and models are aggregated here, so no extra plumbing is
needed in the data layer.
"""

# pyright: reportAttributeAccessIssue=false

from collections import defaultdict

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, money, percent, safe
from pc_ai_monitor.formato import tokens as fmt_tokens
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.barras import KpiTile
from pc_ai_monitor.widgets.graficos import Columnas, Dona, Slice

LOCAL_PROVIDERS = {"llamacpp", "abito-direct", "ollama", "magnitude"}
TOP_SLICES = 8


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
        for tile in self._tiles.values():
            strip.append(tile)
        self.body.append(strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        self._tiles["total"].set_value(fmt_tokens(safe(pi.get("total"))))
        self._tiles["turns"].set_value(count(safe(pi.get("turns"))))
        self._tiles["cost"].set_value(money(safe(pi.get("cost_usd"))))
        self._tiles["cache"].set_value(fmt_tokens(safe(pi.get("cache_read"))))


class CliKpiBlock(Block):
    """Headline consumption of one external CLI section.

    The collector (``pc-ai-tokens``) emits ``codex_cli`` and ``claude_code``
    with the same normalized fields, so the only per-CLI difference is which
    section to read: reasoning (thinking in Claude Code), cache_write (cache
    creation) and cache_read are metrics Pi's block does not show.
    """

    title = ""
    frame = False
    section = ""

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
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for tile in self._tiles.values():
            strip.append(tile)
        self.body.append(strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        data = snapshot.tokens_section(self.section)
        self._tiles["total"].set_value(fmt_tokens(safe(data.get("total"))))
        self._tiles["reasoning"].set_value(fmt_tokens(safe(data.get("reasoning"))))
        self._tiles["cache_write"].set_value(
            fmt_tokens(safe(data.get("cache_write")))
        )
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
        self._legend = legend
        row.append(legend)
        self.body.append(row)

        self._note = chips.note("Hacé clic en un proveedor para ver sus modelos.")
        self.body.append(self._note)
        self._focused = ""

    def _focus(self, slice_: Slice) -> None:
        self._focused = "" if slice_.label == self._focused else slice_.label
        self._render()

    def _render(self) -> None:
        while (child := self._legend.get_first_child()) is not None:
            self._legend.remove(child)

        totals = self._totals
        grand = sum(totals.values())
        shown = sorted(totals.items(), key=lambda item: item[1], reverse=True)

        for name, value in shown:
            if self._focused and name != self._focused:
                continue
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.add_css_class("legend-row")

            swatch = Gtk.Label(label="●")
            swatch.add_css_class("mono")
            swatch.add_css_class("muted")
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

            self._legend.append(line)

        if self._focused:
            self._note.set_text(
                f"Filtrado por {self._focused}. Clic de nuevo para ver todo."
            )

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        totals: dict[str, float] = defaultdict(float)
        for model in pi.get("models") or []:
            provider = str(model.get("provider") or "sin proveedor")
            totals[provider] += safe(model.get("total"))

        self._totals = dict(totals)
        grand = sum(totals.values())
        ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)

        slices = [
            Slice(label=name, value=value, color=theme.active().chart_series(index))
            for index, (name, value) in enumerate(ordered[:TOP_SLICES])
        ]
        rest = sum(value for _name, value in ordered[TOP_SLICES:])
        if rest > 0:
            slices.append(
                Slice(
                    label=f"resto ({len(ordered) - TOP_SLICES})",
                    value=rest,
                    color="#5a6b7d",
                )
            )

        self._donut.set_data(slices, fmt_tokens(grand), "tokens")
        self._render()

    def redraw(self) -> None:
        self._donut.queue_draw()


class ModelTokensBlock(Block):
    """Token totals per model, columns scaled to the largest one."""

    title = "POR MODELO"

    def __init__(self) -> None:
        super().__init__()
        self._columns = Columnas(height=170)
        self.body.append(self._columns)
        self._note = chips.note("")
        self.body.append(self._note)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        pi = snapshot.tokens_section("pi")
        models = [m for m in (pi.get("models") or []) if safe(m.get("total")) > 0]
        models.sort(key=lambda m: safe(m.get("total")), reverse=True)

        slices = [
            Slice(
                label=str(model.get("name") or "?"),
                value=safe(model.get("total")),
                color=theme.active().chart_series(index),
            )
            for index, model in enumerate(models)
        ]
        self._columns.set_data(slices)

        uncounted = [m for m in (pi.get("models") or []) if safe(m.get("total")) == 0]
        if uncounted:
            listed = ", ".join(
                f"{m.get('name')} ({count(safe(m.get('turns')))} turnos)"
                for m in uncounted[:6]
            )
            self._note.set_text(f"Sin conteo de tokens (Pi no los recibe): {listed}.")
        else:
            self._note.set_text("")

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
        if used >= 90:
            return theme.active().error
        if used >= 70:
            return theme.active().warning
        return theme.active().green

    def update_snapshot(self, snapshot: Snapshot) -> None:
        codex = snapshot.tokens_section("codex")
        windows = codex.get("windows") or []

        while (child := self._row.get_first_child()) is not None:
            self._row.remove(child)
        self._donuts = {}

        if not windows:
            self._note.set_text("Sin ventanas informadas por codexbar.")
            return

        self._note.set_text(
            f"Plan {codex.get('plan') or '—'} · resetea "
            f"{windows[0].get('resets_at', '—')}"
        )
        for window in windows:
            used = safe(window.get("used_percent"))
            donut = Dona(size=140, thickness=18)
            donut.set_data(
                [
                    Slice(label="consumido", value=used, color=self._pressure(used)),
                    Slice(
                        label="disponible", value=max(0.0, 100 - used), color="#2c3a52"
                    ),
                ],
                f"{used:.0f}%",
                str(window.get("label") or "ventana"),
            )
            self._donuts[str(window.get("label"))] = donut
            self._row.append(donut)

    def redraw(self) -> None:
        for donut in self._donuts.values():
            donut.queue_draw()
