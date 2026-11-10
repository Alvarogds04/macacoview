# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests de «Actualizaciones del PC» (S6, contrato L8/L11/L12).

La lógica (``actualizaciones_logica``) se testa pura contra los fixtures
medidos (``tests/fixtures/updates_real_2026-11-04.json`` y
``update_results_real_2026-11-04.json``); el bloque GTK se testea con
``run_manager`` mockeado: nunca se llama al administrador de verdad.
"""

import json
import threading
import time
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks.actualizaciones_logica import (
    BUILD_CONFIRM_TEXT,
    CliResult,
    parse_update_result,
    parse_updates,
    update_args,
)

FIXTURES = Path(__file__).parent / "fixtures"


UPDATES_FIXTURE = (FIXTURES / "updates_real_2026-11-04.json").read_text()
RESULTS_FIXTURE = json.loads(
    (FIXTURES / "update_results_real_2026-11-04.json").read_text()
)

_aislamiento.isolate()


def fixture_result(stdout: str = UPDATES_FIXTURE) -> CliResult:
    return CliResult(0, stdout=stdout)


def row_by_id(view, row_id):
    return next(row for row in view.rows if row.id == row_id)


def live_updates_with_null_installed() -> str:
    """El fixture real de live_2026-11-05 con installed null en el motor
    Gufo (L21.4: visto en vivo cuando el CLI no pudo leer el estado)."""
    live = json.loads((FIXTURES / "live_2026-11-05/updates.json").read_text())
    assert live["components"][0]["id"] == "engine-gufo"
    live["components"][0]["installed"] = None
    return json.dumps(live)


# ---------------------------------------------------------------------------
# Listado: parseo del fixture medido (L11)
# ---------------------------------------------------------------------------


class UpdatesFixtureTests(unittest.TestCase):
    """Cada fila del fixture medido con su píldora, eta y botón."""

    @classmethod
    def setUpClass(cls):
        cls.view = parse_updates(fixture_result())

    def test_lists_the_six_components_of_the_contract(self):
        self.assertEqual(
            [row.id for row in self.view.rows],
            [
                "engine-gufo",
                "engine-abito",
                "controller-gufo",
                "router-pi",
                "weights-models",
                "cli-local-ai-models",
            ],
        )

    def test_no_error_and_human_names(self):
        self.assertEqual(self.view.error, "")
        self.assertEqual(
            row_by_id(self.view, "engine-gufo").name, "Motor Gufo (gufo-flash-next)"
        )

    def test_count_is_one_today(self):
        self.assertEqual(self.view.count, 1)
        self.assertEqual(self.view.updatable_rows[0].id, "engine-abito")

    def test_engine_gufo_is_up_to_date_with_measured_eta(self):
        row = row_by_id(self.view, "engine-gufo")
        self.assertEqual(row.pill, "Al día")
        self.assertEqual(row.eta_text, "≈ 85 s")
        self.assertFalse(row.button_enabled)
        self.assertTrue(row.restarts_engine)
        self.assertIn("0.28.1", row.installed)

    def test_engine_abito_is_updatable_with_tens_of_minutes(self):
        row = row_by_id(self.view, "engine-abito")
        self.assertEqual(row.pill, "Nueva 5851a8e5")
        self.assertEqual(row.eta_text, "decenas de minutos")
        self.assertTrue(row.button_enabled)
        self.assertTrue(row.restarts_engine)
        self.assertTrue(row.needs_build)

    def test_tools_are_up_to_date_with_their_etas(self):
        controller = row_by_id(self.view, "controller-gufo")
        self.assertEqual(controller.pill, "Al día")
        self.assertEqual(controller.eta_text, "≈ 1 s")
        self.assertFalse(controller.button_enabled)

        router = row_by_id(self.view, "router-pi")
        self.assertEqual(router.pill, "Al día")
        self.assertEqual(router.eta_text, "≈ 30 s")

    def test_null_available_means_no_channel(self):
        for row_id in ("weights-models", "cli-local-ai-models"):
            row = row_by_id(self.view, row_id)
            self.assertEqual(row.pill, "Sin canal", row_id)
            self.assertEqual(row.eta_text, "—", row_id)
            self.assertFalse(row.button_enabled, row_id)
            self.assertTrue(row.reason, row_id)  # el motivo vive en el tooltip
            self.assertIn(row.reason, row.pill_tooltip)

    def test_channel_failure_is_reported_not_silenced(self):
        """L8: canal inalcanzable → available null + reason con el fallo."""
        payload = json.dumps(
            {
                "checked_at": "2026-11-04T15:24:45Z",
                "components": [
                    {
                        "id": "cli-local-ai-models",
                        "kind": "tool",
                        "name": "CLI local-ai-models",
                        "installed": "1d267be0",
                        "available": None,
                        "updatable": False,
                        "reason": "no se pudo alcanzar el canal remoto (timeout)",
                        "restarts_engine": False,
                        "eta_s": None,
                        "apply": None,
                    }
                ],
            }
        )
        view = parse_updates(CliResult(0, stdout=payload))
        self.assertEqual(view.rows[0].pill, "No se pudo consultar")

    def test_tolerates_null_fields_on_an_updatable_row(self):
        """reason null cuando updatable, eta null, apply null no rompen."""
        payload = json.dumps(
            {
                "checked_at": None,
                "components": [
                    {
                        "id": "engine-x",
                        "kind": "engine",
                        "name": "Motor X",
                        "installed": "1.0",
                        "available": "2.0",
                        "updatable": True,
                        "reason": None,
                        "restarts_engine": True,
                        "eta_s": None,
                        "apply": None,
                    }
                ],
            }
        )
        view = parse_updates(CliResult(0, stdout=payload))
        row = view.rows[0]
        self.assertEqual(row.pill, "Nueva 2.0")
        self.assertEqual(row.eta_text, "decenas de minutos")
        self.assertTrue(row.button_enabled)

    def test_tolerates_an_unknown_kind(self):
        payload = json.dumps(
            {
                "components": [
                    {
                        "id": "raro",
                        "kind": "otro",
                        "name": "Raro",
                        "installed": "1",
                        "available": None,
                        "updatable": False,
                        "reason": "sin canal",
                        "restarts_engine": False,
                        "eta_s": None,
                        "apply": None,
                    }
                ],
            }
        )
        view = parse_updates(CliResult(0, stdout=payload))
        self.assertEqual(view.rows[0].kind, "otro")


class InstalledNullTests(unittest.TestCase):
    """L21.4: `installed: null` (el CLI no pudo leer el estado, visto en
    vivo) se muestra «instalada —» con tooltip «sin dato», nunca
    «instalada None». El resto del listado viene del fixture real de
    live_2026-11-05."""

    @classmethod
    def setUpClass(cls):
        cls.payload = live_updates_with_null_installed()
        cls.view = parse_updates(CliResult(0, stdout=cls.payload))

    def test_null_installed_becomes_a_dash(self):
        row = row_by_id(self.view, "engine-gufo")
        self.assertEqual(row.installed, "—")

    def test_other_rows_keep_their_installed_version(self):
        row = row_by_id(self.view, "engine-abito")
        self.assertEqual(row.installed, "9f31776c (2026-09-15)")


class UpdatesFailureTests(unittest.TestCase):
    """Comando ausente / salida≠0 / JSON inválido → «sin actualizador»."""

    def test_missing_command(self):
        view = parse_updates(CliResult(None, failure="missing"))
        self.assertTrue(view.error)
        self.assertIn("sin actualizador", view.error)
        self.assertIn("local-ai-models", view.error)
        self.assertEqual(view.rows, ())

    def test_nonzero_exit(self):
        view = parse_updates(CliResult(5, stderr="boom"))
        self.assertIn("sin actualizador", view.error)
        self.assertIn("5", view.error)

    def test_invalid_json(self):
        view = parse_updates(CliResult(0, stdout="no soy json"))
        self.assertIn("sin actualizador", view.error)


# ---------------------------------------------------------------------------
# Resultados de `update <id> --json` (L12)
# ---------------------------------------------------------------------------


class UpdateResultTests(unittest.TestCase):
    def _run(self, key):
        case = RESULTS_FIXTURE[key]
        return parse_update_result(
            CliResult(
                case["exit"],
                stdout=json.dumps(case["stdout"]),
                stderr=case.get("stderr", ""),
            )
        )

    def test_success_applied_is_verified(self):
        outcome = self._run("success_applied")
        self.assertEqual(outcome.kind, "applied")
        self.assertIn("verificado", outcome.message.lower())
        self.assertIn("d2309665", outcome.message)
        self.assertIn("1c78d6c1", outcome.message)

    def test_success_noop_reports_the_reason(self):
        outcome = self._run("success_noop")
        self.assertEqual(outcome.kind, "noop")
        self.assertIn("ya coincide", outcome.message)

    def test_refused_no_channel_weights(self):
        outcome = self._run("refused_no_channel_weights")
        self.assertEqual(outcome.kind, "refused")
        self.assertIn("no hay canal de actualización", outcome.message)

    def test_refused_no_channel_cli(self):
        outcome = self._run("refused_no_channel_cli")
        self.assertEqual(outcome.kind, "refused")
        self.assertIn("remoto git", outcome.message)

    def test_build_required_asks_for_the_second_confirmation(self):
        outcome = self._run("needs_confirmation_build")
        self.assertEqual(outcome.kind, "build_required")
        self.assertIn("compilar llama.cpp", outcome.message)

    def test_rollback_restores_the_previous_version(self):
        """L14: el fallo (exit 1) emite JSON; la rama es verified false /
        restored true, con el motivo del propio payload (fixture medido
        `rollback_isolated`, jamás con LOCAL_AI_FAULT desde la UI)."""
        outcome = self._run("rollback_isolated")
        self.assertEqual(outcome.kind, "rollback")
        self.assertIn("se restauró la versión anterior", outcome.message)
        self.assertIn("dd4fcec0", outcome.message)
        self.assertIn("690f8af1", outcome.message)
        self.assertIn("verificación posterior falló", outcome.message)

    def test_rollback_without_json_still_reports_the_restoration(self):
        """Tolerancia: un CLI viejo sin JSON en el fallo no rompe."""
        outcome = parse_update_result(CliResult(1, stderr="boom"))
        self.assertEqual(outcome.kind, "rollback")
        self.assertIn("se restauró la versión anterior", outcome.message)
        self.assertIn("boom", outcome.message)

    def test_missing_command(self):
        outcome = parse_update_result(CliResult(None, failure="missing"))
        self.assertEqual(outcome.kind, "error")
        self.assertIn("No se encontró local-ai-models", outcome.message)


class UpdatePlanTests(unittest.TestCase):
    """Orden de la orden y confirmaciones."""

    def _row(self, row_id):
        view = parse_updates(fixture_result())
        return row_by_id(view, row_id)

    def test_engine_abito_runs_with_build_and_yes(self):
        self.assertEqual(
            update_args(self._row("engine-abito"), with_build=True),
            ("update", "engine-abito", "--build", "--yes", "--json"),
        )

    def test_regular_rows_run_without_build(self):
        self.assertEqual(
            update_args(self._row("engine-gufo"), with_build=False),
            ("update", "engine-gufo", "--yes", "--json"),
        )

    def test_confirmation_states_eta_and_engine_restart(self):
        from pc_ai_monitor.blocks.actualizaciones_logica import confirm_text

        title, body = confirm_text(self._row("engine-gufo"))
        self.assertEqual(title, "¿Actualizar Motor Gufo (gufo-flash-next)?")
        self.assertIn("≈ 85 s", body)
        self.assertIn("se cortan", body)

    def test_build_confirmation_names_the_compile_and_restart(self):
        self.assertIn("Compilar llama.cpp", BUILD_CONFIRM_TEXT)
        self.assertIn("decenas de minutos", BUILD_CONFIRM_TEXT)
        self.assertIn("Abito", BUILD_CONFIRM_TEXT)


# ---------------------------------------------------------------------------
# Bloque GTK (xvfb): todo mockeado, sin administrador real
# ---------------------------------------------------------------------------


class FakeDialog:
    """Doble de Gtk.MessageDialog que registra texto y respuestas."""

    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.text = kwargs.get("text", "")
        # GTK 4: el cuerpo llega como propiedad «secondary-text». El doble no
        # ofrece format_secondary_text (GTK 3): si el bloque la llamara, el
        # test fallaría igual que la app real.
        self.secondary = kwargs.get("secondary_text", "")
        self._handler = None
        self._user_data = ()
        FakeDialog.instances.append(self)

    def add_button(self, _label, _response):
        pass

    def connect(self, _signal, handler, *user_data):
        self._handler = handler
        self._user_data = user_data

    def present(self):
        pass

    def close(self):
        pass

    def respond(self, response):
        if self._handler is not None:
            self._handler(self, response, *self._user_data)


class ActualizacionesWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            if not Gtk.init_check():
                raise unittest.SkipTest("sin display (usá xvfb-run)")
        except ImportError as error:  # pragma: no cover
            raise unittest.SkipTest(f"PyGObject no disponible: {error}") from error

    def setUp(self):
        FakeDialog.instances = []
        from pc_ai_monitor.blocks import actualizaciones

        self.mod = actualizaciones

    def _block(self, run_manager):
        # El patch de run_manager vive hasta el tearDown: el worker de la
        # acción corre después de construir el bloque y jamás debe alcanzar
        # el administrador real.
        patcher = mock.patch.object(self.mod, "run_manager", run_manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            block = self.mod.ActualizacionesBlock()
        # El primer listado llega desde el worker vía GLib.idle_add: hay que
        # bombear el contexto por defecto para que el callback corra.
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5.0
        while (
            not (block._view.rows or block._view.error) and time.monotonic() < deadline
        ):
            context.iteration(False)
            time.sleep(0.01)
        return block

    def _manager(self, update_results=None):
        calls = []
        done = threading.Event()

        def fake_run_manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            if tuple(args)[:1] == ("updates",):
                return CliResult(0, stdout=UPDATES_FIXTURE)
            for pattern, case in (update_results or {}).items():
                if pattern in args:
                    done.set()
                    return case
            done.set()
            return CliResult(0, stdout="{}")

        return fake_run_manager, calls, done

    def test_block_renders_the_fixture_rows(self):
        manager, _calls, _done = self._manager()
        block = self._block(manager)

        view = block._view
        self.assertEqual(view.count, 1)
        self.assertEqual(len(block._rows), 6)
        abito = block._rows["engine-abito"]
        self.assertEqual(abito["pill"].get_text(), "Nueva 5851a8e5")
        self.assertTrue(abito["button"].get_sensitive())
        gufo = block._rows["engine-gufo"]
        self.assertFalse(gufo["button"].get_sensitive())
        self.assertIn("85 s", gufo["eta"].get_text())

    def test_null_installed_renders_a_dash_with_sin_dato_tooltip(self):
        """L21.4: la fila con installed null pinta «instalada —» y tooltip
        «sin dato»; nunca «instalada None»."""
        block = self._block(
            lambda args, timeout=None: CliResult(
                0, stdout=live_updates_with_null_installed()
            )
        )
        row = block._rows["engine-gufo"]
        self.assertEqual(row["installed"].get_text(), "instalada —")
        self.assertEqual(row["installed"].get_tooltip_text(), "sin dato")
        abito = block._rows["engine-abito"]
        self.assertFalse(abito["installed"].get_tooltip_text())

    def test_the_gufo_row_uses_the_same_update_path(self):
        """La fila del motor Gufo pasa por `update engine-gufo` (no engine.py).

        Hoy el fixture lo tiene al día (botón apagado); el camino se prueba
        con un listado sintético donde Gufo sí tiene versión nueva.
        """
        synthetic = json.dumps(
            {
                "checked_at": "t",
                "components": [
                    {
                        "id": "engine-gufo",
                        "kind": "engine",
                        "name": "Motor Gufo (gufo-flash-next)",
                        "installed": "0.28.0",
                        "available": "0.28.1",
                        "updatable": True,
                        "reason": None,
                        "restarts_engine": True,
                        "eta_s": 85,
                        "apply": "reemplaza la imagen del motor",
                    }
                ],
            }
        )
        calls = []
        done = threading.Event()

        def manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            if tuple(args)[:1] == ("updates",):
                return CliResult(0, stdout=synthetic)
            done.set()
            return CliResult(
                0, stdout=json.dumps(RESULTS_FIXTURE["success_applied"]["stdout"])
            )

        block = self._block(manager)
        row = block._rows["engine-gufo"]

        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            row["button"].emit("clicked")
            self.assertEqual(len(FakeDialog.instances), 1)
            FakeDialog.instances[0].respond(Gtk.ResponseType.ACCEPT)

        self.assertTrue(done.wait(5.0), "el update no llegó a correr")
        # La nota llega vía GLib.idle_add: bombear el contexto hasta que caiga.
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5.0
        while block._busy and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)
        self.assertIn(("update", "engine-gufo", "--yes", "--json"), calls)
        self.assertIn("verificado", block._note.get_text())

    def test_build_required_row_asks_for_a_second_confirmation(self):
        manager, calls, done = self._manager(
            {
                "engine-abito": CliResult(
                    0, stdout=json.dumps(RESULTS_FIXTURE["success_applied"]["stdout"])
                ),
            }
        )
        block = self._block(manager)
        row = block._rows["engine-abito"]

        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            row["button"].emit("clicked")
            self.assertEqual(len(FakeDialog.instances), 1)
            self.assertIn("Motor Abito", FakeDialog.instances[0].text)
            FakeDialog.instances[0].respond(Gtk.ResponseType.ACCEPT)
            self.assertEqual(len(FakeDialog.instances), 2)
            self.assertIn("Compilar", FakeDialog.instances[1].text)
            self.assertIn("Abito", FakeDialog.instances[1].secondary)
            FakeDialog.instances[1].respond(Gtk.ResponseType.ACCEPT)

        self.assertTrue(done.wait(5.0))
        self.assertIn(("update", "engine-abito", "--build", "--yes", "--json"), calls)

    def test_update_all_runs_only_updatable_ids_in_order(self):
        """Actualizar todo (1): solo el actualizable del fixture, en orden."""
        manager, calls, done = self._manager(
            {
                "engine-abito": CliResult(
                    0, stdout=json.dumps(RESULTS_FIXTURE["success_applied"]["stdout"])
                ),
            }
        )
        block = self._block(manager)

        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            self.assertIn("(1)", block._all_button.get_label())
            block._all_button.emit("clicked")
            self.assertEqual(len(FakeDialog.instances), 1)
            FakeDialog.instances[0].respond(Gtk.ResponseType.ACCEPT)
            # un build-required en la lista: pide la segunda confirmación
            self.assertEqual(len(FakeDialog.instances), 2)
            FakeDialog.instances[1].respond(Gtk.ResponseType.ACCEPT)

        self.assertTrue(done.wait(5.0))
        update_calls = [call for call in calls if call[:1] == ("update",)]
        self.assertEqual(
            update_calls,
            [
                ("update", "engine-abito", "--build", "--yes", "--json"),
            ],
        )

    def test_update_all_with_several_updatables_keeps_the_listed_order(self):
        synthetic = json.dumps(
            {
                "checked_at": "t",
                "components": [
                    {
                        "id": "a-uno",
                        "kind": "tool",
                        "name": "Uno",
                        "installed": "1",
                        "available": "2",
                        "updatable": True,
                        "reason": None,
                        "restarts_engine": False,
                        "eta_s": 5,
                        "apply": None,
                    },
                    {
                        "id": "b-dos",
                        "kind": "tool",
                        "name": "Dos",
                        "installed": "1",
                        "available": None,
                        "updatable": False,
                        "reason": "sin canal",
                        "restarts_engine": False,
                        "eta_s": None,
                        "apply": None,
                    },
                    {
                        "id": "c-tres",
                        "kind": "tool",
                        "name": "Tres",
                        "installed": "1",
                        "available": "3",
                        "updatable": True,
                        "reason": None,
                        "restarts_engine": False,
                        "eta_s": 7,
                        "apply": None,
                    },
                ],
            }
        )

        calls = []
        done = threading.Event()

        def manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            if tuple(args)[:1] == ("updates",):
                return CliResult(0, stdout=synthetic)
            done.set()
            return CliResult(0, stdout=json.dumps({"verified": True}))

        block = self._block(manager)
        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            self.assertIn("(2)", block._all_button.get_label())
            block._all_button.emit("clicked")
            self.assertEqual(len(FakeDialog.instances), 1)  # nada compila
            FakeDialog.instances[0].respond(Gtk.ResponseType.ACCEPT)

        self.assertTrue(done.wait(5.0))
        # ``done`` salta con la PRIMERA orden: esperar a que el lote termine
        # (el bloque deja de estar ocupado) antes de mirar las dos.
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5.0
        while block._busy and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)
        update_calls = [call for call in calls if call[:1] == ("update",)]
        self.assertEqual(
            update_calls,
            [
                ("update", "a-uno", "--yes", "--json"),
                ("update", "c-tres", "--yes", "--json"),
            ],
        )

    def test_sin_actualizador_shows_the_list_error(self):
        _manager, _calls, _done = self._manager()

        def missing(args, timeout=None):
            del args, timeout
            return CliResult(None, failure="missing")

        block = self._block(missing)
        self.assertIn("sin actualizador", block._error_label.get_text())
        self.assertFalse(block._all_button.get_sensitive())



def _tool(component_id, name, apply=None):
    return {
        "id": component_id, "kind": "tool", "name": name, "installed": "1",
        "available": "2", "updatable": True, "reason": None,
        "restarts_engine": False, "eta_s": 5, "apply": apply,
    }


def _listing(*components):
    return json.dumps({"checked_at": "t", "components": list(components)})


class UpdateFlowResilienceTests(unittest.TestCase):
    """T18 (S20): review findings R3-001/R4-003 and R3-003/R4-001 on the
    update flow. Every update ends, one failure never blocks the next, and
    «Reintentar» does what the CLI asked for."""

    setUpClass = ActualizacionesWidgetTests.setUpClass
    _block = ActualizacionesWidgetTests._block

    def setUp(self):
        ActualizacionesWidgetTests.setUp(self)  # type: ignore[arg-type]
        from pc_ai_monitor.blocks import progreso

        self.progreso = progreso
        progreso.HUB.reset()
        self.addCleanup(progreso.HUB.reset)

    def _pump_until(self, condition, timeout=5.0):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() >= deadline:
                return False
            context.iteration(False)
            time.sleep(0.01)
        return True

    def test_a_raising_update_fails_its_row_and_the_next_one_still_runs(self):
        listing = _listing(_tool("a-uno", "Uno"), _tool("c-tres", "Tres"))
        calls = []

        def manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            if tuple(args)[:1] == ("updates",):
                return CliResult(0, stdout=listing)
            if "a-uno" in args:
                raise RuntimeError("runner roto")
            return CliResult(0, stdout=json.dumps({"verified": True}))

        block = self._block(manager)
        plan = [(row, False) for row in block._view.updatable_rows]
        block._begin(plan)
        self.assertTrue(self._pump_until(lambda: not block._busy), "quedó ocupado")

        from pc_ai_monitor.blocks import progreso_logica as pl

        first = self.progreso.HUB.get("update:a-uno").progress()
        second = self.progreso.HUB.get("update:c-tres").progress()
        self.assertEqual(first.state, pl.ERROR)
        self.assertIn("runner roto", first.detail)
        self.assertEqual(second.state, pl.SUCCESS)
        self.assertIn(("update", "c-tres", "--yes", "--json"), calls)
        self.assertIn("Uno", block._note.get_text())
        self.assertTrue(block._rows["c-tres"]["button"].get_sensitive())

    def test_a_raising_listing_still_releases_the_block(self):
        state = {"after": False}
        listing = _listing(_tool("a-uno", "Uno"))

        def manager(args, timeout=None):
            del timeout
            if tuple(args)[:1] == ("updates",):
                if state["after"]:
                    raise RuntimeError("listado roto")
                return CliResult(0, stdout=listing)
            state["after"] = True
            return CliResult(0, stdout=json.dumps({"verified": True}))

        block = self._block(manager)
        block._begin([(block._view.updatable_rows[0], False)])
        self.assertTrue(self._pump_until(lambda: not block._busy), "quedó ocupado")
        self.assertIn("listado roto", block._error_label.get_text())

    def test_a_raising_refresh_never_leaves_the_list_refreshing(self):
        def manager(args, timeout=None):
            raise RuntimeError("listado roto")

        block = self._block(manager)
        self.assertTrue(self._pump_until(lambda: not block._refreshing))
        self.assertIn("listado roto", block._error_label.get_text())

    def test_retry_after_build_required_asks_to_compile(self):
        """A component outside ``needs_build`` that answers build_required:
        «Reintentar» ran it again without --build, forever."""
        listing = _listing(_tool("x-motor", "Motor X"))
        calls = []
        needs_build = RESULTS_FIXTURE["needs_confirmation_build"]

        def manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            if tuple(args)[:1] == ("updates",):
                return CliResult(0, stdout=listing)
            if "--build" in args:
                return CliResult(0, stdout=json.dumps({"verified": True}))
            return CliResult(10, stdout=json.dumps(needs_build["stdout"]))

        block = self._block(manager)
        block._begin([(block._view.updatable_rows[0], False)])
        self.assertTrue(self._pump_until(lambda: not block._busy))
        activity = self.progreso.HUB.get("update:x-motor")
        self.assertIn("falta confirmar la compilación", activity.progress().message)

        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            activity.retry()
            self.assertEqual(len(FakeDialog.instances), 1)
            FakeDialog.instances[0].respond(Gtk.ResponseType.ACCEPT)
            self.assertEqual(len(FakeDialog.instances), 2, "no pidió compilar")
            self.assertIn("Compilar", FakeDialog.instances[1].text)
            FakeDialog.instances[1].respond(Gtk.ResponseType.ACCEPT)
        self.assertTrue(self._pump_until(lambda: not block._busy))
        self.assertIn(("update", "x-motor", "--build", "--yes", "--json"), calls)

    def test_retry_while_another_update_runs_says_why(self):
        listing = _listing(_tool("a-uno", "Uno"))
        block = self._block(lambda args, timeout=None: CliResult(0, stdout=listing))
        block._busy = True
        with mock.patch.object(self.mod.Gtk, "MessageDialog", FakeDialog):
            block._retry("a-uno")
        self.assertEqual(FakeDialog.instances, [])
        self.assertTrue(block._note.get_visible())
        self.assertIn("esperá", block._note.get_text())


if __name__ == "__main__":
    unittest.main()
