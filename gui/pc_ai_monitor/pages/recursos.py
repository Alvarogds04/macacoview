# pyright: reportAttributeAccessIssue=false
"""Recursos: the machine dashboard, assembled from blocks.

Design 07 lays this page out in two columns: memory meters and the 5-minute
history on the left, the RSS groups and the local models on the right, with
the KPI strip across the top. The blocks keep their order in ``blocks`` so
anything iterating ``_blocks`` (tests, redraw) sees the same sequence.
"""

from gi.repository import Gtk

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.grupos import GroupsBlock
from pc_ai_monitor.blocks.historial import HistoryBlock
from pc_ai_monitor.blocks.kpis import KpiBlock
from pc_ai_monitor.blocks.memoria import MemoryBlock
from pc_ai_monitor.blocks.modelos import ModelsBlock


class RecursosPage(Dashboard):
    blocks = (KpiBlock, MemoryBlock, GroupsBlock, ModelsBlock, HistoryBlock)

    def __init__(self) -> None:
        super().__init__()
        kpi, memory, groups, models, history = self._blocks

        body = self.get_child().get_child()
        while (child := body.get_first_child()) is not None:
            body.remove(child)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.append(memory)
        left.append(history)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.append(groups)
        right.append(models)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        columns.set_homogeneous(True)
        columns.append(left)
        columns.append(right)

        body.append(kpi)
        body.append(columns)
