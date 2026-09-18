"""Widgets that hold a value and update in place.

A meter here is a ``Gtk.ProgressBar`` with a semantic colour class: the widget
owns the geometry, the theme owns the colour. Pure layout helpers (chips, cards)
live in ``chips``; the charts live in ``graficos``.
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

KINDS = ("accent", "blue", "green", "warning", "error")


def _kind(kind: str) -> str:
    return kind if kind in KINDS else "accent"


class MeterRow(Gtk.Box):
    """Label + native progress bar + exact value, updatable in place."""

    def __init__(
        self,
        label: str,
        label_width: int = 52,
        value_width: int = 150,
        kind: str = "accent",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name = Gtk.Label(label=label, xalign=0)
        name.set_size_request(label_width, -1)
        name.add_css_class("kpi-label")
        self.append(name)

        self._bar = Gtk.ProgressBar()
        self._bar.set_hexpand(True)
        self._bar.set_valign(Gtk.Align.CENTER)
        self._bar.add_css_class(f"bar-{_kind(kind)}")
        self.append(self._bar)

        self._value = Gtk.Label(label="—", xalign=1)
        self._value.add_css_class("mono")
        self._value.add_css_class("muted")
        self._value.set_size_request(value_width, -1)
        self.append(self._value)

    def set(self, fraction: float, text: str) -> None:
        self._bar.set_fraction(max(0.0, min(1.0, fraction)))
        self._value.set_text(text)


class KpiTile(Gtk.Box):
    """Big number with a small caption underneath."""

    def __init__(self, value: str, title: str, width: int = 158) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("kpi")
        self.set_size_request(width, -1)

        self._value = Gtk.Label(label=value, xalign=0)
        self._value.add_css_class("kpi-value")
        self.append(self._value)

        caption = Gtk.Label(label=title, xalign=0)
        caption.add_css_class("kpi-title")
        self.append(caption)

    def set_value(self, text: str) -> None:
        self._value.set_text(text)
