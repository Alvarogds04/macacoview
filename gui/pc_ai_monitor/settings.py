"""Configuración, como una sección más del sidebar.

Junta lo que antes estaba repartido entre el menú de la cabecera y el TOML: tema,
ícono, ritmo de refresco, colectores y el pill del panel. Cada control aplica al
instante; los cambios se agrupan con un pequeño retardo para no reiniciar la
recolección en cada tecla.
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from pc_ai_monitor import config as config_module
from pc_ai_monitor import theme

APPLY_DELAY_MS = 600

# Nombres que existen en cualquier tema de iconos de GNOME, para no tener que
# adivinar el primero.
ICON_PRESETS = (
    ("Monitor", "utilities-system-monitor"),
    ("PC", "computer"),
    ("Terminal", "utilities-terminal"),
    ("Sistema", "preferences-system"),
)


def _split_keys(text: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in text.split(",") if part.strip())


class SettingsSection(Gtk.ScrolledWindow):
    """Preferencias de la app dentro del sidebar (no en una ventana aparte)."""

    def __init__(
        self,
        current: config_module.Config,
        on_apply: Callable[[config_module.Config], None],
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)

        self._config = current
        self._on_apply = on_apply
        self._pending: int | None = None
        self._building = True  # no aplicar mientras se construye

        page = Adw.PreferencesPage()
        self.set_child(page)

        # -- apariencia --------------------------------------------------------
        look = Adw.PreferencesGroup(title="Apariencia")
        page.add(look)

        self._theme_keys = list(theme.THEMES)
        theme_row = Adw.ComboRow(title="Tema")
        theme_row.set_model(
            Gtk.StringList.new([theme.THEMES[key].label for key in self._theme_keys])
        )
        if current.theme in self._theme_keys:
            theme_row.set_selected(self._theme_keys.index(current.theme))
        theme_row.connect("notify::selected", self._on_change)
        look.add(theme_row)
        self._theme_row = theme_row

        icon_row = Adw.EntryRow(title="Ícono (nombre del tema de iconos)")
        icon_row.set_text(current.icon)
        self._icon_preview = Gtk.Image.new_from_icon_name(current.icon)
        self._icon_preview.set_pixel_size(24)
        icon_row.add_suffix(self._icon_preview)
        for label, name in ICON_PRESETS:
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.set_tooltip_text(name)
            button.connect(
                "clicked", lambda *_args, chosen=name: icon_row.set_text(chosen)
            )
            icon_row.add_suffix(button)
        icon_row.connect("notify::text", self._on_icon_changed)
        look.add(icon_row)
        self._icon_row = icon_row

        # -- refresco ----------------------------------------------------------
        refresh = Adw.PreferencesGroup(
            title="Refresco",
            description="Cada cuánto se consultan los colectores. Más seguido = más carga.",
        )
        page.add(refresh)

        self._stats = self._spin("Recursos", current.stats_interval_s, 0.5, 30.0)
        self._ports = self._spin("Puertos", current.ports_interval_s, 0.5, 60.0)
        self._tokens = self._spin("Tokens", current.tokens_interval_s, 2.0, 120.0)
        for row in (self._stats, self._ports, self._tokens):
            refresh.add(row)

        # -- colectores --------------------------------------------------------
        sources = Adw.PreferencesGroup(
            title="Colectores",
            description="Rutas de los scripts. Si falta uno, esa sección queda en «sin datos».",
        )
        page.add(sources)

        self._stats_bin = self._entry("Recursos", str(current.stats_bin))
        self._tokens_bin = self._entry("Tokens", str(current.tokens_bin))
        self._helper = self._entry("Helper de puertos", str(current.ports_helper))
        for row in (self._stats_bin, self._tokens_bin, self._helper):
            sources.add(row)

        self._sudo = Adw.SwitchRow(title="Usar sudo -n para los puertos")
        self._sudo.set_subtitle(
            "Sin contraseña: si falta el helper, la pestaña queda vacía."
        )
        self._sudo.set_active(current.ports_use_sudo)
        self._sudo.connect("notify::active", self._on_change)
        sources.add(self._sudo)

        # -- panel -------------------------------------------------------------
        panel = Adw.PreferencesGroup(
            title="Pill del panel",
            description=(
                "Qué muestra la extensión de GNOME en la barra superior. Las claves son "
                "pi, hermes, firefox o el alias de un modelo (por ejemplo abito-gpt)."
            ),
        )
        page.add(panel)

        self._hide_zero = Adw.SwitchRow(title="Ocultar valores en cero")
        self._hide_zero.set_active(current.bar_hide_zero)
        self._hide_zero.connect("notify::active", self._on_change)
        panel.add(self._hide_zero)

        self._always = self._entry(
            "Mostrar aunque estén en cero", ", ".join(current.bar_always_show)
        )
        self._only = self._entry(
            "Mostrar SOLO (vacío = todo)", ", ".join(current.bar_only)
        )
        panel.add(self._always)
        panel.add(self._only)

        self._units = Adw.ComboRow(title="Unidades")
        self._units.set_model(Gtk.StringList.new(["GiB", "Porcentaje"]))
        self._units.set_selected(1 if current.bar_units == "percent" else 0)
        self._units.connect("notify::selected", self._on_change)
        panel.add(self._units)

        self._models = Adw.ComboRow(title="Con varios modelos")
        self._models.set_model(
            Gtk.StringList.new(["Un chip por modelo", "Una métrica sumada"])
        )
        self._models.set_selected(1 if current.bar_models == "combined" else 0)
        self._models.connect("notify::selected", self._on_change)
        panel.add(self._models)

        # Opt-in y desactivado de fabrica: la barra queda como estaba hasta que
        # se encienda esta opcion.
        self._router_indicators = Adw.SwitchRow(
            title="Mostrar estado del router en la barra"
        )
        self._router_indicators.set_subtitle(
            "A/F vivos · JEV · sesiones/slots · RAM libre · cola"
        )
        self._router_indicators.set_active(current.bar_router_indicators)
        self._router_indicators.connect("notify::active", self._on_change)
        panel.add(self._router_indicators)

        self._building = False

    # -- filas ----------------------------------------------------------------

    def _spin(self, title: str, value: float, low: float, high: float) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(low, high, 0.5)
        row.set_title(f"{title} cada (s)")
        row.set_digits(1)
        row.set_value(value)
        row.connect("notify::value", self._on_change)
        return row

    def _entry(self, title: str, value: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(value)
        # `notify::text` cubre el tipeo (agrupado por el retardo) y `apply` el Enter.
        row.connect("notify::text", self._on_change)
        row.connect("apply", self._on_change)
        return row

    # -- aplicación -----------------------------------------------------------

    def _on_icon_changed(self, row: Adw.EntryRow, _param) -> None:
        self._icon_preview.set_from_icon_name(row.get_text().strip())
        self._on_change()

    def _collect(self) -> config_module.Config:
        index = max(0, min(len(self._theme_keys) - 1, self._theme_row.get_selected()))
        icon = self._icon_row.get_text().strip() or self._config.icon
        return config_module.Config(
            stats_bin=Path(
                self._stats_bin.get_text().strip() or self._config.stats_bin
            ).expanduser(),
            tokens_bin=Path(
                self._tokens_bin.get_text().strip() or self._config.tokens_bin
            ).expanduser(),
            ports_helper=Path(
                self._helper.get_text().strip() or self._config.ports_helper
            ).expanduser(),
            ports_use_sudo=self._sudo.get_active(),
            stats_interval_s=self._stats.get_value(),
            ports_interval_s=self._ports.get_value(),
            tokens_interval_s=self._tokens.get_value(),
            history_capacity=self._config.history_capacity,
            theme=self._theme_keys[index],
            icon=icon,
            bar_hide_zero=self._hide_zero.get_active(),
            bar_always_show=_split_keys(self._always.get_text()),
            bar_only=_split_keys(self._only.get_text()),
            bar_units="percent" if self._units.get_selected() == 1 else "gb",
            bar_models="combined" if self._models.get_selected() == 1 else "separate",
            bar_router_indicators=self._router_indicators.get_active(),
        )

    def _on_change(self, *_args) -> None:
        if self._building:
            return
        if self._pending is not None:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(APPLY_DELAY_MS, self._apply)

    def _apply(self) -> bool:
        self._pending = None
        updated = self._collect()
        config_module.save(updated)
        self._config = updated
        self._on_apply(updated)
        return False
