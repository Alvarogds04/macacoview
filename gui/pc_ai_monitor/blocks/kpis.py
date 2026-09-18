"""KPI strip: the headline numbers of the machine."""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, gib, percent, safe
from pc_ai_monitor.widgets.barras import KpiTile


class KpiBlock(Block):
    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        self._tiles = {
            "ram": KpiTile("—", "RAM EN USO"),
            "total": KpiTile("—", "RAM TOTAL"),
            "models": KpiTile("—", "MODELOS CARGADOS"),
            "procs": KpiTile("—", "PROCESOS VIGILADOS"),
        }
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for tile in self._tiles.values():
            strip.append(tile)
        self.body.append(strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        memory = snapshot.memory()
        used = safe(memory.get("used_gib"))
        total = safe(memory.get("total_gib"))
        self._tiles["ram"].set_value(percent(used, total))
        self._tiles["total"].set_value(f"{gib(total)}G")
        self._tiles["models"].set_value(count(len(snapshot.models())))
        self._tiles["procs"].set_value(count(len(snapshot.processes())))
