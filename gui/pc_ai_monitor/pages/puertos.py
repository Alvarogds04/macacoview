"""Puertos: listening sockets and their exposure."""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.puertos import PortsBlock, PortsKpiBlock


class PuertosPage(Dashboard):
    blocks = (PortsKpiBlock, PortsBlock)
