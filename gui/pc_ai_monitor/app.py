"""Application shell: sidebar of sections and the navigation stack.

Sections are peers in the sidebar and each one is a page in an
``Adw.NavigationView``; a drill-down (a group's processes, a model's detail) is a
push on top of the current section. Every page is registered, so a detail view
keeps updating while it is on screen.
"""

# pyright: reportAttributeAccessIssue=false

import sys
import time
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from pc_ai_monitor import config as config_module
from pc_ai_monitor import theme
from pc_ai_monitor.blocks import progreso
from pc_ai_monitor.config import write_template
from pc_ai_monitor.datos import Collector, Snapshot
from pc_ai_monitor.formato import gib
from pc_ai_monitor.pages.home import HomePage
from pc_ai_monitor.pages.motores import MotoresPage
from pc_ai_monitor.pages.procesos import ProcesosPage
from pc_ai_monitor.pages.puertos import PuertosPage
from pc_ai_monitor.pages.recursos import RecursosPage
from pc_ai_monitor.pages.router import RouterPage
from pc_ai_monitor.pages.usage import TokensPage
from pc_ai_monitor.puertos import counts, parse_ss
from pc_ai_monitor.settings import SettingsSection

APP_ID = "local.pcai.monitor.gnome"
WINDOW_TITLE = "MacacoView"
DEFAULT_SECTION = "home"

SECTIONS = (
    ("home", "Home", "go-home-symbolic"),
    ("motores", "Motores", "system-run-symbolic"),
    ("recursos", "Recursos", "view-list-symbolic"),
    ("router", "Router", "network-transmit-receive-symbolic"),
    ("tokens", "Tokens", "emblem-shared-symbolic"),
    ("puertos", "Puertos", "network-wired-symbolic"),
    ("procesos", "Procesos", "system-run-symbolic"),
    ("config", "Configuración", "preferences-system-symbolic"),
)

# Dashboards available by section key; anything else shows the placeholder.
# Home no entra aqui: PageTests fija este registro en seis claves y el shell
# resuelve Home por su cuenta (SHELL_PAGES).
DASHBOARDS = {
    "motores": MotoresPage,
    "recursos": RecursosPage,
    "router": RouterPage,
    "tokens": TokensPage,
    "puertos": PuertosPage,
    "procesos": ProcesosPage,
}

# Secciones del shell que no son dashboards del registro de arriba.
SHELL_PAGES = {
    "home": HomePage,
}

SECTION_LABEL = {key: label for key, label, _icon in SECTIONS}

# Qué fuente alimenta la pill de edad de cada sección (S1 del encargo T1:
# Recursos/Home/Motores/Router -> stats; Puertos -> ports; Tokens -> tokens).
SECTION_SOURCE = {
    "home": "stats",
    "motores": "stats",
    "recursos": "stats",
    "router": "stats",
    "procesos": "stats",
    "puertos": "ports",
    "tokens": "tokens",
}

# Qué colector, si falla, enciende el banner de cada sección. Motores y Home
# leen de stats (el colector «recursos»); config no tiene colector.
SECTION_COLLECTOR = {
    "home": "recursos",
    "motores": "recursos",
    "recursos": "recursos",
    "router": "router",
    "procesos": "recursos",
    "puertos": "puertos",
    "tokens": "tokens",
}

# Edad máxima de los datos antes de que la pill pase a ámbar.
AGE_OK_S = 10.0

DASH = "\u2014"

# Secciones con badge vivo en la barra lateral (diseño 07).
_BADGE_SECTIONS = ("motores", "recursos", "router", "puertos")


def engines_badge(snapshot: Snapshot) -> str:
    """Badge de Motores: el motor sano según el router, o «—».

    Solo Abito: la entrada «flash» del router es el motor viejo, retirado
    (odd/tasks/macacoview-gtk.md L9). Gufo se sondea aparte en T3.
    """
    router = snapshot.router()
    for name in ("abito",):
        engine = router.get(name)
        if isinstance(engine, dict) and engine.get("healthy"):
            return name
    return DASH


