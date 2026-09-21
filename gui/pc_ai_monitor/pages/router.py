"""Router: la seccion que muestra la arquitectura PC-AI en vivo.

Orden de los bloques segun el panel pedido: SYSTEM, ABITO/FLASH, ROUTER, JEV,
CLOUD, ENGRAM, SESSIONS.
"""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.router import (
    CloudBlock,
    ConfirmationsBlock,
    EnginesBlock,
    EngramBlock,
    FlashLifecycleBlock,
    JevBlock,
    RouterBlock,
    SessionsBlock,
    SystemBlock,
)


class RouterPage(Dashboard):
    blocks = (
        ConfirmationsBlock,
        SystemBlock,
        EnginesBlock,
        RouterBlock,
        FlashLifecycleBlock,
        JevBlock,
        CloudBlock,
        EngramBlock,
        SessionsBlock,
    )
