"""Bloque «Actualizaciones del PC»: todo lo actualizable en esta máquina (S6).

Lista cada componente que informa ``local-ai-models updates --json``
(contrato L8/L11) con su versión instalada, píldora de estado, eta y botón
«Actualizar»; «Actualizar todo (N)» corre los actualizables en orden,
uno detrás de otro. El motor Gufo usa la misma orden que el resto
(``update engine-gufo``) — la tarjeta de un solo estado sigue en Home, y la
tarjeta larga de texto de ``blocks.engine`` ya no se muestra en esta página.

Toda decisión de texto vive en ``actualizaciones_logica`` (testeada sin
GTK); este widget sólo lee fuentes y pinta. Las órdenes corren en un worker
con el ``run_manager`` acotado de ``blocks.motores``: el hilo principal de
GTK nunca se bloquea. Confirmaciones al estilo de ``blocks.engine``: una
general (eta + si el motor se reinicia) y una segunda explícita cuando el
componente compila llama.cpp (``--build``, decenas de minutos, reinicia
Abito).
"""

# pyright: reportAttributeAccessIssue=false

import threading
import time

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks import Block, progreso
from pc_ai_monitor.blocks import progreso_logica as pl
from pc_ai_monitor.blocks.actualizaciones_logica import (
    BUILD_CONFIRM_TEXT,
    BUILD_CONFIRM_TITLE,
    CliResult,
    UpdateRow,
    UpdatesView,
    confirm_text,
    disabled_reason,
    parse_update_result,
    parse_updates,
    update_args,
)
from pc_ai_monitor.blocks.motores import run_manager, stream_manager
from pc_ai_monitor.datos import Snapshot

REFRESH_INTERVAL_S = 60.0
# Compilar llama.cpp lleva decenas de minutos: el plazo tiene que cubrirlo.
UPDATE_TIMEOUT_S = 2700

# Icono por clase de componente; el fallback cubre clases que aparezcan
# más adelante en el contrato (S6: «modelos motores y eso»).
_KIND_ICONS = {
    "engine": "system-run-symbolic",
    "model": "media-optical-symbolic",
    "tool": "applications-utilities-symbolic",
}
_FALLBACK_ICON = "package-x-generic-symbolic"

_PILL_CLASSES = {"ok": "vpill-ok", "acc": "vpill-acc", "warn": "vpill-warn"}


