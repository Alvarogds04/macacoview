"""Application shell: sidebar of sections and the navigation stack.

Sections are peers in the sidebar and each one is a page in an
``Adw.NavigationView``; a drill-down (a group's processes, a model's detail) is a
push on top of the current section. Every page is registered, so a detail view
keeps updating while it is on screen.
"""

# pyright: reportAttributeAccessIssue=false

import sys
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from pc_ai_monitor import config as config_module
from pc_ai_monitor import theme
from pc_ai_monitor.config import write_template
from pc_ai_monitor.datos import Collector, Snapshot
from pc_ai_monitor.pages.procesos import ProcesosPage
from pc_ai_monitor.pages.puertos import PuertosPage
from pc_ai_monitor.pages.recursos import RecursosPage
from pc_ai_monitor.pages.router import RouterPage
from pc_ai_monitor.pages.usage import TokensPage
from pc_ai_monitor.settings import SettingsSection

APP_ID = "local.pcai.monitor.gnome"

SECTIONS = (
    ("recursos", "Recursos", "view-list-symbolic"),
    ("router", "Router", "network-transmit-receive-symbolic"),
    ("tokens", "Tokens", "emblem-shared-symbolic"),
    ("puertos", "Puertos", "network-wired-symbolic"),
    ("procesos", "Procesos", "system-run-symbolic"),
    ("config", "Configuración", "preferences-system-symbolic"),
)

# Dashboards available by section key; anything else shows the placeholder.
DASHBOARDS = {
    "recursos": RecursosPage,
    "router": RouterPage,
    "tokens": TokensPage,
    "puertos": PuertosPage,
    "procesos": ProcesosPage,
}


