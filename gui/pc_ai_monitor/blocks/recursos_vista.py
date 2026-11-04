"""Vista de Recursos (diseño 07, T6): reglas puras y el treemap de grupos.

Todo lo que decide valores vive acá sin GTK dibujable; el bloque
(``blocks.grupos``) solo arma widgets y delega.
"""

# pyright: reportAttributeAccessIssue=false, reportIncompatibleMethodOverride=false

from collections.abc import Callable

from pc_ai_monitor import theme
from pc_ai_monitor.formato import safe, truncate
from pc_ai_monitor.widgets.base import Slice, draw_text
from pc_ai_monitor.widgets.graficos import Treemap, _layout


def visible_total(values: dict[str, float], hidden: set[str]) -> float:
    """Suma de RSS de los grupos visibles: los ocultos no cuentan."""
    return sum(safe(value) for key, value in values.items() if key not in hidden)


def treemap_slices(
    values: dict[str, float],
    hidden: set[str],
    colour_for: Callable[[str], str],
    label_for: Callable[[str], str],
) -> list[Slice]:
    """Fichas del treemap: grupos visibles con RSS > 0, mayor primero.

    El color lo resuelve el llamador (queda anclado a la posición del grupo
    en GROUP_KEYS, para que no se corra al ocultar entradas); la etiqueta
    también, porque la leyenda y las fichas comparten el rótulo del grupo.
    """
    return [
        Slice(label=label_for(key), value=safe(value), color=colour_for(key))
        for key, value in sorted(
            (
                (key, safe(value))
                for key, value in values.items()
                if key not in hidden and safe(value) > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def top_processes(processes: list[dict], pids: set[int], limit: int = 10) -> list[dict]:
    """Los procesos de un grupo, por RSS descendente, top 10."""
    found = [p for p in processes if int(safe(p.get("pid"))) in pids]
    return sorted(found, key=lambda p: safe(p.get("rss_kb")), reverse=True)[:limit]


class GroupTreemap(Treemap):
    """Treemap de RSS por grupo con el total legible en la cabecera.

    Reemplaza al donut de grupos conservando su interfaz — ``set_data`` con
    (slices, total, etiqueta) y los atributos ``_slices`` / ``_center_value``
    / ``_center_label`` — porque test_core la fija: el total del treemap es
    el mismo número que antes se dibujaba al centro del donut.
    """

    HEADER = 24

    def __init__(
        self, height: int = 190, on_select: Callable[[Slice], None] | None = None
    ) -> None:
        super().__init__(height=height, on_select=on_select)
        self._center_value = ""
        self._center_label = ""

    def set_data(self, slices, center_value: str, center_label: str) -> None:  # type: ignore[override]
        self._center_value = center_value
        self._center_label = center_label
        super().set_data(slices)

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        self._rects = []

        # Cabecera: «RSS total NN.NNG» — el total del donut sigue visible.
        if self._center_value:
            draw_text(
                area,
                cr,
                f"{self._center_label} {self._center_value}",
                2,
                # draw_text sin centrar ubica el borde superior en y: con
                # HEADER/2 el texto bajaba sobre las fichas.
                3,
                palette.text,
                10,
                True,
            )

        top = self.HEADER if self._center_value else 0
        rect = (0.0, float(top), float(width), float(height - top))
        for item, (x, y, w, h) in _layout(self._slices, rect, True):
            cr.set_source_rgba(*theme.rgba(item.color))
            cr.rectangle(x + 1, y + 1, max(1.0, w - 2), max(1.0, h - 2))
            cr.fill()

            # Texto oscuro sobre la ficha: las series son claras en los cuatro
            # temas (todos oscuros), como las fichas del diseño.
            if w > 54 and h > 26:
                draw_text(
                    area,
                    cr,
                    truncate(item.label, max(4, round(w / 9))),
                    x + 6,
                    y + 12,
                    "#10131c",
                    9,
                    True,
                )
            if w > 54 and h > 44:
                draw_text(
                    area,
                    cr,
                    f"{safe(item.value):.1f}G",
                    x + 6,
                    y + 27,
                    "#10131c",
                    8,
                )
            self._rects.append((item, x, y, w, h))
