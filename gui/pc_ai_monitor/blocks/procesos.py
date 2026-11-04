"""Procesos: the heaviest processes on the machine (diseño 07).

Same rules as always — PID ascending when a new key arrives, RAM/CPU descending,
the same key toggles, top 40, the «Mostrando N de M procesos · ordenado por …»
note — with the design's paint: an inline RAM bar per row scaled to the largest
row shown, the CPU as a compact number, and the full command line in the row's
tooltip instead of a fourth wide column of text.
"""

# pyright: reportAttributeAccessIssue=false

from typing import Any

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.tablas_vista import ram_fraction, ram_text
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, gib, safe
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.tabla import bar_cell, cell, header, row

# (título, ancho, clave de orden). La clave vacía no es clickeable.
COLUMNS = (
    ("PID", 80, "pid"),
    ("PROCESO", 170, ""),
    ("RAM", 200, "ram"),
    ("CPU", 90, "cpu"),
    ("COMANDO", 340, ""),
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

        self._header_box, self._header_buttons = self._build_header()
        self.body.append(self._header_box)
        self.body.append(self._rows)

        self._note = chips.note("")
        self.body.append(self._note)

    def _build_header(self) -> tuple[Gtk.Box, dict[str, Gtk.Button]]:
        return header(list(COLUMNS), self._sort, self._ascending, self._on_sort)

    def _on_sort(self, key: str) -> None:
        if key == self._sort:
            self._ascending = not self._ascending
        else:
            self._sort = key
            self._ascending = key == "pid"
        self._rebuild_header()
        self._render()

    def _rebuild_header(self) -> None:
        # El ▲/▼ y el acento de la columna activa viven en el encabezado.
        self.body.remove(self._header_box)
        self._header_box, self._header_buttons = self._build_header()
        self.body.prepend(self._header_box)

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
        shown = ordered[:LIMIT]
        peak = max((safe(p.get("rss_kb")) for p in shown), default=0.0)
        for process in shown:
            rss_kb = process.get("rss_kb")
            line = row(
                [
                    cell(
                        str(process.get("pid", "—")),
                        COLUMNS[0][1],
                        align=Gtk.Align.END,
                        mono=True,
                        muted=True,
                    ),
                    cell(str(process.get("comm") or "—"), COLUMNS[1][1]),
                    bar_cell(ram_fraction(rss_kb, peak), ram_text(rss_kb)),
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
            # La fila completa se cuenta en el tooltip (S12).
            line.set_tooltip_text(
                f"{process.get('args') or process.get('comm') or '—'}"
                f" · PID {process.get('pid', '—')}"
                f" · {ram_text(rss_kb)}iB · CPU {gib(process.get('cpu'))} %"
            )
            self._rows.append(line)

        self._note.set_text(
            f"Mostrando {min(LIMIT, len(ordered))} de {count(len(self._all))} procesos · "
            f"ordenado por {self._sort}"
        )
