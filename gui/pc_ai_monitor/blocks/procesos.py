"""Procesos: the heaviest processes on the machine."""

# pyright: reportAttributeAccessIssue=false

from typing import Any

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, gib, safe
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.tabla import cell, header, row

# (título, ancho, clave de orden). La clave vacía no es clickeable.
COLUMNS = (
    ("PID", 80, "pid"),
    ("PROCESO", 190, ""),
    ("RAM", 110, "ram"),
    ("CPU", 90, "cpu"),
    ("COMANDO", 420, ""),
)
SORTS = {
    "pid": lambda item: safe(item.get("pid")),
    "ram": lambda item: safe(item.get("rss_kb")),
    "cpu": lambda item: safe(item.get("cpu")),
}
LIMIT = 40


class ProcessesBlock(Block):
    title = "PROCESOS POR CONSUMO"

    def __init__(self) -> None:
        super().__init__()
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._sort = "ram"
        self._ascending = False
        self._all: list[dict[str, Any]] = []

        self._header = header(list(COLUMNS), self._sort, self._ascending, self._on_sort)
        self.body.append(self._header)
        self.body.append(self._rows)

        self._note = chips.note("")
        self.body.append(self._note)

    def _on_sort(self, key: str) -> None:
        if key == self._sort:
            self._ascending = not self._ascending
        else:
            self._sort = key
            self._ascending = key == "pid"
        self._render()

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._all = snapshot.processes()
        self._render()

    def _render(self) -> None:
        while (child := self._rows.get_first_child()) is not None:
            self._rows.remove(child)

        ordered = sorted(
            self._all,
            key=SORTS.get(self._sort, SORTS["ram"]),
            reverse=not self._ascending,
        )
        for process in ordered[:LIMIT]:
            self._rows.append(
                row(
                    [
                        cell(
                            str(process.get("pid", "—")),
                            COLUMNS[0][1],
                            align=Gtk.Align.END,
                            mono=True,
                            muted=True,
                        ),
                        cell(str(process.get("comm") or "—"), COLUMNS[1][1]),
                        cell(
                            f"{safe(process.get('rss_kb')) / 1024 / 1024:.2f}G",
                            COLUMNS[2][1],
                            align=Gtk.Align.END,
                            mono=True,
                        ),
                        cell(
                            f"{gib(process.get('cpu'))}%",
                            COLUMNS[3][1],
                            align=Gtk.Align.END,
                            mono=True,
                            muted=True,
                        ),
                        cell(str(process.get("args") or ""), COLUMNS[4][1], muted=True),
                    ]
                )
            )

        self._note.set_text(
            f"Mostrando {min(LIMIT, len(ordered))} de {count(len(self._all))} procesos · "
            f"ordenado por {self._sort}"
        )