class ActualizacionesBlock(Block):
    """Lista de actualizaciones del PC con sus confirmaciones."""

    title = "ACTUALIZACIONES DEL PC"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._view = UpdatesView()
        self._busy = False
        self._refreshing = False
        self._last_refresh = 0.0
        self._rows: dict[str, dict] = {}

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_max_width_chars(55)
        self._error_label.set_visible(False)
        self.body.append(self._error_label)

        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.append(self._rows_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        all_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._all_button = Gtk.Button(label="Actualizar todo")
        self._all_button.set_halign(Gtk.Align.START)
        self._all_button.connect("clicked", self._on_update_all_clicked)
        all_box.append(self._all_button)
        # S16: el motivo de un botón apagado se lee, no sólo en el tooltip.
        self._all_why = _why_label()
        all_box.append(self._all_why)
        actions.append(all_box)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        actions.append(self._spinner)

        self._note = Gtk.Label(xalign=0)
        self._note.add_css_class("muted")
        self._note.set_wrap(True)
        self._note.set_max_width_chars(55)
        self._note.set_hexpand(True)
        actions.append(self._note)
        self.body.append(actions)

        self._render()
        self._refresh(force=True)
        # Un botón nunca invita a repetir una actualización que ya corre.
        progreso.HUB.subscribe_widget(self._render)

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._refresh(force=False)

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing or self._busy:
            return
        if not force and now - self._last_refresh < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_refresh = now

        def _worker() -> None:
            result = run_manager(("updates", "--json"))
            GLib.idle_add(self._on_list_loaded, _as_cli(result))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_list_loaded(self, result: CliResult) -> bool:
        self._refreshing = False
        self._view = parse_updates(result)
        self._render()
        return False

    # -- pintado -----------------------------------------------------------

    def _render(self) -> None:
        view = self._view

        if view.error:
            self._error_label.set_text(view.error)
            self._error_label.set_visible(True)
            self._rows_box.set_visible(False)
            self._all_button.set_sensitive(False)
            self._all_button.set_label("Actualizar todo")
            self._all_button.set_tooltip_text(view.error)
            _set_why(self._all_why, "Sin lista de actualizaciones: mirá el aviso de arriba.")
            return

        self._error_label.set_visible(False)
        self._rows_box.set_visible(True)

        incoming = [row.id for row in view.rows]
        for row_id in tuple(self._rows):
            if row_id not in incoming:
                row = self._rows.pop(row_id)
                self._rows_box.remove(row["widget"])

        for row in view.rows:
            widgets = self._rows.get(row.id)
            if widgets is None:
                widgets = self._build_row(row)
                self._rows[row.id] = widgets
                self._rows_box.append(widgets["widget"])
            self._update_row(widgets, row)

        self._all_button.set_label(
            f"Actualizar todo ({view.count})" if view.count else "Actualizar todo"
        )
        self._all_button.set_sensitive(bool(view.count) and not self._busy)
        if self._busy:
            all_reason = "Hay una actualización en curso; esperá a que termine."
        elif not view.count:
            all_reason = "Nada para actualizar: todo está al día o sin canal."
        else:
            all_reason = ""
        _set_why(self._all_why, all_reason)
        self._all_button.set_tooltip_text(
            f"Actualiza los {view.count} componentes con versión nueva, "
            "uno detrás de otro."
            if view.count
            else "Nada para actualizar ahora."
        )

    def _build_row(self, row: UpdateRow) -> dict:
        # Fila = línea (icono, nombres, eta, píldora, botón) + el progreso de
        # su propia actualización debajo (S14: la barra vive en la fila que la
        # empezó).
        widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        widget.append(line)

        icon = Gtk.Image.new_from_icon_name(_KIND_ICONS.get(row.kind, _FALLBACK_ICON))
        icon.set_valign(Gtk.Align.CENTER)
        line.append(icon)

        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = Gtk.Label(label=row.name, xalign=0)
        name.add_css_class("row-name")
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        names.append(name)
        installed = Gtk.Label(xalign=0)
        installed.add_css_class("caption")
        installed.add_css_class("muted")
        installed.set_ellipsize(3)
        names.append(installed)
        names.set_hexpand(True)
        line.append(names)

        eta = Gtk.Label(xalign=0)
        eta.add_css_class("caption")
        eta.add_css_class("muted")
        eta.set_valign(Gtk.Align.CENTER)
        line.append(eta)

        pill = Gtk.Label(xalign=0)
        pill.add_css_class("vpill")
        pill.set_valign(Gtk.Align.CENTER)
        line.append(pill)

        button = Gtk.Button(label="Actualizar")
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.END)
        button.connect("clicked", self._on_row_clicked, row.id)
        line.append(button)

        # S16: por qué «Actualizar» está apagado, debajo de la fila.
        why = _why_label()
        why.set_margin_start(26)
        widget.append(why)

        progress = progreso.ProgressInline(f"update:{row.id}")
        widget.append(progress)

        return {
            "widget": widget,
            "why": why,
            "progress": progress,
            "name": name,
            "installed": installed,
            "eta": eta,
            "pill": pill,
            "button": button,
        }

    def _update_row(self, widgets: dict, row: UpdateRow) -> None:
        installed = widgets["installed"]
        installed.set_text(f"instalada {row.installed}")
        # L21.4: «—» significa que el CLI no pudo leer la versión instalada.
        installed.set_tooltip_text("sin dato" if row.installed == "—" else "")
        widgets["eta"].set_text(row.eta_text)
        widgets["eta"].set_tooltip_text("Tiempo estimado que informa el administrador.")
        pill = widgets["pill"]
        pill.set_text(row.pill)
        pill.set_tooltip_text(row.pill_tooltip)
        for key in _PILL_CLASSES.values():
            pill.remove_css_class(key)
        if row.pill_class in _PILL_CLASSES:
            pill.add_css_class(_PILL_CLASSES[row.pill_class])

        button = widgets["button"]
        running = progreso.HUB.running(f"update:{row.id}")
        button.set_label("Actualizando…" if running else "Actualizar")
        enabled = row.button_enabled and not self._busy and not running
        button.set_sensitive(enabled)
        if enabled or running:
            reason = ""  # en curso: lo explica su bloque de progreso
        elif not row.button_enabled:
            reason = disabled_reason(row)
        else:
            reason = "Hay otra actualización en curso; esperá a que termine."
        _set_why(widgets["why"], reason)
        button.set_tooltip_text(row.apply or row.reason or "Actualiza este componente.")
        # La frase completa (qué hace la actualización) vive en el tooltip (S12).
        widgets["widget"].set_tooltip_text(row.apply or row.reason or "")

    # -- acciones ----------------------------------------------------------

    def _on_row_clicked(self, _button: Gtk.Button, row_id: str) -> None:
        row = next((r for r in self._view.rows if r.id == row_id), None)
        if row is None or self._busy or not row.updatable:
            return
        self._confirm([(row, False)])

    def _on_update_all_clicked(self, _button: Gtk.Button) -> None:
        rows = self._view.updatable_rows
        if not rows or self._busy:
            return
        self._confirm([(row, False) for row in rows])

    def _confirm(self, plan: list[tuple[UpdateRow, bool]]) -> None:
        """Primera confirmación: eta y si el motor se reinicia (como engine.py)."""
        single = len(plan) == 1
        row = plan[0][0]
        if single:
            title, body = confirm_text(row)
        else:
            title = f"¿Actualizar todo ({len(plan)})?"
            detail = "; ".join(f"{r.name} ({r.eta_text})" for r, _b in plan)
            body = (
                "Se actualizan en orden, uno detrás de otro: "
                f"{detail}. Los que reinician un motor cortan las "
                "peticiones en curso."
            )

        dialog = self._dialog(title, body)
        dialog.connect("response", self._on_confirmed, plan)
        dialog.present()

    def _on_confirmed(self, dialog: Gtk.MessageDialog, response: int, plan) -> None:
        dialog.close()
        if response != Gtk.ResponseType.ACCEPT:
            return
        build_rows = [row for row, with_build in plan if row.needs_build]
        if build_rows:
            self._confirm_build(plan)
        else:
            self._begin(plan)

    def _confirm_build(self, plan: list[tuple[UpdateRow, bool]]) -> None:
        """Segunda confirmación explícita: compilar llama.cpp (L8/L12)."""
        dialog = self._dialog(BUILD_CONFIRM_TITLE, BUILD_CONFIRM_TEXT)
        dialog.connect("response", self._on_build_confirmed, plan)
        dialog.present()

    def _on_build_confirmed(
        self, dialog: Gtk.MessageDialog, response: int, plan
    ) -> None:
        dialog.close()
        if response != Gtk.ResponseType.ACCEPT:
            return
        self._begin(
            [(row, True if row.needs_build else with_build) for row, with_build in plan]
        )

    def _begin(self, plan: list[tuple[UpdateRow, bool]]) -> None:
        self._busy = True
        self._spinner.start()
        self._spinner.set_visible(True)
        self._all_button.set_sensitive(False)
        self._note.set_text("Actualizando…")
        self._note.set_visible(True)
        self._render()

        # S14: una actividad por componente; cada una entra al hub cuando le
        # toca (el «Actualizar todo» corre uno detrás de otro).
        activities = [
            (row, with_build, self._activity_for(row, with_build))
            for row, with_build in plan
        ]

        def _worker() -> None:
            messages = []
            for row, with_build, activity in activities:
                progreso.post(progreso.HUB.begin, activity)
                build = pl.BuildProgress() if with_build else None

                def _on_line(line: str, activity=activity, build=build) -> None:
                    if build is not None:
                        progreso.post(_feed_build, activity, build, line)

                result = stream_manager(
                    run_manager,
                    update_args(row, with_build),
                    UPDATE_TIMEOUT_S,
                    _on_line,
                )
                outcome = parse_update_result(_as_cli(result))
                messages.append(f"{row.name}: {outcome.message}")
                progreso.post(_finish_update, activity, row, outcome, result)
            listing = run_manager(("updates", "--json"))
            view = parse_updates(_as_cli(listing))
            GLib.idle_add(self._on_done, "\n".join(messages), view)

        threading.Thread(target=_worker, daemon=True).start()

    def _activity_for(self, row: UpdateRow, with_build: bool) -> pl.Activity:
        widgets = self._rows.get(row.id)
        return pl.update_action(
            row.id,
            _short_name(row.name),
            row.eta_s,
            with_build=with_build,
            section="motores",
            anchor=widgets["progress"] if widgets else None,
            retry=lambda row_id=row.id: self._retry(row_id),
        )

    def _retry(self, row_id: str) -> None:
        """«Reintentar»: vuelve a pedir confirmación, nunca corre a ciegas."""
        row = next((r for r in self._view.rows if r.id == row_id), None)
        if row is None or self._busy or not row.updatable:
            return
        self._confirm([(row, False)])

    def _on_done(self, message: str, view: UpdatesView) -> bool:
        self._busy = False
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._note.set_text(message)
        self._note.set_visible(True)
        self._view = view
        self._render()
        return False

    # -- dialogo -----------------------------------------------------------

    def _dialog(self, title: str, body: str) -> Gtk.MessageDialog:
        """Mismo patrón de diálogo que el motor residente (blocks/engine.py)."""
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
            # GTK 4: el cuerpo es la propiedad «secondary-text»;
            # format_secondary_text() era API de GTK 3 y aquí no existe.
            secondary_text=body,
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Actualizar", Gtk.ResponseType.ACCEPT)
        return dialog


