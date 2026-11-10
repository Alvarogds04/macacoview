# pyright: reportAttributeAccessIssue=false
"""Recursos: the machine dashboard, assembled from blocks.

Design 07 lays this page out in two columns: memory meters and the 5-minute
history on the left, the RSS groups and the local models on the right, with
the KPI strip across the top. The blocks keep their order in ``blocks`` so
anything iterating ``_blocks`` (tests, redraw) sees the same sequence.

Opening the Pi group (S17, design 08 tab 4) adds a row under the KPI strip:
the per-session card wide on the left and the groups card narrowed to
``PI_GROUPS_WIDTH`` on its right. The page owns that single sessions view and
moves the groups card between the right column and the row; closing Pi (or
opening another group) puts the two columns back exactly as they were.
"""

from gi.repository import Gtk

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.grupos import GroupsBlock
from pc_ai_monitor.blocks.historial import HistoryBlock
from pc_ai_monitor.blocks.kpis import KpiBlock
from pc_ai_monitor.blocks.memoria import MemoryBlock
from pc_ai_monitor.blocks.modelos import ModelsBlock
from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

PI_GROUPS_WIDTH = 250  # 08 tab 4: .rec-grid { grid-template-columns: 1fr 250px }


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

        # Fila de Pi: vacía y oculta mientras Pi está cerrado, para que la
        # lista de sesiones no exista dos veces ni quede colgada fuera de vista.
        # Gtk.Grid y no Gtk.Box: una Box horizontal pide de alto mínimo el de
        # la tabla a su ancho mínimo (~2.4k px con los nombres partidos) y deja
        # un hueco enorme debajo; la Grid mide el alto con el ancho real.
        self._pi_row = Gtk.Grid(column_spacing=12)
        self._pi_row.set_visible(False)
        self._pi_view = PiSessionsView(framed=True)
        self._pi_view.set_hexpand(True)
        self._pi_view.set_valign(Gtk.Align.START)
        self._groups = groups
        self._right = right

        body.append(kpi)
        body.append(self._pi_row)
        body.append(columns)

        groups.on_pi_sessions = self._on_pi_sessions

    def _on_pi_sessions(self, sessions, group_rss_gib: float) -> None:
        if sessions is None:
            self._close_pi()
            return
        self._pi_view.update(sessions, group_rss_gib)
        if self._pi_view.get_parent() is None:
            self._open_pi()

    def _open_pi(self) -> None:
        groups = self._groups
        self._right.remove(groups)
        groups.set_compact(True)
        groups.set_size_request(PI_GROUPS_WIDTH, -1)
        # Un hijo con hexpand (el nombre de la leyenda) contagia la ficha: la
        # columna de 250 px es fija y lo que sobra va a la lista de sesiones.
        groups.set_hexpand(False)
        groups.set_valign(Gtk.Align.START)
        self._pi_row.attach(self._pi_view, 0, 0, 1, 1)
        self._pi_row.attach(groups, 1, 0, 1, 1)
        self._pi_row.set_visible(True)

    def _close_pi(self) -> None:
        if self._pi_view.get_parent() is None:
            return
        groups = self._groups
        self._pi_row.remove(self._pi_view)
        self._pi_row.remove(groups)
        self._pi_row.set_visible(False)
        groups.set_compact(False)
        groups.set_size_request(-1, -1)
        groups.set_hexpand_set(False)
        groups.set_valign(Gtk.Align.FILL)
        self._right.prepend(groups)
