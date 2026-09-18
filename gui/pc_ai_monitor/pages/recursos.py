"""Recursos: the machine dashboard, assembled from blocks.

Order matters: the models sit right under the group chart, and clicking one
opens its detail in place.
"""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.grupos import GroupsBlock
from pc_ai_monitor.blocks.historial import HistoryBlock
from pc_ai_monitor.blocks.kpis import KpiBlock
from pc_ai_monitor.blocks.memoria import MemoryBlock
from pc_ai_monitor.blocks.modelos import ModelsBlock


class RecursosPage(Dashboard):
    blocks = (KpiBlock, MemoryBlock, GroupsBlock, ModelsBlock, HistoryBlock)
