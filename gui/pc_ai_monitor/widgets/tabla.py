"""Table helpers for the list-like dashboards (diseño 07: cabina compacta).

Plain boxes instead of a ``Gtk.ColumnView``: with a few dozen rows refreshed once
a second, replacing a list model every tick costs more than it gives, and these
rows stay readable. Sorting lives in the block, not in a model.

Design 07 additions, all painted with the app's ``@theme_*`` colours so a theme
switch re-themes them for free:

* the active sort header is accent-coloured (``th-sort-on``);
* the ACCESO column is a pill (``access-pill``), amber for exposed rows;
* exposed rows get the amber tint + left bar (``row-exposed``);
* Procesos carries an inline RAM bar (``ram-bar``).

The few classes this module owns ship with their own CSS provider, registered
once per display: the theme owns the colours, this module owns the geometry.
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Callable, Iterator

from gi.repository import Gtk

_CSS = """
/* Fila expuesta (Puertos): tinte ámbar, como el artboard. La barra izquierda
   es un elemento propio (no un box-shadow): con el radio de la fila, la sombra
   se curvaba y tapaba la primera letra. */
.row-exposed {
  background-color: alpha(@theme_warning, 0.10);
}
.row-bar {
  min-width: 3px;
  border-radius: 2px;
}
.row-bar-ex {
  background-color: @theme_warning;
}

/* Insignia de acceso: píldora compacta; expuesta ámbar, el resto azul. */
.access-pill {
  background-color: @theme_subtle;
  border: 1px solid @theme_border;
  border-radius: 999px;
  padding: 1px 9px;
  font-size: 11px;
  font-weight: 600;
}
.access-pill.access-lo { color: @theme_blue; }
.access-pill.access-ex {
  color: @theme_warning;
  border-color: alpha(@theme_warning, 0.45);
  background-color: alpha(@theme_warning, 0.12);
}

/* Columna activa del encabezado de orden. */
.th-sort-on { color: @theme_accent; }

