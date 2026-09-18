"""Loaded models, expandable in place.

Clicking a model opens its detail **inside the card** (a revealer), not on
another page: the dashboard never navigates away. Opening one closes the others.
"""

# pyright: reportAttributeAccessIssue=false

from typing import Any

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import gib, safe, share
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.barras import MeterRow

ICONS = (("abito", "🤖"), ("personal", "🧠"))


def icon_for(alias: str) -> str:
    lowered = alias.lower()
    for needle, icon in ICONS:
        if needle in lowered:
            return icon
    return "🧩"


class ModelCard(Gtk.Box):
    """One model: name and summary always visible, detail revealed on click."""

    def __init__(self, alias: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.alias = alias
        self._on_open = None

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._name = Gtk.Label(label=f"{icon_for(alias)} {alias}", xalign=0)
        self._name.add_css_class("model-name")
        self._name.set_hexpand(True)
        header.append(self._name)

        self._summary = Gtk.Label(label="—", xalign=1)
        self._summary.add_css_class("mono")
        self._summary.add_css_class("muted")
        header.append(self._summary)
        self.append(header)

        self._revealer = Gtk.Revealer()
        self._revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        detail.set_margin_top(8)
        self._gtt = MeterRow("GTT", label_width=40, value_width=90)
        self._rss = MeterRow("RSS", label_width=40, value_width=90, kind="blue")
        self._cpu = MeterRow("CPU", label_width=40, value_width=90, kind="green")
        for meter in (self._gtt, self._rss, self._cpu):
            detail.append(meter)
        self._facts = chips.note("")
        detail.append(self._facts)

        self._revealer.set_child(detail)
        self.append(self._revealer)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self._toggle)
        self.add_controller(gesture)

    def set_open_callback(self, callback) -> None:
        self._on_open = callback

    def close(self) -> None:
        self._revealer.set_reveal_child(False)

    def _toggle(self, *_args) -> None:
        opening = not self._revealer.get_reveal_child()
        self._revealer.set_reveal_child(opening)
        if opening and self._on_open is not None:
            self._on_open(self)

    def set_model(
        self, model: dict[str, Any], gtt_peak: float, rss_peak: float
    ) -> None:
        gtt = safe(model.get("gtt_gib"))
        rss = safe(model.get("rss_gib"))
        cpu = safe(model.get("cpu"))
        port = model.get("port")

        self._summary.set_text(f"GTT {gib(gtt)}G · RSS {gib(rss)}G")
        self._gtt.set(share(gtt, gtt_peak), f"{gib(gtt)}G")
        self._rss.set(share(rss, rss_peak), f"{gib(rss)}G")
        self._cpu.set(min(1.0, cpu / 100), f"{gib(cpu)}%")
        self._facts.set_text(
            f"{model.get('model') or '—'} · PID {model.get('pid', '—')} · "
            f"puerto {port if port else '—'}"
        )


class ModelsBlock(Block):
    title = "MODELOS LOCALES"

    def __init__(self) -> None:
        super().__init__()
        self._cards: dict[str, ModelCard] = {}
        self._hint = chips.note(
            "Hacé clic en un modelo y el detalle se abre acá mismo."
        )
        self.body.append(self._hint)

    def _close_others(self, opened: ModelCard) -> None:
        for card in self._cards.values():
            if card is not opened:
                card.close()

    def update_snapshot(self, snapshot: Snapshot) -> None:
        models = snapshot.models()
        aliases = [str(model.get("alias") or "?") for model in models]

        if set(aliases) != set(self._cards):
            while (child := self.body.get_first_child()) is not None:
                self.body.remove(child)

            self._cards = {}
            for alias in aliases:
                card = ModelCard(alias)
                card.set_open_callback(self._close_others)
                self._cards[alias] = card
                self.body.append(card)
            self.body.append(self._hint)

        if not models:
            self._hint.set_text("Sin modelos locales cargados.")
            return

        gtt_peak = max((safe(model.get("gtt_gib")) for model in models), default=0.0)
        rss_peak = max((safe(model.get("rss_gib")) for model in models), default=0.0)
        for model in models:
            card = self._cards.get(str(model.get("alias") or "?"))
            if card is not None:
                card.set_model(model, gtt_peak, rss_peak)
