"""Home: el cockpit de MacacoView.

T1 lo deja como placeholder; T3 lo llena con los bloques del diseño 07
(RAM, banda de motores, lanes decode/prefill, inventario y tarjeta de
actualización).
"""

# PyGObject no trae stubs propios; ver gui/typings/gi.
# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block, Dashboard


class _HomeNote(Block):
    """El único contenido de T1: un aviso corto y apagado."""

    frame = False

    def __init__(self) -> None:
        super().__init__()
        note = Gtk.Label(label="Home: en construcción", xalign=0)
        note.add_css_class("muted")
        self.body.append(note)


class HomePage(Dashboard):
    blocks = (_HomeNote,)