def memory_badge(snapshot: Snapshot) -> str:
    """Badge de Recursos: RAM libre, redondeada, o «—» sin dato."""
    value = snapshot.memory().get("available_gib")
    if value is None:
        return DASH
    try:
        return f"{gib(float(value), 0)}G"
    except (TypeError, ValueError):
        return DASH


def local_badge(snapshot: Snapshot) -> str:
    """Badge de Router: porcentaje de decisiones locales, o «—» sin métricas."""
    pct = snapshot.telemetry().get("pct_local")
    return DASH if pct is None else f"{pct}%"


def ports_badge(snapshot: Snapshot) -> str:
    """Badge de Puertos: cuántas escuchas quedan expuestas (acceso «Todas»)."""
    if not snapshot.ports:
        return DASH
    return str(counts(parse_ss(snapshot.ports))[2])


def age_state(seconds: float) -> str:
    """Clase de la pill de edad: verde por debajo de 10 s, ámbar desde 10 s."""
    return "ok" if seconds < AGE_OK_S else "warn"


def error_title(section: str, snapshot: Snapshot) -> str | None:
    """Título del banner de la sección, o None si su colector no falló."""
    collector = SECTION_COLLECTOR.get(section)
    if collector is None:
        return None
    prefix = f"{collector}:"
    if any(error.startswith(prefix) for error in snapshot.errors):
        return f"{SECTION_LABEL.get(section, section)}: el colector no responde"
    return None


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


