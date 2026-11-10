# pyright: reportAttributeAccessIssue=false
"""Recursos → Pi sesiones: the per-session card of design 08 tab 4 (S17).

``PiSessionsView`` is self-contained and width-independent: its own header
(title + «RSS grupo» / «PSS sesiones» / «no sumar»), a five-column table
(Sesión, PIDs, PSS ↓ with a bar, RSS, Hijos) with subagents indented under
their session, and the footnote. It knows nothing about the page around it:
feed it with ``update(sessions, group_rss_gib)`` from any snapshot.
"""

from gi.repository import Gtk, Pango

from pc_ai_monitor.blocks import pi_sesiones_logica as logica

_CSS = """
.pi-ses-title { font-weight: 700; font-size: 14px; }
.pi-ses-total { font-family: "JetBrains Mono", "Fira Code", monospace; font-weight: 700; font-size: 15px; }
.pi-ses-head { font-size: 10.5px; font-weight: 700; letter-spacing: 0.08em; color: @theme_muted; }
.pi-ses-name { font-weight: 600; font-size: 13px; }
.pi-ses-tag {
  font-size: 10.5px; font-weight: 600; color: @theme_muted;
  border: 1px solid @theme_border; border-radius: 999px; padding: 0 7px;
}
.pi-ses-tag-named { color: @theme_accent; border-color: @theme_accent; }
.pi-ses-tag-warn { color: @theme_warning; border-color: @theme_warning; }
progressbar.pi-ses-bar { min-height: 8px; }
progressbar.pi-ses-bar > trough { min-height: 8px; border-radius: 4px; }
progressbar.pi-ses-bar > trough > progress {
  min-height: 8px; border-radius: 4px; background-color: @theme_blue;
}
progressbar.pi-ses-bar.pi-ses-sub > trough > progress { background-color: alpha(@theme_blue, 0.7); }
"""

_provider = None
_SUB_INDENT = 22
_BAR_MIN = 48


def _ensure_css() -> None:
    global _provider
    if _provider is not None:
        return
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


def _label(text: str, *classes: str, xalign: float = 0.0, tooltip: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign)
    label.set_use_markup(False)
    for css in classes:
        label.add_css_class(css)
    if tooltip:
        label.set_tooltip_text(tooltip)
    return label