def _why_label() -> Gtk.Label:
    label = Gtk.Label(xalign=0)
    label.add_css_class("why")
    label.set_wrap(True)
    label.set_max_width_chars(60)
    label.set_visible(False)
    return label


def _set_why(label: Gtk.Label, text: str) -> None:
    label.set_text(text)
    label.set_visible(bool(text))


def _short_name(name: str) -> str:
    """«Motor Abito (llama.cpp-newmodels)» → «Motor Abito» para la barra."""
    return name.split(" (", 1)[0]


def _feed_build(activity: pl.Activity, build: pl.BuildProgress, line: str) -> None:
    """Hilo de GTK: una línea de la compilación → etapa y % reales."""
    if build.feed(line):
        activity.set_stage(build.stage)
        activity.set_percent(build.percent)


def _finish_update(activity: pl.Activity, row: UpdateRow, outcome, result) -> None:
    name = _short_name(row.name)
    if outcome.kind in ("applied", "noop"):
        activity.succeed(f"{name}: {outcome.message}")
        return
    headline = {
        "rollback": f"No se pudo actualizar {name} · revertida",
        "refused": f"No se actualizó {name}",
        "build_required": f"{name}: falta confirmar la compilación",
    }.get(outcome.kind, f"No se pudo actualizar {name}")
    activity.fail(
        headline,
        outcome.message,
        log=(result.stderr or result.stdout or "").strip(),
    )


def _as_cli(result) -> CliResult:
    """Adapta la salida común del administrador a la lógica pura."""
    return CliResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        failure=result.failure,
    )
