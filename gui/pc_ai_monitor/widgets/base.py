"""Chart primitives shared by every chart in ``graficos``.

Kept apart from the charts themselves so a new chart imports the contract
(``Slice``, the drawing base class and the text helper) without dragging the
whole chart module in.
"""

# pyright: reportAttributeAccessIssue=false

from dataclasses import dataclass

from gi.repository import Gtk, PangoCairo

from pc_ai_monitor import theme


@dataclass(frozen=True)
class Slice:
    """One drawn portion: a donut arc, a column, a treemap cell."""

    label: str
    value: float
    color: str


def draw_text(
    area: Gtk.DrawingArea,
    cr,
    text: str,
    x: float,
    y: float,
    color: str,
    size: float = 11.0,
    bold: bool = False,
    centered: bool = False,
) -> None:
    """Draw one line of text with Pango, optionally centred on (x, y).

    The draw function receives a plain pycairo context, so text goes through
    ``PangoCairo`` instead of the GTK-only ``cr.show_layout`` helper.
    """
    layout = area.create_pango_layout(text)
    weight = "bold" if bold else "normal"
    layout.set_markup(
        f'<span size="{round(size * 1024)}" weight="{weight}">{text}</span>'
    )
    width, height = layout.get_pixel_size()
    cr.move_to(x - width / 2 if centered else x, y - height / 2 if centered else y)
    cr.set_source_rgba(*theme.rgba(color))
    PangoCairo.show_layout(cr, layout)


class Chart(Gtk.DrawingArea):
    """Base class: fixed content size and a draw callback."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

    def _draw(self, area, cr, width: int, height: int) -> None:  # pragma: no cover
        raise NotImplementedError