class PiSessionsView(Gtk.Box):
    """One row per Pi session, PSS first. ``framed`` draws its own card."""

    def __init__(self, framed: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        _ensure_css()
        if framed:
            self.add_css_class("card")

        # Title and totals side by side when there is room, stacked when the
        # card is narrow: a FlowBox wraps its children without a breakpoint.
        head = Gtk.FlowBox()
        head.set_selection_mode(Gtk.SelectionMode.NONE)
        head.set_max_children_per_line(2)
        head.set_min_children_per_line(1)
        head.set_column_spacing(12)
        head.set_row_spacing(4)
        head.set_homogeneous(False)
        self._title = _label("Pi", "pi-ses-title")
        self._title.set_valign(Gtk.Align.CENTER)
        head.append(self._title)

        totals = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        totals.set_halign(Gtk.Align.END)
        totals.append(_label("RSS grupo", "muted", tooltip=logica.TIP_GROUP_RSS))
        self._group_rss = _label("—", "pi-ses-total", tooltip=logica.TIP_GROUP_RSS)
        totals.append(self._group_rss)
        spacer = Gtk.Box()
        spacer.set_size_request(8, -1)
        totals.append(spacer)
        totals.append(_label("PSS sesiones", "muted", tooltip=logica.TIP_SESSIONS_PSS))
        self._sessions_pss = _label("—", "pi-ses-total", tooltip=logica.TIP_SESSIONS_PSS)
        totals.append(self._sessions_pss)
        no_sumar = _label("no sumar", "chip", "chip-warn", tooltip=logica.TIP_NO_SUMAR)
        no_sumar.set_margin_start(6)
        no_sumar.set_valign(Gtk.Align.CENTER)
        totals.append(no_sumar)
        head.append(totals)
        for child in (head.get_child_at_index(0), head.get_child_at_index(1)):
            child.set_focusable(False)
        head.get_child_at_index(1).set_halign(Gtk.Align.END)
        head.get_child_at_index(1).set_hexpand(True)
        self.append(head)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        self._grid.set_hexpand(True)
        self.append(self._grid)

        self._empty = _label(logica.EMPTY, "muted")
        self._empty.set_visible(False)
        self.append(self._empty)

        footnote = _label(logica.FOOTNOTE, "muted")
        footnote.set_wrap(True)
        footnote.set_margin_top(4)
        self.append(footnote)

        self._children_heading = _label("HIJOS", "pi-ses-head", xalign=1, tooltip=logica.TIP_CHILDREN)
        self._rows: list[logica.Row] = []
        self._key: object = None

    # -- public API -------------------------------------------------------

    def update(self, sessions, group_rss_gib: float, home: str | None = None) -> None:
        """Refresh from the collector's ``pi_sessions`` and the Pi group RSS."""
        rows = logica.session_rows(sessions, home=home)
        head = logica.header(sessions, group_rss_gib)
        key = (tuple(rows), head)
        if key == self._key:
            return
        self._key = key
        self._rows = rows

        self._title.set_text(head.title)
        self._group_rss.set_text(head.rss_text)
        self._sessions_pss.set_text(head.pss_text)
        self._sessions_pss.set_tooltip_text(
            logica.TIP_SESSIONS_PSS
            + ("\nAlguna PSS no se pudo leer: la suma es un mínimo." if head.partial else "")
        )
        self._render(rows)

    # -- table ------------------------------------------------------------

    def _render(self, rows: list[logica.Row]) -> None:
        while (child := self._grid.get_first_child()) is not None:
            self._grid.remove(child)
        self._empty.set_visible(not rows)
        self._grid.set_visible(bool(rows))
        if not rows:
            return

        headings = (
            _label("SESIÓN", "pi-ses-head"),
            _label("PIDS", "pi-ses-head", xalign=1, tooltip=logica.TIP_PIDS),
            _label("PSS ↓", "pi-ses-head", xalign=1, tooltip=logica.TIP_PSS),
            _label("RSS", "pi-ses-head", xalign=1, tooltip=logica.TIP_RSS),
            self._children_heading,
        )
        # GTK has no text-transform: the 08 uppercase headings are written so.
        for column, widget in enumerate(headings):
            self._grid.attach(widget, column, 0, 1, 1)

        line = 1
        for row in rows:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            self._grid.attach(separator, 0, line, 5, 1)
            line += 1
            self._attach_row(row, line)
            line += 1

    def _attach_row(self, row: logica.Row, line: int) -> None:
        sub = row.depth > 0
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cell.set_hexpand(True)
        cell.set_margin_start(_SUB_INDENT * row.depth)
        cell.set_tooltip_text(row.tooltip)

        # Name first and whole (it is the point of the row); the tags wrap
        # under it, so a narrow column never squeezes the name into «m…».
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if sub:
            top.append(_label("└", "muted"))
        name = _label(row.name, "pi-ses-name")
        name.set_wrap(True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.set_max_width_chars(28)
        name.set_hexpand(True)
        top.append(name)
        cell.append(top)
        detail = _label(row.detail, "mono", "muted")
        detail.set_wrap(True)
        detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail.set_max_width_chars(30)
        detail.set_visible(bool(row.detail))
        cell.append(detail)
        if row.tags:
            tags = Gtk.FlowBox()
            tags.set_selection_mode(Gtk.SelectionMode.NONE)
            tags.set_max_children_per_line(6)
            tags.set_column_spacing(4)
            tags.set_row_spacing(2)
            tags.set_halign(Gtk.Align.START)
            for text, kind in row.tags:
                tag = _label(text, "pi-ses-tag", tooltip=text)
                # Ellipsized, a tag never sets the card's minimum width.
                tag.set_ellipsize(Pango.EllipsizeMode.END)
                tag.set_max_width_chars(32)
                if kind:
                    tag.add_css_class(f"pi-ses-tag-{kind}")
                tags.append(tag)
                child = tags.get_last_child()
                child.set_focusable(False)
                child.set_halign(Gtk.Align.START)
            cell.append(tags)
        self._grid.attach(cell, 0, line, 1, 1)

        self._grid.attach(_label(str(row.pid_count), "mono", xalign=1), 1, line, 1, 1)

        pss = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pss.set_hexpand(True)
        pss.set_valign(Gtk.Align.CENTER)
        bar = Gtk.ProgressBar()
        bar.add_css_class("pi-ses-bar")
        if sub:
            bar.add_css_class("pi-ses-sub")
        bar.set_fraction(row.bar)
        bar.set_size_request(_BAR_MIN, -1)
        bar.set_hexpand(True)
        bar.set_valign(Gtk.Align.CENTER)
        pss.append(bar)
        value = _label(row.pss_text, "mono", xalign=1)
        value.set_width_chars(9)
        pss.append(value)
        self._grid.attach(pss, 2, line, 1, 1)

        self._grid.attach(_label(row.rss_text, "mono", "muted", xalign=1), 3, line, 1, 1)
        self._grid.attach(_label(str(row.children), "mono", xalign=1), 4, line, 1, 1)
        for column in range(1, 5):
            widget = self._grid.get_child_at(column, line)
            if widget is not None:
                widget.set_valign(Gtk.Align.CENTER)
