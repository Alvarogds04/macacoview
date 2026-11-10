"""Configuración, como una sección más del sidebar.

Junta lo que antes estaba repartido entre el menú de la cabecera y el TOML: tema,
ícono, ritmo de refresco, colectores y el pill del panel. Cada control aplica al
instante; los cambios se agrupan con un pequeño retardo para no reiniciar la
recolección en cada tecla.
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from pc_ai_monitor import config as config_module
from pc_ai_monitor import theme
from pc_ai_monitor.blocks import programas_vista as programas
from pc_ai_monitor.blocks.programas_dialogo import AddProgramDialog
from pc_ai_monitor.blocks.tablas_vista import pill_chips
from pc_ai_monitor.datos import Snapshot

APPLY_DELAY_MS = 600

# Nombres que existen en cualquier tema de iconos de GNOME, para no tener que
# adivinar el primero.
ICON_PRESETS = (
    ("Monitor", "utilities-system-monitor"),
    ("PC", "computer"),
    ("Terminal", "utilities-terminal"),
    ("Sistema", "preferences-system"),
)

# Réplica de la barra superior de GNOME: negra siempre, sea cual sea el tema
# de la app; la pill es el elemento que consume la extensión.
PREVIEW_CSS = """
.mv-topbar {
  background-color: #000;
  border: 1px solid @theme_border;
  border-radius: 8px 8px 0 0;
  padding: 0 14px;
  min-height: 30px;
}
.mv-topbar-label {
  color: #e8e8e8;
  opacity: 0.75;
  font-size: 12px;
  font-weight: 600;
}
.mv-topbar-pill {
  background-color: #1c1c1e;
  border: 1px solid #333;
  border-radius: 999px;
  padding: 3px 12px;
}
.mv-topbar-pill label {
  color: #e8e8e8;
  font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}
