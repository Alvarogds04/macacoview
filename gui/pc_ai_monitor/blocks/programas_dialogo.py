"""«Agregar programa…» (S19, proposal 10 section C).

A modal ``Adw.Dialog`` over Configuración: a search box, the candidates
sorted by RSS (15 without a query, every match up to 50 with one) and a
«Fijar» / «Fijado ✓» toggle per row. Small (< 50 MiB) and unreadable
(«comm», root or another user's) programs are hidden until «Incluir
procesos del sistema y los chicos» is on. The list is the snapshot the
dialog opened with: rows do not jump under the pointer while it is open.
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Callable
from typing import Any

from gi.repository import Adw, Gtk

from pc_ai_monitor.blocks import programas_vista as programas


class AddProgramDialog(Adw.Dialog):
    def __init__(
        self,
        snapshot_programs: list[dict[str, Any]],
        is_pinned: Callable[[str], bool],
        on_toggle: Callable[[str, bool], None],
    ) -> None:
        super().__init__(title="Agregar programa")
        self.set_content_width(560)
        self.set_content_height(680)
        self._programs = list(snapshot_programs)
        self._is_pinned = is_pinned
        self._on_toggle = on_toggle
        self.rows: dict[str, Adw.ActionRow] = {}
        self._shown: list[str] = []

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_bottom(16)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("firefox, rustdesk, codegraph…")
        self.search.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Buscar por nombre o carpeta"]
        )
        # «changed», not «search-changed»: filtering ~650 dicts is instant
        # and the list must match the text right away.
        self.search.connect("changed", self._refill)
        body.append(self.search)

        self.note = Gtk.Label(xalign=0)
        self.note.add_css_class("dim-label")
        self.note.add_css_class("caption")
        self.note.set_wrap(True)
        body.append(self.note)

        self.list = Gtk.ListBox()
        self.list.add_css_class("boxed-list")
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.list)
        body.append(scroller)

        footer = Gtk.ListBox()
        footer.add_css_class("boxed-list")
        footer.set_selection_mode(Gtk.SelectionMode.NONE)
        self.include_all = Adw.SwitchRow(title=programas.INCLUDE_ALL_TITLE)
        self.include_all.set_subtitle(programas.INCLUDE_ALL_SUBTITLE)
        self.include_all.set_active(False)
        self.include_all.connect("notify::active", self._refill)
        footer.append(self.include_all)
        body.append(footer)

        toolbar.set_content(body)
        self.set_child(toolbar)
        self.set_focus(self.search)
        self._refill()

    # -- list ---------------------------------------------------------------

    def shown_ids(self) -> list[str]:
        """IDs of the rows on screen, in order."""
        return list(self._shown)

    def _refill(self, *_args) -> None:
        query = self.search.get_text()
        shown, total = programas.candidates(
            self._programs, query, self.include_all.get_active()
        )
        self.list.remove_all()
        self.rows = {}
        self._shown = []
        for program in shown:
            row = self._row(program)
            self.rows[program["id"]] = row
            self._shown.append(program["id"])
            self.list.append(row)
        self.note.set_text(self._note(query, len(shown), total))

    @staticmethod
    def _note(query: str, shown: int, total: int) -> str:
        if not query.strip():
            return "Los 15 que más RAM usan ahora · ordenado por RSS ↓"
        if total == 0:
            return "Nada coincide. Probá con otro nombre o incluí los del sistema."
        if total > shown:
            return f"Mostrando {shown} de {total} · refiná la búsqueda"
        return f"{total} coincidencia{'s' if total != 1 else ''} · ordenado por RSS ↓"

    def _row(self, program: dict[str, Any]) -> Adw.ActionRow:
        key = program["id"]
        row = Adw.ActionRow(title=programas.human_name(program))
        row.set_use_markup(False)
        row.set_subtitle(programas.program_kind(program))
        row.set_tooltip_text(programas.program_tooltip(program, key))

        value = Gtk.Label(label=programas.rss_text(program))
        value.add_css_class("numeric")
        value.set_valign(Gtk.Align.CENTER)
        row.add_suffix(value)

        button = Gtk.Button()
        button.set_valign(Gtk.Align.CENTER)
        self._paint(button, self._is_pinned(key))
        button.connect("clicked", self._on_clicked, key)
        row.add_suffix(button)
        return row

    @staticmethod
    def _paint(button: Gtk.Button, pinned: bool) -> None:
        button.set_label(programas.PINNED_LABEL if pinned else programas.PIN_LABEL)
        if pinned:
            button.add_css_class("suggested-action")
        else:
            button.remove_css_class("suggested-action")

    def _on_clicked(self, button: Gtk.Button, key: str) -> None:
        pinned = not self._is_pinned(key)
        self._on_toggle(key, pinned)
        self._paint(button, self._is_pinned(key))
