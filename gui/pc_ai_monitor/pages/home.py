"""Home: el cockpit de MacacoView (diseño 07, T3).

Primera fila — RAM + banda de motores + mini tarjetas con el cambio compacto
(S4, Ctrl E); segunda — lanes decode/prefill con cursor compartido y los
instrumentos del motor; tercera — inventario + margen RAM + la tarjeta de
actualización de un solo estado (S6, Ctrl U). Todos los bloques comparten el
poller de ``blocks.home``; los atajos disparan las mismas confirmaciones y
comandos que el panel Motores.
"""

# PyGObject no trae stubs propios; ver gui/typings/gi.
# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

import pc_ai_monitor.blocks.home as home_blocks
from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.home import (
    InventarioRowBlock,
    MotorRowBlock,
    RendimientoRowBlock,
    UpdateCardBlock,
)


class HomePage(Dashboard):
    """El cockpit: tres filas del diseño 07, con Ctrl E y Ctrl U."""

    blocks = (MotorRowBlock, RendimientoRowBlock, InventarioRowBlock, UpdateCardBlock)

    def __init__(self) -> None:
        super().__init__()

        # El cockpit cabe en la ventana por defecto (1180×800) sin scroll:
        # márgenes y huecos compactos como el artboard.
        body = self.get_child()
        if isinstance(body, Gtk.Viewport):
            body = body.get_child()
        body.set_spacing(12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        # Fila 3 en dos columnas, la misma rejilla «1fr 276px» del artboard
        # (defecto 2 del pase visual 2): inventario + margen RAM a la
        # izquierda (~70 %) y la tarjeta de actualización compacta a la
        # derecha, en vez de apiladas a ancho completo.
        inventario = self._blocks[2]
        update = self._blocks[3]
        body.remove(inventario)
        body.remove(update)
        inventario.set_hexpand(True)
        update.set_size_request(276, -1)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(inventario)
        row.append(update)
        body.append(row)

        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        controller.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>e"),
            Gtk.CallbackAction.new(self._on_ctrl_e),
        ))
        controller.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>u"),
            Gtk.CallbackAction.new(self._on_ctrl_u),
        ))
        self.add_controller(controller)

    # -- delegación --------------------------------------------------------

    @property
    def motor_block(self) -> MotorRowBlock:
        return self._blocks[0]  # type: ignore[no-any-return]

    @property
    def update_block(self) -> UpdateCardBlock:
        return self._blocks[3]  # type: ignore[no-any-return]

    def request_switch(self) -> None:
        """Ctrl E: el cambio compacto con su confirmación (S4)."""
        self.motor_block.request_switch()

    def request_update(self) -> None:
        """Ctrl U: la actualización con su confirmación (S6)."""
        self.update_block.request_update()

    def _on_ctrl_e(self, *_args) -> bool:
        self.request_switch()
        return True

    def _on_ctrl_u(self, *_args) -> bool:
        self.request_update()
        return True


# El poller compartido vive en blocks.home; exponerlo aquí para el shell.
POLLER = home_blocks.POLLER