.mv-preview-note {
  background-color: @theme_subtle;
  border: 1px solid @theme_border;
  border-radius: 0 0 8px 8px;
  border-top: 0;
  padding: 7px 12px;
  font-size: 12px;
}
"""

_preview_provider = None


def _ensure_preview_css() -> None:
    """Register the top-bar mock CSS once per display (no-op without one)."""
    global _preview_provider
    if _preview_provider is not None:
        return
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(PREVIEW_CSS, -1)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _preview_provider = provider


def _split_keys(text: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in text.split(",") if part.strip())


def _clock_text() -> str:
    """Clock of the mock bar: the real current time, formatted like GNOME."""
    from gi.repository import GLib

    now = GLib.DateTime.new_now_local()
    return now.format("%a %d %b  %H:%M") if now else ""


class SettingsSection(Gtk.ScrolledWindow):
    """Preferencias de la app dentro del sidebar (no en una ventana aparte)."""

    def __init__(
        self,
        current: config_module.Config,
        on_apply: Callable[[config_module.Config], None],
        snapshot: Snapshot | None = None,
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)

        self._config = current
        self._on_apply = on_apply
        self._snapshot = snapshot
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

        # Vista previa viva: los chips que la config actual produciría con la
        # última medición. Sin medición no hay chips: nunca datos inventados.
        _ensure_preview_css()
        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("mv-topbar")
        for label in ("Actividades", _clock_text()):
            text = Gtk.Label(label=label, xalign=0)
            text.add_css_class("mv-topbar-label")
            bar.append(text)
        self._pill_preview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._pill_preview.add_css_class("mv-topbar-pill")
        bar.append(self._pill_preview)
        preview.append(bar)
        preview_note = Gtk.Label(xalign=0)
        preview_note.add_css_class("mv-preview-note")
        preview_note.add_css_class("muted")
        preview_note.set_wrap(True)
        preview_note.set_text(
            "Vista previa de la barra superior de GNOME: así queda la pill con estos "
            "ajustes y la última medición. La consume la extensión, no esta app."
        )
        preview.append(preview_note)
        panel.add(preview)

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

        # Opt-in, apagado de fábrica: los programas no tocan grupos ni modelos.
        # Controla la barra y Recursos (L23). Apagarlo no borra la selección
        # ni cambia qué se muestrea.
        self._programs_enabled = Adw.SwitchRow(title=programas.SWITCH_TITLE)
        self._programs_enabled.set_subtitle(programas.SWITCH_SUBTITLE)
        self._programs_enabled.set_active(current.bar_programs_enabled)
        self._programs_enabled.connect("notify::active", self._on_change)
        panel.add(self._programs_enabled)

        # -- RAM por programa ----------------------------------------------------
        # S19: solo lo fijado, un estado vacío y «Agregar programa…». Las
        # filas salen del snapshot habitual: abrir la sección no escanea nada.
        self._program_group = Adw.PreferencesGroup(
            title=programas.GROUP_TITLE,
            description=programas.GROUP_DESCRIPTION,
        )
        page.add(self._program_group)
        self._program_group.set_header_suffix(self._program_help())
        self._program_rows: dict[str, Adw.ActionRow] = {}
        self._program_values: dict[str, Gtk.Label] = {}
        self._program_selection = set(current.bar_program_ids)
        self._program_order = list(dict.fromkeys(current.bar_program_ids))
        self._add_dialog: AddProgramDialog | None = None
        self._program_empty = self._program_empty_row()
        self._program_group.add(self._program_empty)
        self._program_add = self._program_add_row()
        self._program_group.add(self._program_add)
        self._refresh_programs()

        # -- monitoreo ---------------------------------------------------------
        watching = Adw.PreferencesGroup(
            title="Monitoreo",
            description=(
                "Grupos de procesos que arma el colector (sección [[watch]] del "
                "TOML). Apagá el que no quieras ver en la barra ni en la app."
            ),
        )
        page.add(watching)

        # Una casilla por entrada: visible = se monitorea; apagado = el grupo
        # no se arma ni aparece, en el pill y en la app.
        self._watch_switches: list[Adw.SwitchRow] = []
        for entry in current.watch:
            row = Adw.SwitchRow(title=f"{entry.icon} {entry.name}".strip())
            if entry.match:
                row.set_subtitle("Coincide con: " + ", ".join(entry.match))
            elif entry.name == "system":
                row.set_subtitle("Regla fija del colector: procesos de root")
            else:
                row.set_subtitle("Regla fija del colector: el resto de los procesos")
            row.set_active(entry.visible)
            row.connect("notify::active", self._on_change)
            watching.add(row)
            self._watch_switches.append(row)

        self._building = False
        self._refresh_preview()

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

    # -- vista previa del pill -------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        """Refresh the pill preview with the latest measurement.

        Called by the shell when a new snapshot arrives; the preview must
        never show stale chips when the machine changes under the settings.
        """
        self._snapshot = snapshot
        self._refresh_programs()
        self._refresh_preview()

    # -- RAM por programa ------------------------------------------------------

    def _program_help(self) -> Gtk.Widget:
        """«¿Qué es PSS?»: the long explanation and the coverage, off the list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        explanation = Gtk.Label(label=programas.PICKER_DESCRIPTION, xalign=0)
        explanation.set_wrap(True)
        explanation.set_max_width_chars(52)
        box.append(explanation)
        self._program_status = Gtk.Label(xalign=0)
        self._program_status.add_css_class("dim-label")
        self._program_status.set_wrap(True)
        self._program_status.set_max_width_chars(52)
        box.append(self._program_status)
        popover = Gtk.Popover()
        popover.set_child(box)
        button = Gtk.MenuButton(icon_name="help-about-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("¿Qué es PSS y por qué no se suma?")
        button.set_popover(popover)
        return button

    def _program_empty_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(title=programas.EMPTY_TITLE)
        row.set_subtitle(programas.EMPTY_SUBTITLE)
        row.add_prefix(Gtk.Image.new_from_icon_name("view-pin-symbolic"))
        button = Gtk.Button(label=programas.ADD_LABEL)
        button.add_css_class("suggested-action")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_args: self._open_add_dialog())
        row.add_suffix(button)
        return row

    def _program_add_row(self) -> Adw.PreferencesRow:
        row = Adw.PreferencesRow(title=programas.ADD_LABEL)
        row.set_activatable(False)
        button = Gtk.Button(label=programas.ADD_LABEL)
        button.add_css_class("flat")
        button.add_css_class("accent")
        button.set_halign(Gtk.Align.START)
        button.set_margin_start(6)
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        button.connect("clicked", lambda *_args: self._open_add_dialog())
        row.set_child(button)
        return row

    def _pinned_row(self, key: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=f"Programa · {programas.short_id(key)}")
        row.set_use_markup(False)
        value = Gtk.Label()
        value.add_css_class("numeric")
        value.set_valign(Gtk.Align.CENTER)
        row.add_suffix(value)
        remove = Gtk.Button(label=programas.REMOVE_LABEL)
        remove.set_valign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda *_args: self._set_pinned(key, False))
        row.add_suffix(remove)
        self._program_values[key] = value
        row._remove_button = remove
        return row

    def _paint_pinned(self, key: str, program: dict | None) -> None:
        row = self._program_rows[key]
        value = self._program_values[key]
        if program:
            name = programas.human_name(program)
            row.set_title(name)
            row.set_subtitle(programas.program_kind(program))
            value.set_text(programas.memory_text(program) or "")
            value.remove_css_class("dim-label")
        else:
            name = row.get_title()
            row.set_subtitle(programas.ABSENT_KIND)
            value.set_text(programas.ABSENT_VALUE)
            value.add_css_class("dim-label")
        row.set_tooltip_text(programas.program_tooltip(program, key))
        row._remove_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"Quitar {name} de la barra"]
        )

    def _refresh_programs(self) -> None:
        """Only the pinned rows; polling never applies nor overwrites toggles."""
        snapshot = self._snapshot or Snapshot()
        live = programas.live_programs(snapshot.programs())
        for key in list(self._program_rows):
            if key not in self._program_selection:
                self._program_group.remove(self._program_rows.pop(key))
                self._program_values.pop(key, None)
        added = False
        for key in self._program_order:
            if key not in self._program_selection:
                continue
            if key not in self._program_rows:
                self._program_rows[key] = self._pinned_row(key)
                self._program_group.add(self._program_rows[key])
                added = True
            self._paint_pinned(key, live.get(key))
        if added:  # «Agregar programa…» stays the last row
            self._program_group.remove(self._program_add)
            self._program_group.add(self._program_add)
        pinned = bool(self._program_rows)
        self._program_empty.set_visible(not pinned)
        self._program_add.set_visible(pinned)
        title, subtitle = programas.coverage_summary(snapshot.stats)
        self._program_status.set_text(f"{title}\n{subtitle}")

    def _is_pinned(self, key: str) -> bool:
        return key in self._program_selection

    def _set_pinned(self, key: str, pinned: bool) -> None:
        if pinned:
            self._program_selection.add(key)
            if key not in self._program_order:
                self._program_order.append(key)
        else:
            self._program_selection.discard(key)
            if key in self._program_order:
                self._program_order.remove(key)
        self._refresh_programs()
        self._on_change()

    def _open_add_dialog(self) -> None:
        snapshot = self._snapshot or Snapshot()
        dialog = AddProgramDialog(
            snapshot.programs(), self._is_pinned, self._set_pinned
        )
        self._add_dialog = dialog
        dialog.connect("closed", self._on_add_dialog_closed)
        dialog.present(self)

    def _on_add_dialog_closed(self, dialog) -> None:
        if self._add_dialog is dialog:
            self._add_dialog = None

    def _refresh_preview(self) -> None:
        while (child := self._pill_preview.get_first_child()) is not None:
            self._pill_preview.remove(child)
        config = self._collect()
        chips = pill_chips(config, self._snapshot)
        if chips:
            for text, tip in chips:
                chip = Gtk.Label(label=text, xalign=0)
                chip.set_tooltip_text(tip)
                self._pill_preview.append(chip)
        else:
            empty = Gtk.Label(label="sin datos", xalign=0)
            empty.add_css_class("muted")
            self._pill_preview.append(empty)

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
            bar_programs_enabled=self._programs_enabled.get_active(),
            bar_program_ids=tuple(sorted(self._program_selection)),
            watch=tuple(
                replace(entry, visible=self._watch_switches[index].get_active())
                for index, entry in enumerate(self._config.watch)
            ),
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
        # Ruta explícita: los tests apuntan CONFIG_PATH a un directorio temporal.
        config_module.save(updated, config_module.CONFIG_PATH)
        self._config = updated
        self._refresh_preview()
        self._on_apply(updated)
        return False
