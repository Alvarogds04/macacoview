"""Motores: inventario dinámico y control de modelos de IA locales.

El inventario y **todas** las acciones vienen de ``local-ai-models``, el único
autorizado a arrancar y parar modelos. El panel no conoce nombres, puertos ni
unidades: muestra lo que el gestor informa, así un modelo nuevo aparece solo.
"""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.motores import MemoriaBlock, MotoresBlock


class MotoresPage(Dashboard):
    blocks = (
        MemoriaBlock,
        MotoresBlock,
    )
