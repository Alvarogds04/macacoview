"""Table helpers for the list-like dashboards.

Plain boxes instead of a ``Gtk.ColumnView``: with a few dozen rows refreshed once
a second, replacing a list model every tick costs more than it gives, and these
rows stay readable. Sorting lives in the block, not in a model.
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Callable

from gi.repository import Gtk


def cell(
    text: str,
    width: int,
    align=Gtk.Align.START,
    mono: bool = False,
    muted: bool = False,
) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0 if align == Gtk.Align.START else 1)
    label.set_size_request(width, -1)
    label.set_ellipsize(3)
    if mono:
        label.add_css_class("mono")
    if muted:
        label.add_css_class("muted")
    return label


def row(cells: list[Gtk.Label], highlight: bool = False) -> Gtk.Box:
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    line.add_css_class("legend-row")
    if highlight:
        line.add_css_class("row-warn")
    for widget in cells:
        line.append(widget)
    return line


def header(
    cells: list[tuple[str, int, str]],
    active: str,
    ascending: bool,
    on_sort: Callable[[str], None],
) -> Gtk.Box:
    """Header row. A cell with a non-empty key becomes a sort button."""
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

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
        button.connect("clicked", lambda *_args, selected=key: on_sort(selected))
        line.append(button)

    return line