DESKTOP_ENTRY = Path.home() / ".local/share/applications/macacoview.desktop"


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
        # Chrome por sección: pill de edad y banner de colector (T1).
        self._chrome: dict[Adw.NavigationPage, tuple[str, Gtk.Label, Adw.Banner]] = {}
        self._last_snapshot: Snapshot | None = None
        self._badges: dict[str, Gtk.Label] = {}
        self._listbox: Gtk.ListBox | None = None
        self._key_rows: dict[str, Gtk.ListBoxRow] = {}
        # S14: indicador de acción en curso de cada cabecera y los toasts.
        self._indicators: dict[Adw.NavigationPage, progreso.HeaderIndicator] = {}
        self._toasts: Adw.ToastOverlay | None = None
        # (huella, monotónico) de la última vez que cada fuente cambió de
        # contenido: la pill de edad data datos, no publicaciones.

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

        window = Adw.ApplicationWindow(application=self, title=WINDOW_TITLE)
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

        # Adw.Toast al terminar una acción larga, esté donde esté el usuario.
        toasts = Adw.ToastOverlay()
        toasts.set_child(split)
        self._toasts = toasts
        self._watch_activities()
        window.set_content(toasts)
        window.set_icon_name(self._config.icon)
        self._window = window
        window.present()

        self._select_section(DEFAULT_SECTION)
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
        self._listbox = listbox
        self._key_rows = {}

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
            name.set_hexpand(True)
            content.append(name)

            if key in _BADGE_SECTIONS:
                # Badge vivo, pequeño y apagado: dato de un vistazo, no otra
                # columna de números.
                badge = Gtk.Label(label=DASH, xalign=1)
                badge.add_css_class("sidebar-badge")
                badge.set_valign(Gtk.Align.CENTER)
                content.append(badge)
                self._badges[key] = badge

            row.set_child(content)

            listbox.append(row)
            self._rows[row] = key
            self._key_rows[key] = row

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

        row = self._key_rows.get(key)
        if (
            row is not None
            and self._listbox is not None
            and self._listbox.get_selected_row() is not row
        ):
            # Dispara _on_row_selected, que vuelve aquí con la fila ya
            # puesta y la página cacheada: recursion de un nivel y para.
            self._listbox.select_row(row)

    # -- pages -------------------------------------------------------------

    def _build_section(self, key: str) -> Adw.NavigationPage:
        label = next(name for section, name, _icon in SECTIONS if section == key)

        content: Gtk.Widget
        if key == "config":
            content = SettingsSection(
                self._config, self._apply_config, snapshot=self._last_snapshot
            )
        else:
            factory = SHELL_PAGES.get(key) or DASHBOARDS.get(key)
            if factory is None:
                content = Placeholder(label, "Se está migrando desde la app Tauri.")
            else:
                content = factory()

        return self._wrap(label, content, section=key)

    def _wrap(
        self, title: str, content: Gtk.Widget, section: str | None = None
    ) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=title, subtitle=WINDOW_TITLE)

        pill: Gtk.Label = Gtk.Label(label=DASH)
        pill.add_css_class("age-pill")
        pill.set_valign(Gtk.Align.CENTER)
        pill.set_visible(False)
        title_widget.set_hexpand(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.append(title_widget)
        box.append(pill)
        # S14: la acción larga en curso, visible desde cualquier página.
        indicator = progreso.HeaderIndicator(self._open_activity)
        box.append(indicator)
        header.set_title_widget(box)
        header.pack_start(self._sidebar_toggle())

        banner = Adw.Banner()
        banner.set_revealed(False)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(banner)
        toolbar.add_top_bar(header)
        toolbar.set_content(content)

        page = Adw.NavigationPage(title=title)
        page.set_child(toolbar)
        self._pages[page] = content
        self._indicators[page] = indicator
        if section is not None:
            self._chrome[page] = (section, pill, banner)
        return page

    def _push_detail(self, title: str, content: Gtk.Widget) -> None:
        """Drill-down entry point handed to the pages."""
        self._nav.push(self._wrap(title, content))

    def _on_popped(
        self, _navigation: Adw.NavigationView, page: Adw.NavigationPage
    ) -> None:
        self._pages.pop(page, None)
        self._indicators.pop(page, None)

    # -- acciones largas (S14) -----------------------------------------------

    def _open_activity(self, section: str, anchor: object | None) -> None:
        """Clic en el indicador: abrir la sección y llevar la vista a la tarjeta."""
        if section:
            self._select_section(section)
        widget = anchor if isinstance(anchor, Gtk.Widget) else None
        if widget is not None and widget.get_root() is not None:
            widget.grab_focus()

    def _watch_activities(self) -> None:
        progreso.HUB.on_finished(self._on_activity_finished)

    def _on_activity_finished(self, activity) -> None:
        if self._toasts is None:
            return
        progress = activity.progress()
        toast = Adw.Toast.new(progress.message)
        toast.set_timeout(int(progreso.pl.LINGER_S))
        if activity.section:
            toast.set_button_label("Ver" if progress.state == progreso.pl.SUCCESS else "Ver detalle")
            toast.connect(
                "button-clicked",
                lambda _t, a=activity: self._open_activity(a.section, a.anchor),
            )
        self._toasts.add_toast(toast)

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
        # Una sección que se abre después (Configuración) arranca con esto.
        self._last_snapshot = snapshot
        for page in self._pages.values():
            update: Callable[[Snapshot], None] | None = getattr(
                page, "update_snapshot", None
            )
            if callable(update):
                update(snapshot)

        now = time.monotonic()

        badge_text = {
            "motores": engines_badge(snapshot),
            "recursos": memory_badge(snapshot),
            "router": local_badge(snapshot),
            "puertos": ports_badge(snapshot),
        }
        for key, text in badge_text.items():
            label = self._badges.get(key)
            if label is not None:
                label.set_text(text)

        for section, pill, banner in self._chrome.values():
            title = error_title(section, snapshot)
            if title is not None:
                banner.set_title(title)
                banner.set_revealed(True)
            else:
                banner.set_revealed(False)

            read_at = snapshot.collected_at.get(SECTION_SOURCE.get(section, ""))
            if read_at is None:
                pill.set_visible(False)
                continue
            age = max(0.0, now - read_at)
            pill.set_text(f"{int(age)}s")
            pill.remove_css_class("age-ok")
            pill.remove_css_class("age-warn")
            pill.add_css_class(f"age-{age_state(age)}")
            pill.set_visible(True)

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
