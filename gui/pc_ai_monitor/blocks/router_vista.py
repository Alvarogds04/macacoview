"""Widgets de la página Router reconstruida (diseño 07, T5, S12).

Piezas visuales reutilizables, todas leyendo el tema activo (``theme``):

* ``ColumnasApiladas`` — «Rutas en el tiempo»: columnas apiladas por minuto
  (local acento / cloud azul / cola ámbar), variante apilada de
  ``widgets.graficos.Columnas``;
* ``KpiStrip`` — franja de seis KPIs con etiqueta corta y unidad;
* ``StatusTile`` — tesela JEV/CLOUD/ENGRAM: lámpara + un valor, detalle en
  el tooltip (S12: menos texto, misma información);
* ``EngineCard`` — tarjeta de motor: lámpara de salud, slots/cola/MTP y la
  barra «ctx en uso».

Todo lo que decide valores vive en ``router_logica``; acá solo se pinta.
"""

# pyright: reportAttributeAccessIssue=false

from typing import ClassVar

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks.router_logica import ctx_text
from pc_ai_monitor.formato import count
from pc_ai_monitor.widgets.base import Chart, draw_text
from pc_ai_monitor.widgets.instrumentos import Lamp


class ColumnasApiladas(Chart):
    """«Rutas en el tiempo»: columnas apiladas por minuto local/cloud/cola."""

    CATEGORIES = ("local", "cloud", "cola")

    def __init__(self, height: int = 150) -> None:
        super().__init__(420, height)
        self._buckets: list[dict] = []
        self.set_hexpand(True)

    def set_data(self, buckets: list[dict]) -> None:
        self._buckets = list(buckets)
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _colors(self) -> dict[str, str]:
        palette = theme.active()
        return {
            "local": palette.accent,
            "cloud": palette.blue,
            "cola": palette.warning,
        }

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        colors = self._colors()
        baseline = height - 16
        top = 10
        buckets = self._buckets
        if not buckets:
            return

        peak = max(sum(bucket[cat] for cat in self.CATEGORIES) for bucket in buckets)
        count_buckets = len(buckets)
        left = 10
        gap = 2
        bar_width = max(3.0, min(24.0, (width - left * 2 - gap * (count_buckets - 1)) / count_buckets))
        label_step = max(1, round(count_buckets / 6))

        for index, bucket in enumerate(buckets):
            x = left + index * (bar_width + gap)
            y = baseline
            for category in self.CATEGORIES:
                value = bucket[category]
                if value <= 0 or peak <= 0:
                    continue
                segment = value / peak * (baseline - top)
                cr.set_source_rgba(*theme.rgba(colors[category]))
                cr.rectangle(x, y - segment, bar_width, segment)
                cr.fill()
                y -= segment

            total = sum(bucket[cat] for cat in self.CATEGORIES)
            if total > 0 and bar_width >= 12:
                draw_text(area, cr, str(int(total)), x + bar_width / 2, y - 6, palette.text, 8)
            if index % label_step == 0 or index == count_buckets - 1:
                draw_text(area, cr, bucket["label"], x + bar_width / 2, baseline + 8, palette.muted, 8)


class KpiStrip(Gtk.Box):
    """Franja de KPIs: etiqueta corta + valor grande, detalle en el tooltip."""

    def __init__(self, count_kpis: int = 6) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_homogeneous(True)
        self._pairs: list[tuple[Gtk.Label, Gtk.Label, Gtk.Box]] = []
        for _ in range(count_kpis):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("kpi")
            label = Gtk.Label(xalign=0)
            label.add_css_class("kpi-label")
            value = Gtk.Label(xalign=0)
            value.add_css_class("kpi-value")
            box.append(label)
            box.append(value)
            self.append(box)
            self._pairs.append((label, value, box))

    def set_values(self, kpis: list[dict[str, str]]) -> None:
        for (label, value, box), kpi in zip(self._pairs, kpis, strict=False):
            label.set_text(kpi["label"])
            value.set_text(kpi["value"])
            box.set_tooltip_text(kpi.get("tip") or "")

    def value_labels(self) -> list[Gtk.Label]:
        return [value for _label, value, _box in self._pairs]


