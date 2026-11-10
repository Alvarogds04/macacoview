# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Cada confirmación de los botones de motores, con los diálogos REALES.

Defecto vivo (journal del 2026-11-06): todo botón que pide confirmación
lanzaba ``AttributeError: 'MessageDialog' object has no attribute
'format_secondary_text'`` — API de GTK 3 que GTK 4 no tiene —, así que el
diálogo nunca se abría y la orden nunca corría. Los tests anteriores
sustituían ``Gtk.MessageDialog`` por un doble que sí tenía ese método y
ocultaban el defecto.

Aquí cada camino va por el propio bloque (el clic del botón o su
``request_…``) contra ``Gtk.MessageDialog`` / ``Adw.MessageDialog`` de
verdad: se comprueba título, cuerpo y botones de respuesta; ACEPTAR lanza la
orden (que ``_aislamiento`` registra sin tocar la máquina) y CANCELAR no
lanza nada. Necesita display (aunque sea virtual):

    xvfb-run -a python3 -m unittest tests.test_confirmaciones -v
"""

import json
import unittest
from unittest import mock

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

try:
    import _dialogos as dialogos
except ModuleNotFoundError:
    from tests import _dialogos as dialogos

from gi.repository import Adw, Gtk

from pc_ai_monitor.blocks.engine_logica import (
    CONFIRMATION_TEXT,
    CliResult,
    parse_engine_status,
)

_aislamiento.isolate()

RESIDENT_GUFO = {
    "resident": "gufo",
    "abito": {"healthy": False},
    "gufo": {"context": 131072},
}

STATUS_UPDATABLE = json.dumps(
    {
        "component": "engine-gufo",
        "running": {
            "tag": "gufo:v0.28.0-r2",
            "version_label": "0.28.0",
            "revision_label": "r2",
            "context": "131072",
            "port": "11070",
            "serving": True,
        },
        "available": {"tag": "gufo:v0.28.1-r1", "version_label": "0.28.1"},
        "updatable": True,
    }
)

ABITO_HEALTHY = {
    "model": "abito",
    "alias": "abito",
    "port": 11002,
    "ctx": 65536,
    "healthy": True,
    "problem": None,
    "capacity_reason": None,
}

STOP_ABITO = ("stop", "abito")
STOP_BODY = "Es el motor base usado por Pi y el router. Confirmá para detenerlo."
ENGINE_UPDATE = ("engine", "update", "--yes", "--json")


class FakePoller:
    """Poller de la Home sin subprocesos: estado fijo que el test rellena."""

    def __init__(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory
        from pc_ai_monitor.blocks.home_logica import HomeState

        self.state = HomeState()
        self.history = MotorHistory()
        self.subscribers = []
        self.update_begun = False
        self.switch_begun = []

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def refresh(self, _snapshot, force=False):
        del force

    def begin_update(self):
        self.update_begun = True
        self.state.updating = True

    def end_update(self, returncode, message, status=None):
        self.state.updating = False
        self.state.last_update_rc = returncode
        self.state.update_message = message
        if status is not None:
            self.state.status = status

    def begin_switch(self, target):
        self.switch_begun.append(target)
        self.state.switching = target

    def end_switch(self, ok, message, info=None):
        del ok, message
        self.state.switching = None
        if info is not None:
            self.state.resident_info = info


class _DialogCase(unittest.TestCase):
    """Base: display real y diálogos reales; nunca un doble del diálogo."""

    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")
        Adw.init()

    def open_one(self, action):
        """Ejecuta el clic y devuelve el único diálogo real que presentó."""
        dialogs = dialogos.presented_dialogs(action)
        for dialog in dialogs:
            self.addCleanup(dialogos.close, dialog)
        self.assertEqual(len(dialogs), 1, "el botón no abrió su confirmación")
        return dialogs[0]

    def assert_gtk_dialog(self, dialog, title, body, accept_label):
        self.assertIsInstance(dialog, Gtk.MessageDialog)
        self.assertEqual(dialog.get_property("text"), title)
        self.assertEqual(dialog.get_property("secondary-text"), body)
        self.assertEqual(
            dialogos.button_label(dialog, Gtk.ResponseType.CANCEL), "Cancelar"
        )
        self.assertEqual(
            dialogos.button_label(dialog, Gtk.ResponseType.ACCEPT), accept_label
        )

    def assert_switch_dialog(self, dialog, target):
        from pc_ai_monitor.blocks.residente_logica import confirm_text

        title, body = confirm_text(target)
        self.assertIsInstance(dialog, Adw.MessageDialog)
        self.assertEqual(dialog.get_heading(), title)
        self.assertEqual(dialog.get_body(), body)
        self.assertEqual(dialog.get_response_label("cancel"), "Cancelar")
        self.assertEqual(dialog.get_response_label("switch"), "Cambiar")
        self.assertEqual(dialog.get_close_response(), "cancel")

    def assert_ran(self, start, command):
        self.assertTrue(
            dialogos.wait_for_command(start, command),
            f"ACEPTAR no lanzó {command}: {dialogos.commands_since(start)}",
        )

    def assert_nothing_ran(self, start, command):
        dialogos.pump(0.3)  # margen para un worker que no debería existir
        self.assertNotIn(command, dialogos.commands_since(start))


# ---------------------------------------------------------------------------
# Motores (motores.py): «Apagar» Abito
# ---------------------------------------------------------------------------


class MotoresStopAbitoTests(_DialogCase):
    def _card(self):
        from pc_ai_monitor.blocks.motores import ModelsInventoryBlock

        block = ModelsInventoryBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        block._on_inventory_loaded([dict(ABITO_HEALTHY)], None)
        return block, block._cards["abito"]

    def test_stop_abito_opens_the_real_dialog_and_accept_stops(self):
        _block, card = self._card()
        dialog = self.open_one(lambda: card._stop_button.emit("clicked"))
        self.assert_gtk_dialog(dialog, "¿Apagar abito?", STOP_BODY, "Apagar")

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assert_ran(start, STOP_ABITO)

    def test_cancel_stops_nothing(self):
        _block, card = self._card()
        dialog = self.open_one(lambda: card._stop_button.emit("clicked"))

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assert_nothing_ran(start, STOP_ABITO)
        self.assertIsNone(card._running_action)


# ---------------------------------------------------------------------------
# Actualizaciones (actualizaciones.py): la fila y la segunda confirmación
# ---------------------------------------------------------------------------


class ActualizacionesConfirmTests(_DialogCase):
    def _block(self):
        from pathlib import Path

        from pc_ai_monitor.blocks.actualizaciones import ActualizacionesBlock
        from pc_ai_monitor.blocks.actualizaciones_logica import (
            CliResult as UpdatesCli,
        )

        block = ActualizacionesBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        fixture = Path(__file__).parent / "fixtures" / "updates_real_2026-11-04.json"
        block._on_list_loaded(UpdatesCli(0, stdout=fixture.read_text()))
        return block

    def test_row_confirmation_then_build_confirmation_then_update(self):
        from pc_ai_monitor.blocks.actualizaciones_logica import (
            BUILD_CONFIRM_TEXT,
            BUILD_CONFIRM_TITLE,
            confirm_text,
        )

        block = self._block()
        row = next(r for r in block._view.rows if r.id == "engine-abito")
        title, body = confirm_text(row)

        first = self.open_one(
            lambda: block._rows["engine-abito"]["button"].emit("clicked")
        )
        self.assert_gtk_dialog(first, title, body, "Actualizar")

        second = self.open_one(lambda: first.response(Gtk.ResponseType.ACCEPT))
        self.assert_gtk_dialog(
            second, BUILD_CONFIRM_TITLE, BUILD_CONFIRM_TEXT, "Actualizar"
        )

        start = dialogos.mark()
        second.response(Gtk.ResponseType.ACCEPT)
        self.assert_ran(start, ("update", "engine-abito", "--build", "--yes", "--json"))
        dialogos.pump_until(lambda: not block._busy)

    def test_update_all_opens_the_real_dialog(self):
        from pc_ai_monitor.blocks.actualizaciones_logica import confirm_text

        block = self._block()
        # El fixture medido tiene un solo actualizable: «Actualizar todo (1)»
        # confirma con el texto de esa fila.
        row = next(r for r in block._view.rows if r.id == "engine-abito")
        title, body = confirm_text(row)
        dialog = self.open_one(lambda: block._all_button.emit("clicked"))
        self.assert_gtk_dialog(dialog, title, body, "Actualizar")

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assert_nothing_ran(
            start, ("update", "engine-abito", "--build", "--yes", "--json")
        )
        self.assertFalse(block._busy)

    def test_cancel_on_the_row_runs_nothing(self):
        block = self._block()
        dialog = self.open_one(
            lambda: block._rows["engine-abito"]["button"].emit("clicked")
        )

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        dialogos.pump()
        self.assertEqual(
            [
                w
                for w in Gtk.Window.list_toplevels()
                if isinstance(w, Gtk.MessageDialog) and w.get_visible()
            ],
            [],
            "CANCELAR no debe abrir la segunda confirmación",
        )
        self.assertNotIn(
            "update", [args[0] for args in dialogos.commands_since(start) if args]
        )
        self.assertFalse(block._busy)


# ---------------------------------------------------------------------------
# Motor residente (engine.py): «Actualizar motor»
# ---------------------------------------------------------------------------


class EngineUpdateTests(_DialogCase):
    def _block(self):
        from pc_ai_monitor.blocks.engine import MotorResidenteBlock

        block = MotorResidenteBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        block._status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        block._render()
        return block

    def test_update_button_opens_the_real_dialog_and_accept_updates(self):
        block = self._block()
        dialog = self.open_one(lambda: block._update_button.emit("clicked"))
        self.assert_gtk_dialog(
            dialog, "¿Actualizar el motor?", CONFIRMATION_TEXT, "Actualizar"
        )

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assert_ran(start, ENGINE_UPDATE)
        dialogos.pump_until(lambda: not block._updating)

    def test_cancel_updates_nothing(self):
        block = self._block()
        dialog = self.open_one(lambda: block._update_button.emit("clicked"))

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assert_nothing_ran(start, ENGINE_UPDATE)
        self.assertFalse(block._updating)


# ---------------------------------------------------------------------------
# Home (home.py): apagar Abito desde el inventario y Ctrl U
# ---------------------------------------------------------------------------


class HomeInventoryStopTests(_DialogCase):
    def _row(self):
        from pc_ai_monitor.blocks.home import InventarioRowBlock

        poller = FakePoller()
        poller.state.inventory = [dict(ABITO_HEALTHY)]
        block = InventarioRowBlock(poller=poller)
        row = block._find_row("abito")
        assert row is not None, "la fila de abito no se construyó"
        return block, row

    def test_power_on_healthy_abito_opens_the_real_dialog_and_accept_stops(self):
        block, row = self._row()
        self.assertEqual(row["state"].get_text(), "encendido")
        dialog = self.open_one(lambda: row["power"].emit("clicked"))
        self.assert_gtk_dialog(dialog, "¿Apagar abito?", STOP_BODY, "Apagar")

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assert_ran(start, STOP_ABITO)
        dialogos.pump_until(lambda: "abito" not in block._running)

    def test_cancel_stops_nothing(self):
        block, row = self._row()
        dialog = self.open_one(lambda: row["power"].emit("clicked"))

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assert_nothing_ran(start, STOP_ABITO)
        self.assertNotIn("abito", block._running)


class HomeUpdateCardTests(_DialogCase):
    def _block(self):
        from pc_ai_monitor.blocks.home import UpdateCardBlock

        poller = FakePoller()
        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        poller.state.available_label = "0.28.1"
        return UpdateCardBlock(poller=poller), poller

    def test_update_button_opens_the_real_dialog_and_accept_updates(self):
        block, poller = self._block()
        dialog = self.open_one(lambda: block._button.emit("clicked"))
        self.assert_gtk_dialog(
            dialog, "¿Actualizar el motor?", CONFIRMATION_TEXT, "Actualizar"
        )

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assert_ran(start, ENGINE_UPDATE)
        self.assertTrue(poller.update_begun)
        dialogos.pump_until(lambda: not poller.state.updating)

    def test_cancel_updates_nothing(self):
        block, poller = self._block()
        dialog = self.open_one(lambda: block._button.emit("clicked"))

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assert_nothing_ran(start, ENGINE_UPDATE)
        self.assertFalse(poller.update_begun)


# ---------------------------------------------------------------------------
# Cambio de residente (Adw.MessageDialog): home, residente, motores_vista
# ---------------------------------------------------------------------------


class HomeSwitchTests(_DialogCase):
    def _block(self):
        from pc_ai_monitor.blocks.home import MotorRowBlock

        poller = FakePoller()
        poller.state.resident_info = dict(RESIDENT_GUFO)
        return MotorRowBlock(poller=poller), poller

    def test_switch_opens_the_real_dialog_and_accept_switches(self):
        block, poller = self._block()
        dialog = self.open_one(block.request_switch)
        self.assert_switch_dialog(dialog, "abito")

        start = dialogos.mark()
        dialog.response("switch")
        self.assert_ran(start, ("switch", "abito"))
        self.assertEqual(poller.switch_begun, ["abito"])
        dialogos.pump_until(lambda: poller.state.switching is None)

    def test_cancel_switches_nothing(self):
        block, poller = self._block()
        dialog = self.open_one(block.request_switch)

        start = dialogos.mark()
        dialog.response("cancel")
        self.assert_nothing_ran(start, ("switch", "abito"))
        self.assertEqual(poller.switch_begun, [])


class ResidenteSwitchTests(_DialogCase):
    def _block(self):
        from pc_ai_monitor.blocks.residente import ResidenteBlock

        block = ResidenteBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        block._info = dict(RESIDENT_GUFO)
        block._render()
        return block

    def test_switch_opens_the_real_dialog_and_accept_switches(self):
        block = self._block()
        dialog = self.open_one(lambda: block._options["abito"]._button.emit("clicked"))
        self.assert_switch_dialog(dialog, "abito")

        start = dialogos.mark()
        dialog.response("switch")
        self.assert_ran(start, ("switch", "abito"))
        dialogos.pump_until(lambda: block._switching is None)

    def test_cancel_switches_nothing(self):
        block = self._block()
        dialog = self.open_one(lambda: block._options["abito"]._button.emit("clicked"))

        start = dialogos.mark()
        dialog.response("cancel")
        self.assert_nothing_ran(start, ("switch", "abito"))
        self.assertIsNone(block._switching)


class MotoresVistaSwitchTests(_DialogCase):
    def _block(self):
        from pc_ai_monitor.blocks.motores_vista import ResidenteVistaBlock

        block = ResidenteVistaBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        block._info = dict(RESIDENT_GUFO)
        block._render()
        return block

    def test_switch_opens_the_real_dialog_and_accept_switches(self):
        block = self._block()
        dialog = self.open_one(lambda: block._cards["abito"]["button"].emit("clicked"))
        self.assert_switch_dialog(dialog, "abito")

        start = dialogos.mark()
        dialog.response("switch")
        self.assert_ran(start, ("switch", "abito"))
        dialogos.pump_until(lambda: block._switching is None)

    def test_cancel_switches_nothing(self):
        block = self._block()
        dialog = self.open_one(lambda: block._cards["abito"]["button"].emit("clicked"))

        start = dialogos.mark()
        dialog.response("cancel")
        self.assert_nothing_ran(start, ("switch", "abito"))
        self.assertIsNone(block._switching)


# ---------------------------------------------------------------------------
# T19 (S21): «Detener» la carga de Gufo desde Motores (motores_vista, residente)
# ---------------------------------------------------------------------------

RESIDENT_NONE = {"resident": "none", "abito": {"healthy": False}, "gufo": {}}


class _DetenerGufoCase(_DialogCase):
    """«Cambiar a Gufo» → «Detener» → gufo-flash stop --no-restore."""

    module_name = ""
    block_name = ""

    def setUp(self):
        from pc_ai_monitor.blocks import progreso

        self.progreso = progreso
        progreso.HUB.reset()
        self.addCleanup(progreso.HUB.reset)

    def _block(self, held):
        import importlib

        from pc_ai_monitor.blocks import motores

        module = importlib.import_module(self.module_name)
        for target, name, value in (
            (module, "run_manager", held.manager),
            (module, "_run_manager", held.manager_args),
            (motores, "run_gufo_flash", held.gufo_flash),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(held.finish)
        block = getattr(module, self.block_name)()
        dialogos.pump_until(lambda: not block._refreshing)
        block._info = dict(RESIDENT_NONE)
        block._render()
        block._start_switch("gufo")
        self.assertTrue(held.started.wait(5.0))
        dialogos.pump(0.05)
        return block

    def _stop_routes(self, start):
        return [a for a in _aislamiento.ATTEMPTS[start:] if a[1][:1] == ("stop",)]

    def test_detener_asks_then_stops_gufo_without_restoring_abito(self):
        from pc_ai_monitor.blocks import progreso_logica as pl

        held = _aislamiento.HeldLoad(("switch", "gufo"), resident=RESIDENT_NONE)
        block = self._block(held)
        title, body = pl.stop_confirm_text("gufo")
        dialog = self.open_one(lambda: block._progress._stop_button.emit("clicked"))
        self.assert_gtk_dialog(dialog, title, body, "Detener")

        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        activity = self.progreso.HUB.get("switch")
        self.assertTrue(dialogos.pump_until(lambda: not activity.running))
        self.assertTrue(held.stop_seen[0].requested)
        self.assertEqual(
            self._stop_routes(start), [("gufo-flash", ("stop", "--no-restore"))]
        )
        self.assertEqual(activity.progress().state, pl.STOPPED)
        self.assertIsNone(block._switching)
        self.assertEqual(block._info["resident"], "none")
        self.assertFalse(block._progress._stop_button.get_visible())

    def test_cancel_sends_nothing(self):
        held = _aislamiento.HeldLoad(("switch", "gufo"), resident=RESIDENT_NONE)
        block = self._block(held)
        dialog = self.open_one(lambda: block._progress._stop_button.emit("clicked"))
        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.CANCEL)
        dialogos.pump(0.3)
        self.assertEqual(_aislamiento.ATTEMPTS[start:], [])
        self.assertTrue(self.progreso.HUB.running("switch"))


class MotoresVistaDetenerTests(_DetenerGufoCase):
    module_name = "pc_ai_monitor.blocks.motores_vista"
    block_name = "ResidenteVistaBlock"


class ResidenteDetenerTests(_DetenerGufoCase):
    module_name = "pc_ai_monitor.blocks.residente"
    block_name = "ResidenteBlock"


del _DetenerGufoCase  # base: sólo corren sus subclases


if __name__ == "__main__":
    unittest.main()
