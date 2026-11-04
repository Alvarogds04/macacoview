"""Sección "Motor residente": estado del motor Gufo y actualización.

Peer del inventario de modelos en el panel Motores. Toda decisión de
renderizado y de mensajes vive en ``residente_logica`` (testeada sin GTK);
este widget solo la pinta. Toda acción pasa por ``local-ai-models``: ninguna
llamada corre en el hilo principal de GTK y el estado se relee después de
accionar, nunca se asume (MODEL_CONTROL_CONTRACT §6).
"""

# pyright: reportAttributeAccessIssue=false

import threading
import time

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.engine_logica import (
    CONFIRMATION_TEXT,
    CliResult,
    EngineStatus,
    admission_warning,
    display_rows,
    inventory_text,
    parse_engine_status,
    parse_update_result,
    rollback_hint,
    update_enabled,
)
from pc_ai_monitor.blocks.motores import CommandResult, run_manager
from pc_ai_monitor.datos import Snapshot

# El estado consulta el registro: no hace falta refrescarlo cada pulso.
STATUS_TIMEOUT_S = 120
# El update reinicia el motor (~85 s) y verifica /health y /v1/models.
UPDATE_TIMEOUT_S = 900
REFRESH_INTERVAL_S = 60.0


class MotorResidenteBlock(Block):
    """Tarjeta del motor residente: estado, disponibilidad y actualización."""

    title = "MOTOR RESIDENTE"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._status = EngineStatus()
        self._refreshing = False
        self._updating = False
        self._last_refresh = 0.0

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_max_width_chars(55)
        self._error_label.set_visible(False)
        self.body.append(self._error_label)

        self._rows_label = Gtk.Label(xalign=0)
        self._rows_label.add_css_class("caption")
        self._rows_label.set_wrap(True)
        self._rows_label.set_max_width_chars(55)
        self.body.append(self._rows_label)

        # Aviso de admisión: separado del motivo de la fila "última versión".
        self._admission_label = Gtk.Label(xalign=0)
        self._admission_label.add_css_class("error")
        self._admission_label.set_wrap(True)
        self._admission_label.set_max_width_chars(55)
        self._admission_label.set_visible(False)
        self.body.append(self._admission_label)

        self._reason_label = Gtk.Label(xalign=0)
        self._reason_label.add_css_class("muted")
        self._reason_label.set_wrap(True)
        self._reason_label.set_visible(False)
        self.body.append(self._reason_label)

        self._inventory_label = Gtk.Label(xalign=0)
        self._inventory_label.add_css_class("muted")
        self._inventory_label.set_wrap(True)
        self._inventory_label.set_max_width_chars(55)
        self._inventory_label.set_visible(False)
        self.body.append(self._inventory_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._update_button = Gtk.Button(label="Actualizar motor")
        self._update_button.add_css_class("suggested-action")
        self._update_button.connect("clicked", self._on_update_clicked)
        actions.append(self._update_button)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        actions.append(self._spinner)
        self.body.append(actions)

        self._notification_label = Gtk.Label(xalign=0)
        self._notification_label.add_css_class("muted")
        self._notification_label.set_wrap(True)
        self._notification_label.set_max_width_chars(55)
        self._notification_label.set_visible(False)
        self.body.append(self._notification_label)

        self._rollback_label = Gtk.Label(xalign=0)
        self._rollback_label.add_css_class("muted")
        self._rollback_label.set_wrap(True)
        self._rollback_label.set_max_width_chars(55)
        self._rollback_label.set_visible(False)
        self.body.append(self._rollback_label)

        self._refresh(force=True)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        """Aprovecha el pulso para releer el estado con moderación."""
        self._refresh(force=False)

    # -- lectura de estado -------------------------------------------------

    def _refresh(self, force: bool) -> None:
        if self._refreshing or self._updating:
            return
        now = time.monotonic()
        if not force and now - self._last_refresh < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_refresh = now

        def _worker() -> None:
            result = run_manager(("engine", "status", "--json"), STATUS_TIMEOUT_S)
            GLib.idle_add(self._on_status_loaded, result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_status_loaded(self, result: CommandResult) -> bool:
        self._refreshing = False
        self._status = parse_engine_status(_as_cli_result(result))
        self._render()
        return False

    # -- actualización del motor -------------------------------------------

    def _on_update_clicked(self, _button) -> None:
        if not update_enabled(self._status) or self._updating:
            return
        self._confirm_update()

    def _confirm_update(self) -> None:
        """Mismo patrón de diálogo que la parada del motor residente."""
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="¿Actualizar el motor?",
        )
        dialog.format_secondary_text(CONFIRMATION_TEXT)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Actualizar", Gtk.ResponseType.ACCEPT)

        def _on_response(current, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT:
                self._begin_update()

        dialog.connect("response", _on_response)
        dialog.present()

    def _begin_update(self) -> None:
        self._updating = True
        self._spinner.start()
        self._spinner.set_visible(True)
        self._update_button.set_sensitive(False)
        self._notification_label.set_text("Actualizando el motor…")
        self._notification_label.set_visible(True)

        def _worker() -> None:
            result = run_manager(
                ("engine", "update", "--yes", "--json"), UPDATE_TIMEOUT_S
            )
            # El contrato pide releer el estado real en vez de asumir.
            status = run_manager(
                ("engine", "status", "--json"), STATUS_TIMEOUT_S
            )
            GLib.idle_add(self._on_update_done, result, status)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_done(
        self, result: CommandResult, status: CommandResult
    ) -> bool:
        self._updating = False
        self._spinner.stop()
        self._spinner.set_visible(False)

        message = parse_update_result(_as_cli_result(result))
        self._notification_label.set_text(message)
        self._notification_label.set_visible(True)

        self._status = parse_engine_status(_as_cli_result(status))
        self._render()
        return False

    # -- pintado ------------------------------------------------------------

    def _render(self) -> None:
        status = self._status

        if status.error:
            self._error_label.set_text(status.error)
            self._error_label.set_visible(True)
            self._rows_label.set_text("")
            self._admission_label.set_visible(False)
            self._reason_label.set_visible(False)
            self._inventory_label.set_visible(False)
            self._rollback_label.set_visible(False)
            self._update_button.set_sensitive(False)
            return

        self._error_label.set_visible(False)
        self._rows_label.set_text(
            "\n".join(f"{label}: {value}" for label, value in display_rows(status))
        )

        warning = admission_warning(status.admission)
        self._admission_label.set_text(warning)
        self._admission_label.set_visible(bool(warning))

        reason = ""
        if self._updating:
            reason = "actualizando…"
        elif not status.updatable and status.reason:
            reason = f"Sin actualización: {status.reason}"
        self._reason_label.set_text(reason)
        self._reason_label.set_visible(bool(reason))

        listing = inventory_text(status)
        self._inventory_label.set_text(listing)
        self._inventory_label.set_visible(bool(listing))

        if status.tag:
            self._rollback_label.set_text(rollback_hint(status.tag))
            self._rollback_label.set_visible(True)
        else:
            self._rollback_label.set_visible(False)

        self._update_button.set_sensitive(
            update_enabled(status) and not self._updating
        )


def _as_cli_result(result: CommandResult) -> CliResult:
    """Adapta la salida común del administrador a la lógica pura."""
    return CliResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        failure=result.failure,
    )
