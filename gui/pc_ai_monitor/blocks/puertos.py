"""Puertos: what is listening and how exposed it is (diseño 07).

The parsing lives in ``pc_ai_monitor.puertos``; this block only presents it, and
sorts in the block instead of in a model. Design 07 keeps every function (KPIs,
the exposed filter, the sortable PUERTO/PID columns, the 80-row cap, the notes)
and changes the paint: the ACCESO column is a pill with its sentence in the
tooltip, exposed rows wear the amber tint, and the header marks the active sort
in accent colour.
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.tablas_vista import (
    access_pill_kind,
    access_tip,
    port_sort_key,
    port_tooltip,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.puertos import PortRow, counts, parse_ss
from pc_ai_monitor.widgets import chips
from pc_ai_monitor.widgets.barras import KpiTile
from pc_ai_monitor.widgets.tabla import cell, header, pill, row

# (título, ancho, clave de orden). La clave vacía no es clickeable.
COLUMNS = (
    ("TIPO", 60, ""),
    ("PUERTO", 80, "port"),
    ("ACCESO", 120, ""),
    ("SERVICIO", 170, ""),
    ("PROCESO", 150, ""),
    ("PID", 80, "pid"),
)

MAX_ROWS = 80


class PortsKpiBlock(Block):
    title = ""
    frame = False

    def __init__(self) -> None:
        super().__init__()
        self._tiles = {
            "tcp": KpiTile("—", "TCP"),
            "udp": KpiTile("—", "UDP"),
            "exposed": KpiTile("—", "EN TODAS LAS INTERFACES"),
        }
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.set_homogeneous(True)  # la franja ocupa el ancho, como el artboard
        for key, tile in self._tiles.items():
            tile.set_hexpand(True)
            tile.set_tooltip_text(
                {
                    "tcp": "Puertos TCP escuchando",
                    "udp": "Puertos UDP escuchando",
                    "exposed": "Escuchando en 0.0.0.0 o ::: expuestos a toda la red",
                }[key]
            )
            strip.append(tile)
        self.body.append(strip)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        tcp, udp, exposed = counts(parse_ss(snapshot.ports or ""))
        self._tiles["tcp"].set_value(str(tcp))
        self._tiles["udp"].set_value(str(udp))
        self._tiles["exposed"].set_value(str(exposed))


class PortsBlock(Block):
    title = "PUERTOS ESCUCHANDO"

    def __init__(self) -> None:
        super().__init__()
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._sort = "port"
        self._ascending = True
        self._only_exposed = False
        self._all: list[PortRow] = []

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._controls = controls
        toggle = Gtk.CheckButton(label="sólo expuestos a todas las interfaces")
        toggle.add_css_class("muted")
        toggle.connect("toggled", self._on_toggle)
        controls.append(toggle)

        # Leyenda compacta de insignias: la frase de cada una va en su tooltip.
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for access in LOCAL_FIRST:
            legend.append(pill(access, access_pill_kind(access), access_tip(access)))
        controls.append(legend)
        self.body.append(controls)

        self._header_box, self._header_buttons = self._build_header()
        self.body.append(self._header_box)
        self.body.append(self._rows)

        self._note = chips.note("")
        self.body.append(self._note)

    def _build_header(self) -> tuple[Gtk.Box, dict[str, Gtk.Button]]:
        return header(list(COLUMNS), self._sort, self._ascending, self._on_sort)

    def _on_toggle(self, button: Gtk.CheckButton) -> None:
        self._only_exposed = button.get_active()
        self._render()

    def _on_sort(self, key: str) -> None:
        if key == self._sort:
            self._ascending = not self._ascending
        else:
            self._sort = key
            self._ascending = True
        self._rebuild_header()
        self._render()

    def _rebuild_header(self) -> None:
        self.body.remove(self._header_box)
        self._header_box, self._header_buttons = self._build_header()
        self.body.insert_child_after(self._header_box, self._controls)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._all = parse_ss(snapshot.ports or "")
        self._render()

    def _render(self) -> None:
        while (child := self._rows.get_first_child()) is not None:
            self._rows.remove(child)

        visible = (
            [entry for entry in self._all if entry.exposed]
            if self._only_exposed
            else list(self._all)
        )
        visible.sort(key=port_sort_key(self._sort), reverse=not self._ascending)

        for entry in visible[:MAX_ROWS]:
            line = row(
                [
                    cell(entry.proto.upper(), COLUMNS[0][1], mono=True, muted=True),
                    cell(
                        str(entry.port),
                        COLUMNS[1][1],
                        align=Gtk.Align.END,
                        mono=True,
                    ),
                    pill(
                        entry.access,
                        access_pill_kind(entry.access),
                        access_tip(entry.access),
                    ),
                    cell(entry.service, COLUMNS[3][1]),
                    cell(entry.process, COLUMNS[4][1], muted=True),
                    cell(
                        str(entry.pid),
                        COLUMNS[5][1],
                        align=Gtk.Align.END,
                        mono=True,
                        muted=True,
                    ),
                ],
                highlight=entry.exposed,
            )
            # El detalle de la fila va en el tooltip (S12), no en texto extra.
            line.set_tooltip_text(port_tooltip(entry))
            self._rows.append(line)

        if not visible:
            self._rows.append(
                chips.note("Sin datos de puertos: falta el helper privilegiado.")
            )
        self._note.set_text(
            f"{len(visible[:MAX_ROWS])} de {len(self._all)} puertos · "
            f"local {entry_local(visible)}"
            if visible
            else "Sin el helper, la pestaña queda vacía en vez de pedir credenciales."
        )


def entry_local(entries: list[PortRow]) -> str:
    return str(entries[0].local) if entries else "—"


LOCAL_FIRST = ("🔒 Local", "🏠 LAN", "🔷 Tailscale", "🌐 Todas", "📡 Interfaz")
