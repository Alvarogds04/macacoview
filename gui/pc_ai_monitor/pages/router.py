"""Router: página reconstruida del diseño 07 (T5).

Franja de KPIs, «Rutas en el tiempo» + «Decisiones» con filtros compartidos,
tarjetas Abito/Gufo (el flash del registro es el motor retirado, L9),
confirmaciones con cuenta regresiva, ciclo de vida de flash como legado,
teselas JEV/CLOUD/ENGRAM y sesiones. Se refresca con cada snapshot del
colector; sin router en :11009 la página entera se atenúa con su banner.
"""

from gi.repository import Gtk

from pc_ai_monitor.blocks import Dashboard, router_logica
from pc_ai_monitor.blocks.router import (
    ConfirmRowBlock,
    DecisionesBlock,
    EnginesBlock,
    KpisBlock,
    TilesRowBlock,
)


class RouterPage(Dashboard):
    """Las cinco filas del artboard «Router», vivas con cada snapshot."""

    blocks = (KpisBlock, DecisionesBlock, EnginesBlock, ConfirmRowBlock, TilesRowBlock)

    def __init__(self) -> None:
        super().__init__()
        body = self.get_child()
        if isinstance(body, Gtk.Viewport):  # ScrolledWindow envuelve en viewport
            body = body.get_child()
        assert isinstance(body, Gtk.Box)
        self._body = body
        self._banner = Gtk.Label(label=router_logica.NO_RESPONSE_TEXT, xalign=0)
        self._banner.add_css_class("tier-crit")
        self._banner.set_visible(False)
        self._body.prepend(self._banner)

    def update_snapshot(self, snapshot) -> None:
        down = router_logica.router_down(snapshot)
        self._banner.set_visible(down)
        self._body.set_opacity(0.45 if down else 1.0)
        super().update_snapshot(snapshot)
