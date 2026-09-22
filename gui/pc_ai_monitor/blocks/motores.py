"""Bloques del panel Motores para administrar los modelos locales.

El inventario y todas las acciones vienen de ``local-ai-models``. Ningún
subproceso se ejecuta en el hilo principal de GTK.
"""

# pyright: reportAttributeAccessIssue=false

import json
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot

MANAGER = "local-ai-models"
COMMAND_TIMEOUT_S = 300
REFRESH_INTERVAL_S = 5.0

PROBLEM_MESSAGES = {
    "missing_model_file": "falta el archivo del modelo",
    "missing_port": "falta configurar el puerto",
    "missing_model_arg": "falta configurar el argumento del modelo",
}


@dataclass(frozen=True)
class CommandResult:
    """Resultado de una invocación al administrador."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    failure: str | None = None


def _run_manager(*args: str) -> CommandResult:
    """Ejecuta el administrador; esta función solo se usa desde workers."""
    try:
        completed = subprocess.run(
            [MANAGER, *args],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(None, failure="missing")
    except subprocess.TimeoutExpired:
        return CommandResult(None, failure="timeout")
    except OSError as exc:
        return CommandResult(None, stderr=str(exc), failure="oserror")

    return CommandResult(
        completed.returncode,
        completed.stdout.strip(),
        completed.stderr.strip(),
    )


def _command_failure(result: CommandResult, action: str, model: str) -> str:
    """Traduce cada salida contractual a un mensaje accionable."""
    if result.failure == "missing":
        return "No se encontró local-ai-models. Revisá la instalación del administrador."
    if result.failure == "timeout":
        return f"{model}: la operación agotó el tiempo de espera; se volvió a leer el estado."
    if result.failure:
        return f"{model}: no se pudo ejecutar local-ai-models ({result.stderr})."
    if result.returncode == 1:
        return "Otra carga está en curso, probá de nuevo."
    if result.returncode == 2:
        return (
            f"{model}: el administrador no reconoce este modelo. "
            "Es un problema de programación del monitor."
        )
    if result.returncode == 3:
        return "Cambiá a hybrid en Pi antes de pararlo."
    if result.returncode == 10:
        verb = "encender" if action == "ensure" else "apagar"
        return (
            f"No se puede {verb} {model}: no alcanza la memoria para conservar "
            "20 GiB o el otro modelo grande está encendido."
        )

    detail = result.stderr or result.stdout or "sin detalle"
    return (
        f"{model}: local-ai-models terminó con código {result.returncode}: "
        f"{detail}"
    )


def _parse_capacity(stdout: str) -> dict[str, str]:
    """Convierte la salida key=value de capacity en un diccionario."""
    values: dict[str, str] = {}
    for token in stdout.replace("\n", " ").split():
        if "=" in token:
            key, value = token.split("=", 1)
            values[key] = value
    return values


def _load_inventory() -> tuple[list[dict] | None, str | None]:
    """Lee estado y capacidad. Debe llamarse exclusivamente en un worker."""
    status = _run_manager("status", "--json")
    if status.returncode != 0:
        return None, _command_failure(status, "status", "Administrador")

    try:
        models = json.loads(status.stdout)
    except (TypeError, ValueError):
        return None, "local-ai-models devolvió un estado JSON inválido."

    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        return None, "local-ai-models devolvió un inventario inválido."

    inventory: list[dict] = []
    for raw_model in models:
        model = dict(raw_model)
        model_name = model.get("model")
        if not isinstance(model_name, str) or not model_name:
            return None, "local-ai-models devolvió un modelo sin identificador."

        model["capacity_route"] = None
        model["capacity_reason"] = None
        if model.get("required_kib") is not None and not model.get("problem"):
            capacity = _run_manager("capacity", model_name)
            values = _parse_capacity(capacity.stdout)
            route = values.get("route")
            # ``capacity`` informa route=cloud con exit 10: es una decisión
            # humana normal, no un fallo del administrador.
            if route in {"local", "cloud"}:
                model["capacity_route"] = route
                if route == "cloud":
                    model["capacity_reason"] = (
                        "no hay margen para conservar 20 GiB"
                    )
            elif capacity.returncode != 0:
                model["capacity_reason"] = _command_failure(
                    capacity, "ensure", model_name
                )
            else:
                model["capacity_reason"] = (
                    "no se pudo confirmar el margen de memoria"
                )
        inventory.append(model)

    return inventory, None


class EngineControlBlock(Gtk.Box):
    """Tarjeta de un modelo informado por el administrador."""

    def __init__(
        self,
        model: dict,
        action_callback: Callable[[str, str], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("card")
        self._action_callback = action_callback
        self._model: dict = {}
        self._running_action: str | None = None

        self._name_label = Gtk.Label(xalign=0)
        self._name_label.add_css_class("section-head")
        self.append(self._name_label)

        self._state_label = Gtk.Label(xalign=0)
        self._state_label.add_css_class("heading")
        self.append(self._state_label)

        self._info_label = Gtk.Label(xalign=0)
        self._info_label.add_css_class("caption")
        self._info_label.set_wrap(True)
        self._info_label.set_max_width_chars(55)
        self.append(self._info_label)

        self._reason_label = Gtk.Label(xalign=0)
        self._reason_label.add_css_class("muted")
        self._reason_label.set_wrap(True)
        self._reason_label.set_visible(False)
        self.append(self._reason_label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._start_button = Gtk.Button(label="Encender")
        self._start_button.add_css_class("suggested-action")
        self._start_button.connect("clicked", self._on_start)
        buttons.append(self._start_button)

        self._stop_button = Gtk.Button(label="Apagar")
        self._stop_button.add_css_class("destructive-action")
        self._stop_button.connect("clicked", self._on_stop)
        buttons.append(self._stop_button)
        self.append(buttons)

        self._notification = Gtk.Label(xalign=0)
        self._notification.add_css_class("muted")
        self._notification.set_wrap(True)
        self._notification.set_visible(False)
        self.append(self._notification)

        self.update_model(model)

    @property
    def model_name(self) -> str:
        return str(self._model.get("model", ""))

    def update_model(self, model: dict) -> None:
        """Actualiza datos reales sin pisar el indicador de acción en curso."""
        self._model = model
        self._render()

    def set_manager_unavailable(self) -> None:
        """Evita acciones sobre datos viejos si no responde el administrador."""
        if self._running_action is None:
            self._state_label.set_text("estado no disponible")
            self._state_label.remove_css_class("success")
        self._start_button.set_sensitive(False)
        self._stop_button.set_sensitive(False)

    def begin_action(self, action: str) -> None:
        self._running_action = action
        self._render()

    def finish_action(self, message: str) -> None:
        self._running_action = None
        self._notification.set_text(message)
        self._notification.set_visible(True)
        self._render()

    def _render(self) -> None:
        model_name = self.model_name
        alias = self._model.get("alias") or "sin alias"
        port = self._model.get("port")
        context = self._model.get("ctx")
        problem = self._model.get("problem")
        healthy = bool(self._model.get("healthy", False))
        capacity_reason = self._model.get("capacity_reason")

        self._name_label.set_text(model_name)
        self._info_label.set_text(
            f"alias: {alias} · puerto: {port} · contexto: {context}"
        )

        reason = ""
        if self._running_action is not None:
            state = "cargando…"
        elif problem:
            state = "no utilizable"
            reason = PROBLEM_MESSAGES.get(str(problem), f"problema: {problem}")
        elif healthy:
            state = "encendido"
        else:
            state = "detenido"
            if capacity_reason:
                reason = str(capacity_reason)

        self._state_label.set_text(state)
        if healthy and self._running_action is None and not problem:
            self._state_label.add_css_class("success")
        else:
            self._state_label.remove_css_class("success")

        self._reason_label.set_text(reason)
        self._reason_label.set_visible(bool(reason))

        action_running = self._running_action is not None
        can_start = (
            not action_running
            and not healthy
            and not problem
            and not capacity_reason
        )
        self._start_button.set_sensitive(can_start)
        self._stop_button.set_sensitive(not action_running and healthy)

    def _on_start(self, _button: Gtk.Button) -> None:
        self._action_callback(self.model_name, "ensure")

    def _on_stop(self, _button: Gtk.Button) -> None:
        if self.model_name == "abito":
            self._confirm_resident_stop()
        else:
            self._action_callback(self.model_name, "stop")

    def _confirm_resident_stop(self) -> None:
        """Pide confirmación solamente para el motor residente."""
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"¿Apagar {self.model_name}?",
        )
        dialog.format_secondary_text(
            "Es el motor base usado por Pi y el router. Confirmá para detenerlo."
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apagar", Gtk.ResponseType.ACCEPT)

        def _on_response(current: Gtk.MessageDialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT:
                self._action_callback(self.model_name, "stop")

        dialog.connect("response", _on_response)
        dialog.present()


class ModelsInventoryBlock(Block):
    """Inventario dinámico y único punto de control de los modelos."""

    title = "MOTORES LOCALES"
    frame = False

    def __init__(self) -> None:
        super().__init__()
        self._cards: dict[str, EngineControlBlock] = {}
        self._refreshing = False
        self._last_refresh = 0.0

        self._manager_message = Gtk.Label(xalign=0)
        self._manager_message.add_css_class("error")
        self._manager_message.set_wrap(True)
        self._manager_message.set_visible(False)
        self.body.append(self._manager_message)

        self._cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.body.append(self._cards_box)
        self._refresh(force=True)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        """Aprovecha el pulso existente para descubrir cambios de inventario."""
        self._refresh(force=False)

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing:
            return
        if not force and now - self._last_refresh < REFRESH_INTERVAL_S:
            return

        self._refreshing = True
        self._last_refresh = now

        def _worker() -> None:
            inventory, error = _load_inventory()
            GLib.idle_add(self._on_inventory_loaded, inventory, error)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_inventory_loaded(
        self, inventory: list[dict] | None, error: str | None
    ) -> bool:
        self._refreshing = False
        if error is not None or inventory is None:
            self._manager_message.set_text(error or "No se pudo leer el inventario.")
            self._manager_message.set_visible(True)
            for card in self._cards.values():
                card.set_manager_unavailable()
            return False

        self._manager_message.set_visible(False)
        incoming_names: set[str] = set()
        for model in inventory:
            name = str(model["model"])
            incoming_names.add(name)
            card = self._cards.get(name)
            if card is None:
                card = EngineControlBlock(model, self._request_action)
                self._cards[name] = card
                self._cards_box.append(card)
            else:
                card.update_model(model)

        for name in tuple(self._cards):
            if name not in incoming_names:
                card = self._cards.pop(name)
                self._cards_box.remove(card)
        return False

    def _request_action(self, model: str, action: str) -> None:
        card = self._cards.get(model)
        if card is None:
            return
        card.begin_action(action)

        def _worker() -> None:
            result = _run_manager(action, model)
            inventory, inventory_error = _load_inventory()
            GLib.idle_add(
                self._on_action_done,
                model,
                action,
                result,
                inventory,
                inventory_error,
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_action_done(
        self,
        model: str,
        action: str,
        result: CommandResult,
        inventory: list[dict] | None,
        inventory_error: str | None,
    ) -> bool:
        if inventory is not None:
            self._on_inventory_loaded(inventory, None)
        elif inventory_error:
            self._manager_message.set_text(inventory_error)
            self._manager_message.set_visible(True)

        card = self._cards.get(model)
        if card is None:
            return False

        if result.returncode != 0:
            message = _command_failure(result, action, model)
        elif inventory is None:
            message = (
                f"{model}: la orden terminó, pero no se pudo verificar el estado real."
            )
        else:
            actual = next(
                (item for item in inventory if item.get("model") == model), None
            )
            healthy = bool(actual and actual.get("healthy", False))
            if action == "ensure" and healthy:
                message = f"{model}: encendido y saludable."
            elif action == "ensure":
                message = (
                    f"{model}: la orden terminó, pero el modelo no está saludable."
                )
            elif action == "stop" and not healthy:
                message = f"{model}: apagado."
            else:
                message = f"{model}: la orden terminó, pero todavía está saludable."

        card.finish_action(message)
        return False


class MemoriaBlock(Block):
    """Muestra la memoria disponible que ya trae el snapshot."""

    title = "MEMORIA DISPONIBLE"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._mem_label = Gtk.Label(label="", xalign=0)
        self._mem_label.add_css_class("heading")
        self.body.append(self._mem_label)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        system = router.get("system", {})
        mem_gib = system.get("mem_available_gib", 0.0)

        if mem_gib < 1.0:
            self._mem_label.set_text(f"⚠️  {mem_gib:.1f} GiB disponibles")
            self._mem_label.add_css_class("error")
        elif mem_gib < 4.0:
            self._mem_label.set_text(f"⚠️  {mem_gib:.1f} GiB disponibles")
            self._mem_label.remove_css_class("error")
        else:
            self._mem_label.set_text(f"{mem_gib:.1f} GiB disponibles")
            self._mem_label.remove_css_class("error")


class MotoresBlock(ModelsInventoryBlock):
    """Aloja el inventario dinámico completo que informa ``local-ai-models``."""
