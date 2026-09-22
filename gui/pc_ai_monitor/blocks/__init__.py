"""Dashboard blocks: one self-contained card per concern.

A block is a widget that knows how to refresh itself from a snapshot, so a
dashboard is just an ordered list of blocks and adding a panel never means
touching the shell.
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.datos import Snapshot


class Block(Gtk.Box):
    """Base card: a heading plus a body that subclasses fill."""

    title = ""
    frame = True

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if self.frame:
            self.add_css_class("card")

        if self.title:
            heading = Gtk.Label(label=self.title, xalign=0)
            heading.add_css_class("section-head")
            self.append(heading)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self.body)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        """Refresh from the latest data. Subclasses override."""

    def redraw(self) -> None:
        """Repaint Cairo charts after a theme switch. Optional."""


class Dashboard(Gtk.ScrolledWindow):
    """A page: an ordered stack of blocks."""

    blocks: tuple[type[Block], ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(14)
        body.set_margin_bottom(14)
        body.set_margin_start(14)
        body.set_margin_end(14)

        self._blocks = [block_type() for block_type in self.blocks]
        for block in self._blocks:
            body.append(block)

        self.set_child(body)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        for block in self._blocks:
            block.update_snapshot(snapshot)

    def redraw(self) -> None:
        for block in self._blocks:
            block.redraw()


def catalogue() -> dict[str, type[Block]]:
    """Block name -> class. Imported here to keep the modules cycle-free."""
    from pc_ai_monitor.blocks.grupos import GroupsBlock
    from pc_ai_monitor.blocks.historial import HistoryBlock
    from pc_ai_monitor.blocks.kpis import KpiBlock
    from pc_ai_monitor.blocks.memoria import MemoryBlock
    from pc_ai_monitor.blocks.modelos import ModelsBlock
    from pc_ai_monitor.blocks.motores import (
        MemoriaBlock,
        MotoresBlock,
    )

    return {
        "kpis": KpiBlock,
        "memoria": MemoryBlock,
        "grupos": GroupsBlock,
        "modelos": ModelsBlock,
        "historial": HistoryBlock,
        "motores": MotoresBlock,
        "motores_memoria": MemoriaBlock,
    }


def build(name: str) -> Block:
    """Instantiate one block by name, so a dashboard can be data-driven."""
    block_type = catalogue().get(name)
    if block_type is None:
        raise KeyError(f"bloque desconocido: {name}")
    return block_type()
