"""Pages of the monitor: motores, recursos, tokens, puertos and procesos."""

from pc_ai_monitor.pages.motores import MotoresPage
from pc_ai_monitor.pages.procesos import ProcesosPage
from pc_ai_monitor.pages.puertos import PuertosPage
from pc_ai_monitor.pages.recursos import RecursosPage
from pc_ai_monitor.pages.router import RouterPage
from pc_ai_monitor.pages.usage import TokensPage

__all__ = [
    "MotoresPage",
    "ProcesosPage",
    "PuertosPage",
    "RecursosPage",
    "RouterPage",
    "TokensPage",
]
