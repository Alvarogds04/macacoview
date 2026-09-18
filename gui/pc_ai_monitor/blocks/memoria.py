"""Memory: RAM and swap against their real totals."""

# pyright: reportAttributeAccessIssue=false

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import gib, safe, share
from pc_ai_monitor.widgets.barras import MeterRow


class MemoryBlock(Block):
    title = "MEMORIA DEL SISTEMA"

    def __init__(self) -> None:
        super().__init__()
        self._ram = MeterRow("RAM", kind="accent")
        self._swap = MeterRow("SWAP", kind="blue")
        self._free = MeterRow("LIBRE", kind="green")
        for row in (self._ram, self._swap, self._free):
            self.body.append(row)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        memory = snapshot.memory()
        total = safe(memory.get("total_gib"))
        used = safe(memory.get("used_gib"))
        available = safe(memory.get("available_gib"))
        swap_total = safe(memory.get("swap_total_gib"))
        swap_used = safe(memory.get("swap_used_gib"))

        self._ram.set(share(used, total), f"{gib(used)} / {gib(total)} GiB")
        self._swap.set(
            share(swap_used, swap_total), f"{gib(swap_used)} / {gib(swap_total)} GiB"
        )
        self._free.set(share(available, total), f"{gib(available)} GiB")