class Placeholder(Gtk.Box):
    """A section that has not been migrated from the Tauri app yet."""

    def __init__(self, title: str, detail: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

        heading = Gtk.Label(label=title)
        heading.add_css_class("kpi-value")
        self.append(heading)

        text = Gtk.Label(label=detail, justify=Gtk.Justification.CENTER)
        text.add_css_class("muted")
        self.append(text)


DESKTOP_ENTRY = Path.home() / ".local/share/applications/pc-ai-monitor-gnome.desktop"


def update_desktop_icon(name: str) -> None:
    """Mantiene el ícono del menú en sincronía con el de la ventana.

    En Wayland el ícono lo aporta el .desktop, no la ventana, así que hay que
    escribir los dos.
    """
    try:
        text = DESKTOP_ENTRY.read_text()
    except OSError:
        return

    lines = [
        f"Icon={name}" if line.startswith("Icon=") else line
        for line in text.splitlines()
    ]
    try:
        DESKTOP_ENTRY.write_text("\n".join(lines) + "\n")
    except OSError:
        return


class MonitorApplication(Adw.Application):
    def __init__(self, self_test: bool = False) -> None:
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self._self_test = self_test
        self._collector: Collector | None = None
        self._config = config_module.load()
        self._theme_key = self._config.theme

        # Every pushed page is tracked so its content keeps receiving snapshots.
        self._window: Adw.ApplicationWindow | None = None
        self._pages: dict[Adw.NavigationPage, Gtk.Widget] = {}
        self._sections: dict[str, Adw.NavigationPage] = {}
        self._rows: dict[Gtk.ListBoxRow, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def do_activate(self) -> None:
        # Segunda activación (menú del panel, ícono del menú, lanzador repetido):
        # traer al frente la ventana que ya existe, no construir otra. Reconstruir
        # volvería a registrar acciones y la activación fallaba en silencio.
        if self._window is not None:
            self._window.present()
            return

        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        theme.load_external()
        self._apply_theme(self._theme_key)

        window = Adw.ApplicationWindow(application=self, title="PC-AI Monitor")
        window.set_default_size(1180, 800)

        navigation = Adw.NavigationView()
        self._nav = navigation

        split = Adw.OverlaySplitView()
        # Angosto a propósito: es un índice de secciones, no contenido.
        split.set_min_sidebar_width(150)
        split.set_max_sidebar_width(180)
        # Arranca colapsado: el contenido usa todo el ancho y libadwaita mueve los
        # botones de ventana a la cabecera del contenido mientras está oculto. El
        # botón de la barra superior lo abre.
        split.set_show_sidebar(False)
        split.set_sidebar(self._build_sidebar())
        split.set_content(navigation)
        self._split = split

        window.set_content(split)
        window.set_icon_name(self._config.icon)
        self._window = window
        window.present()

        self._select_section("recursos")
        write_template()

        self._collector = Collector(self._config)
        self._collector.subscribe(self._on_snapshot)
        self._collector.start()

        if self._self_test:
            GLib.timeout_add(3000, self._finish_self_test)

    def do_shutdown(self) -> None:
        if self._collector is not None:
            self._collector.stop()
            self._collector = None
        Adw.Application.do_shutdown(self)

    # -- sidebar -----------------------------------------------------------

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar())

        listbox = Gtk.ListBox()
        listbox.add_css_class("navigation-sidebar")
        listbox.connect("row-selected", self._on_row_selected)

        for key, label, icon in SECTIONS:
            if key == "config":
                # Separador: la configuración no es un dashboard más.
                listbox.append(Gtk.Separator())

            row = Gtk.ListBoxRow()
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            content.set_margin_top(3)
            content.set_margin_bottom(3)
            content.set_margin_start(7)
            content.set_margin_end(7)

            glyph = Gtk.Image.new_from_icon_name(icon)
            glyph.set_pixel_size(14)
            content.append(glyph)

            name = Gtk.Label(label=label, xalign=0)
            name.add_css_class("sidebar-label")
            content.append(name)
            row.set_child(content)

            listbox.append(row)
            self._rows[row] = key

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(listbox)
        box.append(scroller)

        return box

    def _on_row_selected(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            return
        key = self._rows.get(row)
        if key is not None:
            self._select_section(key)

    def _select_section(self, key: str) -> None:
        page = self._sections.get(key)
        if page is None:
            page = self._build_section(key)
            self._sections[key] = page
        self._nav.replace([page])

    # -- pages -------------------------------------------------------------

    def _build_section(self, key: str) -> Adw.NavigationPage:
        label = next(name for section, name, _icon in SECTIONS if section == key)

        content: Gtk.Widget
        if key == "config":
            content = SettingsSection(self._config, self._apply_config)
        else:
            factory = DASHBOARDS.get(key)
            if factory is None:
                content = Placeholder(label, "Se está migrando desde la app Tauri.")
            else:
                content = factory()

        return self._wrap(label, content)

    def _wrap(self, title: str, content: Gtk.Widget) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))
        header.pack_start(self._sidebar_toggle())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)

        page = Adw.NavigationPage(title=title)
        page.set_child(toolbar)
        self._pages[page] = content
        return page

    def _push_detail(self, title: str, content: Gtk.Widget) -> None:
        """Drill-down entry point handed to the pages."""
        self._nav.push(self._wrap(title, content))

    def _on_popped(
        self, _navigation: Adw.NavigationView, page: Adw.NavigationPage
    ) -> None:
        self._pages.pop(page, None)

    # -- chrome ------------------------------------------------------------

    def _sidebar_toggle(self) -> Gtk.ToggleButton:
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        toggle.set_tooltip_text("Mostrar u ocultar la barra lateral")
        self._split.bind_property(
            "show-sidebar",
            toggle,
            "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        return toggle

    def _apply_config(self, updated: config_module.Config) -> None:
        """Reinicia el colector con la config nueva y re-aplica el tema.

        Se llama con cada cambio del diálogo: el colector es un hilo que sólo lanza
        los scripts, así que recrearlo es barato.
        """
        self._config = updated
        self._apply_theme(updated.theme)
        if self._window is not None:
            self._window.set_icon_name(updated.icon)
        update_desktop_icon(updated.icon)

        if self._collector is not None:
            self._collector.stop()
        self._collector = Collector(updated)
        self._collector.subscribe(self._on_snapshot)
        self._collector.start()

    def _apply_theme(self, key: str) -> None:
        palette = theme.get(key)
        if key != self._theme_key:
            # Elegir tema a mano lo persiste: es la misma preferencia que el diálogo.
            self._config = (
                self._config.with_theme(palette.key)
                if hasattr(self._config, "with_theme")
                else self._config
            )
            config_module.save(self._config)
        display = Gdk.Display.get_default()
        if display is not None:
            theme.install(display, palette)
        self._theme_key = palette.key

        for page in self._pages.values():
            redraw: Callable[[], None] | None = getattr(page, "redraw", None)
            if callable(redraw):
                redraw()

    # -- data --------------------------------------------------------------

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        for page in self._pages.values():
            update: Callable[[Snapshot], None] | None = getattr(
                page, "update_snapshot", None
            )
            if callable(update):
                update(snapshot)

    def _finish_self_test(self) -> bool:
        snapshot = self._collector.snapshot() if self._collector is not None else None
        if snapshot is None:
            print("self-test: sin collector")
        else:
            print(
                "self-test: stats",
                bool(snapshot.stats),
                "| tokens",
                bool(snapshot.tokens),
                "| historial",
                len(snapshot.history),
                "| errores",
                snapshot.errors or "ninguno",
            )
        print(
            "self-test: paginas registradas",
            len(self._pages),
            "| secciones",
            len(self._sections),
            "| tema",
            theme.active().label,
        )
        self.quit()
        return False


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv)
    self_test = "--self-test" in arguments
    application = MonitorApplication(self_test=self_test)
    return application.run([arg for arg in arguments if arg != "--self-test"])
