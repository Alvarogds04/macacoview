"""Motores: la página de detalle del diseño 07 (T4).

Dos columnas como el artboard: a la izquierda «Motor residente»
(``motores_vista.ResidenteVistaBlock``, tarjetas con lámpara, estado, ctx y
las dos cifras de RAM S9) y «Actualizaciones del PC»
(``actualizaciones.ActualizacionesBlock``, la lista de todo lo actualizable
con sus confirmaciones); a la derecha «Rendimiento del motor» como
instrumentos (``motores_vista.RendimientoVistaBlock``) y «Motores locales»
(``motores.MotoresBlock``, el inventario dinámico de siempre).

La tarjeta larga de texto del motor (``blocks.engine``) y la fila de texto
de rendimiento (``blocks.rendimiento``) ya no se muestran acá: Home lleva
la tarjeta de un solo estado (S6) y esta página muestra la lista y los
instrumentos (S7).
"""

# PyGObject no trae stubs propios; ver gui/typings/gi.
# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.actualizaciones import ActualizacionesBlock
from pc_ai_monitor.blocks.motores import MotoresBlock
from pc_ai_monitor.blocks.motores_vista import (
    RendimientoVistaBlock,
    ResidenteVistaBlock,
)


class MotoresPage(Dashboard):
    """Dos columnas: residente + actualizaciones | rendimiento + inventario."""

    blocks = (
        ResidenteVistaBlock,
        ActualizacionesBlock,
        RendimientoVistaBlock,
        MotoresBlock,
    )

    def __init__(self) -> None:
        super().__init__()

        body = self.get_child()
        if isinstance(body, Gtk.Viewport):
            body = body.get_child()
        body.set_spacing(12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        residente, actualizaciones, rendimiento, inventario = self._blocks
        for block in self._blocks:
            body.remove(block)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.set_hexpand(True)
        left.append(residente)
        left.append(actualizaciones)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.set_hexpand(True)
        right.append(rendimiento)
        right.append(inventario)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(left)
        row.append(right)
        body.append(row)