/* Barra de RAM por fila (Procesos): azul serie, no compite con el acento. */
progressbar.ram-bar { min-height: 8px; }
progressbar.ram-bar > trough { border-radius: 4px; }
progressbar.ram-bar > trough > progress {
  background-color: @theme_blue;
  border-radius: 4px;
}
"""

_provider: Gtk.CssProvider | None = None


def _ensure_css() -> None:
    """Register the table CSS once per display (no-op without one)."""
    global _provider
    if _provider is not None:
        return
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS, -1)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _provider = provider


def cell(
    text: str,
    width: int,
    align=Gtk.Align.START,
    mono: bool = False,
    muted: bool = False,
    tooltip: str = "",
) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0 if align == Gtk.Align.START else 1)
    label.set_size_request(width, -1)
    label.set_ellipsize(3)
    # L21.6: el ancho de celda es un TOPE, no un mínimo que crece — sin el
    # máximo en caracteres, un texto largo (un `comm` raro en Procesos)
    # empujaba las columnas de la derecha. La ellipsis recorta y el texto
    # completo va en el tooltip.
    # Con ellipsize, GTK mide el ancho natural como max_width_chars × el
    # ancho medio de un carácter, que puede pasar el de la columna: el tope
    # en caracteres se queda bajo y el ancho real lo fija size_request.
    label.set_width_chars(1)
    label.set_max_width_chars(1)
    label.set_hexpand(False)
    if tooltip:
        label.set_tooltip_text(tooltip)
    if mono:
        label.add_css_class("mono")
    if muted:
        label.add_css_class("muted")
    return label


def pill(text: str, kind: str = "", tooltip: str = "") -> Gtk.Label:
    """Rounded badge, e.g. the ACCESO column: «🔒 Local», «🌐 Todas»."""
    _ensure_css()
    label = Gtk.Label(label=text)
    label.add_css_class("access-pill")
    if kind:
        label.add_css_class(f"access-{kind}")
    if tooltip:
        label.set_tooltip_text(tooltip)
    return label


def bar_cell(fraction: float, text: str, width: int = 130) -> Gtk.Box:
    """Inline bar + exact value: the RAM column of Procesos."""
    _ensure_css()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    bar = Gtk.ProgressBar()
    bar.add_css_class("ram-bar")
    bar.set_size_request(width, -1)
    bar.set_valign(Gtk.Align.CENTER)
    bar.set_fraction(max(0.0, min(1.0, fraction)))
    box.append(bar)

    value = Gtk.Label(label=text, xalign=1)
    value.add_css_class("mono")
    value.set_size_request(64, -1)
    box.append(value)
    return box


def row(cells: list[Gtk.Widget], highlight: bool = False) -> Gtk.Box:
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    line.add_css_class("legend-row")
    # Reserva de la barra izquierda: las columnas no se mueven cuando una
    # fila expuesta enciende la suya.
    _ensure_css()
    bar = Gtk.Box()
    bar.add_css_class("row-bar")
    if highlight:
        line.add_css_class("row-exposed")
        bar.add_css_class("row-bar-ex")
    line.prepend(bar)
    for widget in cells:
        line.append(widget)
    return line


def header(
    cells: list[tuple[str, int, str]],
    active: str,
    ascending: bool,
    on_sort: Callable[[str], None],
) -> tuple[Gtk.Box, dict[str, Gtk.Button]]:
    """Header row. A cell with a non-empty key becomes a sort button.

    Returns the row plus the buttons by key, so the block can rebuild the
    markers (▲/▼) and the accent on the active column after each sort.
    """
    _ensure_css()
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    line.set_margin_start(11)  # misma sangría que las filas (barra 3px + hueco 8px)
    buttons: dict[str, Gtk.Button] = {}

    for title, width, key in cells:
        if not key:
            label = Gtk.Label(label=title, xalign=0)
            label.set_size_request(width, -1)
            label.add_css_class("kpi-title")
            line.append(label)
            continue

        marker = ""
        if key == active:
            marker = " ▲" if ascending else " ▼"

        button = Gtk.Button(label=f"{title}{marker}")
        button.set_size_request(width, -1)
        button.add_css_class("flat")
        button.add_css_class("kpi-title")
        label = button.get_child()
        if isinstance(label, Gtk.Label):
            label.set_xalign(0)  # encabezado alineado a su columna, como el artboard
        if key == active:
            button.add_css_class("th-sort-on")
        button.connect("clicked", lambda *_args, selected=key: on_sort(selected))
        line.append(button)
        buttons[key] = button

    return line, buttons


# ---------------------------------------------------------------------------
# Introspection helpers: tests (and nothing else) read the painted rows.
# ---------------------------------------------------------------------------


def _children(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _walk(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    for child in _children(widget):
        yield child
        yield from _walk(child)


def row_labels(container: Gtk.Widget) -> list[list[str]]:
    """Texts of every row under a table container (non-row children skipped)."""
    rows: list[list[str]] = []
    for child in _children(container):
        if not isinstance(child, Gtk.Box):
            continue
        rows.append(
            [label.get_text() for label in _walk(child) if isinstance(label, Gtk.Label)]
        )
    return rows


def row_flags(container: Gtk.Widget, css_class: str) -> list[bool]:
    """Per row under ``container``: does it carry ``css_class``?"""
    return [
        css_class in list(child.get_css_classes())
        for child in _children(container)
        if isinstance(child, Gtk.Box)
    ]


def find_css(container: Gtk.Widget, css_class: str, kind: type) -> list:
    """Every widget of ``kind`` with ``css_class`` under ``container``."""
    return [
        widget
        for widget in _walk(container)
        if isinstance(widget, kind) and css_class in list(widget.get_css_classes())
    ]


def find_widgets(container: Gtk.Widget, kind: type) -> list:
    return [widget for widget in _walk(container) if isinstance(widget, kind)]


def widget_texts(widget: Gtk.Widget) -> list[str]:
    """Every label text under a widget, in paint order."""
    texts = []
    if isinstance(widget, Gtk.Label):
        texts.append(widget.get_text())
    texts.extend(
        label.get_text() for label in _walk(widget) if isinstance(label, Gtk.Label)
    )
    return texts
