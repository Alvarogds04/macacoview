"""Procesos: sortable list of the heaviest processes."""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.procesos import ProcessesBlock


class ProcesosPage(Dashboard):
    blocks = (ProcessesBlock,)