class StatusTile(Gtk.Box):
    """Tesela de estado (JEV/CLOUD/ENGRAM): lámpara + un valor + tooltip."""

    # Estados de la lógica (ok/warn/crit) → lámparas de instrumentos.
    LAMP_MAP: ClassVar[dict[str, str]] = {"ok": "ok", "warn": "stale", "crit": "down"}

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("kpi")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._lamp = Lamp()
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("kpi-label")
        head.append(self._lamp)
        head.append(name)
        self.append(head)

        self._value = Gtk.Label(xalign=0)
        self._value.add_css_class("num")
        self._value.set_wrap(True)
        self.append(self._value)

    def set_data(self, tile: dict) -> None:
        self._lamp.set_data(self.LAMP_MAP.get(tile["lamp"], "off"))
        self._value.set_text(tile["value"])
        self.set_tooltip_text(tile.get("tip") or "")

    def redraw(self) -> None:
        self._lamp.redraw()


class EngineCard(Gtk.Box):
    """Tarjeta de motor: salud, slots/cola/MTP y la barra «ctx en uso»."""

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("kpi")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._lamp = Lamp()
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("kpi-label")
        name.set_hexpand(True)
        self._status = Gtk.Label(xalign=1)
        self._status.add_css_class("mono")
        head.append(self._lamp)
        head.append(name)
        head.append(self._status)
        self.append(head)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._slots = Gtk.Label(xalign=0)
        self._queue = Gtk.Label(xalign=0)
        self._mtp = Gtk.Label(xalign=0)
        for label in (self._slots, self._queue, self._mtp):
            label.add_css_class("muted")
            label.add_css_class("mono")
            meta.append(label)
        self.append(meta)

        ctx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctx_label = Gtk.Label(label="ctx en uso", xalign=0)
        ctx_label.add_css_class("muted")
        self._bar = Gtk.ProgressBar()
        self._bar.set_hexpand(True)
        self._bar.set_valign(Gtk.Align.CENTER)
        self._ctx_value = Gtk.Label(xalign=1)
        self._ctx_value.add_css_class("mono")
        ctx_row.append(ctx_label)
        ctx_row.append(self._bar)
        ctx_row.append(self._ctx_value)
        self.append(ctx_row)

    def set_data(self, card: dict) -> None:
        self._lamp.set_data(card["lamp"])
        self._status.set_text(card["status"])
        self._slots.set_text(f"slots {card['slots'] or '—'}")
        self._queue.set_text(
            f"cola {count(card['queue']) if card['queue'] is not None else '—'}"
        )
        self._mtp.set_text(f"MTP {card['mtp'] or '—'}")

        ctx_pct = card["ctx_pct"]
        self._bar.set_fraction((ctx_pct or 0.0) / 100.0)
        self._ctx_value.set_text(ctx_text(ctx_pct, card["ctx_total"]))

        self.set_tooltip_text(engine_tooltip(card))

    def redraw(self) -> None:
        self._lamp.redraw()


def engine_tooltip(card: dict) -> str:
    """Detalle de la tarjeta en el tooltip (S12): el texto va en la lógica."""
    parts = [
        f"{card['name']} · puerto {card['port'] or '—'}",
        f"estado {card['status']}",
        f"slots {card['slots'] or '—'}",
        f"cola {count(card['queue']) if card['queue'] is not None else '—'}",
        f"ctx {card['ctx_total'] or '—'}",
    ]
    if card["ctx_pct"] is None:
        parts.append("ventana en uso sin datos de la fuente")
    if card["mtp"]:
        parts.append(f"MTP {card['mtp']}")
    return " · ".join(parts)
