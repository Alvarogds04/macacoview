# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""S14: progreso honesto de las acciones largas (T11).

La lógica (``progreso_logica``) se testea pura: estados, textos, el parser de
las líneas «[ NN%]» de cmake y las etapas de ``gufo-flash status``. El widget
en línea, el indicador del header y el cableado de cada bloque se testean
sobre GTK real (xvfb) con el administrador SIEMPRE sustituido: los dobles de
``run_manager`` emiten líneas falsas; nunca se alcanza la máquina real.
"""

import json
import sys
import threading
import unittest
from pathlib import Path
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

from pc_ai_monitor.blocks import progreso_logica as pl

_aislamiento.isolate()


class Clock:
    """Reloj monotónico controlado por el test."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Lógica pura
# ---------------------------------------------------------------------------


class ClockTextTests(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(pl.clock_text(0), "0:00")
        self.assertEqual(pl.clock_text(41), "0:41")
        self.assertEqual(pl.clock_text(754), "12:34")

    def test_hours_when_needed(self):
        self.assertEqual(pl.clock_text(3723), "1:02:03")

    def test_duration_text(self):
        self.assertEqual(pl.duration_text(90), "90 s")
        self.assertEqual(pl.duration_text(900), "15 min")
        self.assertEqual(pl.duration_text(2700), "45 min")


class CmakeLineTests(unittest.TestCase):
    def test_reads_the_real_percentage(self):
        self.assertEqual(pl.cmake_percent("[ 62%] Building CXX object src/llama.cpp.o"), 62)
        self.assertEqual(pl.cmake_percent("[  5%] Built target ggml-base"), 5)
        self.assertEqual(pl.cmake_percent("[100%] Built target llama-server"), 100)

    def test_ignores_everything_else(self):
        for line in (
            "From https://github.com/ggml-org/llama.cpp",
            "Scanning dependencies of target llama",
            "[123/456] Building CXX object",
            "62% done",
            "",
        ):
            self.assertIsNone(pl.cmake_percent(line), line)


class BuildProgressTests(unittest.TestCase):
    def test_fetch_then_compile_then_restart(self):
        build = pl.BuildProgress()
        self.assertEqual((build.stage, build.percent), (1, None))

        self.assertFalse(build.feed(" * branch HEAD -> FETCH_HEAD"))
        self.assertEqual((build.stage, build.percent), (1, None))

        self.assertTrue(build.feed("[  5%] Building C object ggml.c.o"))
        self.assertEqual((build.stage, build.percent), (2, 5))
        build.feed("[ 62%] Building CXX object llama.cpp.o")
        self.assertEqual(build.percent, 62)

        build.feed("[100%] Built target llama-server")
        # Compilado: reiniciar y verificar no informan porcentaje.
        self.assertEqual((build.stage, build.percent), (3, None))
        self.assertFalse(build.rolled_back)

    def test_parallel_jitter_never_moves_the_bar_backwards(self):
        build = pl.BuildProgress()
        build.feed("[ 40%] a")
        build.feed("[ 38%] b")
        self.assertEqual(build.percent, 40)

    def test_a_second_compile_is_the_rollback(self):
        build = pl.BuildProgress()
        build.feed("[ 70%] a")
        build.feed("[  3%] recompiling the previous commit")
        self.assertTrue(build.rolled_back)
        self.assertEqual((build.stage, build.percent), (2, 3))


class SwitchStagesTests(unittest.TestCase):
    def test_follows_loading_then_postload(self):
        stages = pl.SwitchStages()
        self.assertEqual(stages.stage, 1)
        self.assertTrue(stages.feed("loading"))
        self.assertEqual(stages.stage, 2)
        self.assertTrue(stages.feed("postload"))
        self.assertEqual(stages.stage, 3)
        self.assertFalse(stages.feed("loading"))  # nunca vuelve atrás
        self.assertEqual(stages.stage, 3)

    def test_a_stale_phase_before_loading_is_ignored(self):
        """Un «workload» del arranque anterior no adelanta la etapa."""
        stages = pl.SwitchStages()
        self.assertFalse(stages.feed("workload"))
        self.assertFalse(stages.feed(""))
        self.assertEqual(stages.stage, 1)


class ActivityStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def _activity(self, **kw):
        kw.setdefault("typical_s", 30.0)
        kw.setdefault("limit_s", 90.0)
        return pl.Activity("engine:abito", "Encendiendo Abito", clock=self.clock, **kw)

    def test_starts_indeterminate_with_elapsed_time(self):
        activity = self._activity()
        self.clock.now += 41
        progress = activity.progress()
        self.assertEqual(progress.state, pl.INDETERMINATE)
        self.assertIsNone(progress.percent)
        self.assertAlmostEqual(progress.elapsed_s, 41)
        self.assertEqual((progress.typical_s, progress.limit_s), (30.0, 90.0))

    def test_time_never_becomes_a_percentage(self):
        activity = self._activity()
        self.clock.now += 85  # casi en el límite: sigue sin porcentaje
        self.assertIsNone(activity.progress().percent)
        self.assertEqual(activity.progress().state, pl.INDETERMINATE)

    def test_stage_state(self):
        activity = self._activity(stages=("Apagando Abito", "Cargando pesos", "Verificando"))
        activity.set_stage(2)
        progress = activity.progress()
        self.assertEqual(progress.state, pl.STAGE)
        self.assertEqual((progress.stage, len(progress.stages)), (2, 3))

    def test_real_percentage_is_determinate(self):
        activity = self._activity(stages=pl.BuildProgress.STAGES)
        activity.set_stage(2)
        activity.set_percent(62)
        progress = activity.progress()
        self.assertEqual(progress.state, pl.DETERMINATE)
        self.assertEqual(progress.percent, 62)

    def test_invalid_percentages_are_ignored(self):
        activity = self._activity()
        for value in (None, -1, 101, 150):
            activity.set_percent(value)
        self.assertEqual(activity.progress().state, pl.INDETERMINATE)

    def test_a_new_stage_drops_the_old_percentage(self):
        activity = self._activity(stages=pl.BuildProgress.STAGES)
        activity.set_stage(2)
        activity.set_percent(100)
        activity.set_stage(3)
        self.assertEqual(activity.progress().state, pl.STAGE)
        self.assertIsNone(activity.progress().percent)

    def test_success_freezes_the_elapsed_time(self):
        activity = self._activity()
        self.clock.now += 28
        activity.succeed("Abito encendido")
        self.clock.now += 100
        progress = activity.progress()
        self.assertEqual(progress.state, pl.SUCCESS)
        self.assertAlmostEqual(progress.elapsed_s, 28)
        self.assertEqual(progress.message, "Abito encendido")

    def test_error_keeps_reason_detail_and_retry(self):
        retried = []
        activity = self._activity(retry=lambda: retried.append(1))
        activity.fail("No se encendió", "no alcanza la memoria")
        progress = activity.progress()
        self.assertEqual(progress.state, pl.ERROR)
        self.assertEqual(progress.message, "No se encendió")
        self.assertEqual(progress.detail, "no alcanza la memoria")
        self.assertTrue(progress.retry)
        activity.retry()
        self.assertEqual(retried, [1])

    def test_finishing_twice_keeps_the_first_outcome(self):
        activity = self._activity()
        activity.succeed("ok")
        activity.fail("tarde")
        self.assertEqual(activity.progress().state, pl.SUCCESS)


class TextTests(unittest.TestCase):
    def _progress(self, **kw):
        base = {
            "state": pl.INDETERMINATE,
            "title": "Encendiendo Abito",
            "elapsed_s": 41.0,
            "typical_s": 30.0,
            "limit_s": 90.0,
        }
        base.update(kw)
        return pl.Progress(**base)

    def test_meta_past_the_usual_time_says_so(self):
        left, right = pl.meta_texts(self._progress())
        self.assertEqual(left, "0:41 transcurridos · más de lo habitual (~30 s)")
        self.assertEqual(right, "límite 90 s")

    def test_meta_within_the_usual_time(self):
        left, _right = pl.meta_texts(self._progress(elapsed_s=20.0))
        self.assertEqual(left, "0:20 transcurridos · suele tardar ~30 s")

    def test_meta_without_typical_or_limit(self):
        left, right = pl.meta_texts(self._progress(typical_s=None, limit_s=None))
        self.assertEqual((left, right), ("0:41 transcurridos", ""))

    def test_meta_determinate_shows_the_real_percentage(self):
        progress = self._progress(
            state=pl.DETERMINATE, percent=62, elapsed_s=754.0, typical_s=None,
            limit_s=2700.0, stage=2, stages=pl.BuildProgress.STAGES,
        )
        self.assertEqual(
            pl.meta_texts(progress), ("62 % · 12:34 transcurridos", "límite 45 min")
        )

    def test_headlines(self):
        self.assertEqual(pl.headline(self._progress()), "Encendiendo Abito…")
        stage = self._progress(
            state=pl.STAGE, title="Cambiando a Gufo", stage=2,
            stages=("Apagando Abito", "Cargando pesos (loading)", "Verificando (postload)"),
        )
        self.assertEqual(
            pl.headline(stage), "Cambiando a Gufo · etapa 2 de 3 · cargando pesos (loading)"
        )
        done = self._progress(state=pl.SUCCESS, message="Abito encendido")
        self.assertEqual(pl.headline(done), "✓ Abito encendido")
        failed = self._progress(state=pl.ERROR, message="No se encendió")
        self.assertEqual(pl.headline(failed), "No se encendió")

    def test_header_texts(self):
        self.assertEqual(pl.header_text(self._progress()), "Encendiendo Abito · 0:41")
        stage = self._progress(
            state=pl.STAGE, title="Cambiando a Gufo", stage=2, elapsed_s=72.0,
            stages=("a", "b", "c"),
        )
        self.assertEqual(pl.header_text(stage), "Cambiando a Gufo · 2/3 · 1:12")
        compiling = self._progress(
            state=pl.DETERMINATE, title="Actualizando Abito", percent=62, stage=2,
            stages=pl.BuildProgress.STAGES,
        )
        self.assertEqual(pl.header_text(compiling), "Actualizando Abito · compilando 62 %")


class HubTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hub = pl.ActivityHub(clock=self.clock)

    def _begin(self, key, title="x"):
        activity = pl.Activity(key, title, clock=self.clock)
        self.hub.begin(activity)
        return activity

    def test_running_activity_is_current(self):
        self.assertIsNone(self.hub.current())
        activity = self._begin("engine:abito")
        self.assertIs(self.hub.current(), activity)
        self.assertTrue(self.hub.running("engine:abito"))

    def test_running_wins_over_a_recently_finished_one(self):
        first = self._begin("update:a")
        first.succeed("ok")
        self.hub.changed()
        second = self._begin("engine:abito")
        self.assertIs(self.hub.current(), second)

    def test_finished_lingers_then_goes_away(self):
        activity = self._begin("engine:abito")
        activity.succeed("ok")
        self.hub.changed()
        self.clock.now += pl.LINGER_S - 1
        self.assertIs(self.hub.current(), activity)
        self.clock.now += 2
        self.assertIsNone(self.hub.current())

    def test_finished_callbacks_fire_once(self):
        seen = []
        self.hub.on_finished(seen.append)
        activity = self._begin("engine:abito")
        self.hub.changed()
        self.assertEqual(seen, [])
        activity.fail("no")
        self.hub.changed()
        self.hub.changed()
        self.assertEqual(seen, [activity])

    def test_same_key_restart_replaces_the_old_one(self):
        old = self._begin("engine:abito")
        old.fail("no")
        new = self._begin("engine:abito")
        self.assertIs(self.hub.get("engine:abito"), new)

    def test_find_by_prefix(self):
        activity = self._begin("update:engine-abito")
        self.assertIs(self.hub.find("update:"), activity)
        self.assertIsNone(self.hub.find("switch"))


class SpecsTests(unittest.TestCase):
    def test_ensure_abito_is_indeterminate_with_its_usual_time(self):
        activity = pl.engine_action("abito", "ensure")
        self.assertEqual(activity.key, "engine:abito")
        self.assertEqual(activity.title, "Encendiendo Abito")
        self.assertEqual((activity.typical_s, activity.limit_s), (30.0, 90.0))
        self.assertEqual(activity.progress().state, pl.INDETERMINATE)

    def test_switch_to_gufo_has_three_observable_stages(self):
        activity = pl.switch_action("gufo")
        self.assertEqual(activity.key, "switch")
        self.assertEqual(len(activity.stages), 3)
        self.assertEqual(activity.progress().state, pl.STAGE)
        self.assertEqual((activity.typical_s, activity.limit_s), (90.0, 900.0))

    def test_switch_to_abito_has_no_stages(self):
        activity = pl.switch_action("abito")
        self.assertEqual(activity.stages, ())
        self.assertEqual(activity.progress().state, pl.INDETERMINATE)

    def test_build_update_has_the_compile_stages(self):
        activity = pl.update_action("engine-abito", "Motor Abito", None, with_build=True)
        self.assertEqual(activity.key, "update:engine-abito")
        self.assertEqual(activity.stages, pl.BuildProgress.STAGES)
        self.assertEqual(activity.limit_s, 2700.0)

    def test_plain_update_uses_the_manager_eta(self):
        activity = pl.update_action("router-pi", "Router", 12.0)
        self.assertEqual((activity.typical_s, activity.stages), (12.0, ()))


# ---------------------------------------------------------------------------
# Streaming del administrador (sin tocar el administrador real)
# ---------------------------------------------------------------------------


class StreamProcessTests(unittest.TestCase):
    """``_stream_process`` corre un argv arbitrario: aquí python -c, nunca
    ``local-ai-models``."""

    def test_lines_arrive_from_both_streams_and_the_result_keeps_stdout(self):
        from pc_ai_monitor.blocks import motores

        script = (
            "import sys\n"
            "print('[ 10%] a', file=sys.stderr, flush=True)\n"
            "print('[ 62%] b', file=sys.stderr, flush=True)\n"
            "print('{\"verified\": true}', flush=True)\n"
        )
        lines = []
        result = motores._stream_process([sys.executable, "-c", script], 10, lines.append)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"verified": true}')
        self.assertIn("[ 62%] b", result.stderr)
        self.assertEqual(sorted(lines), sorted(["[ 10%] a", "[ 62%] b", '{"verified": true}']))

    def test_timeout_is_reported(self):
        from pc_ai_monitor.blocks import motores

        result = motores._stream_process(
            [sys.executable, "-c", "import time; time.sleep(5)"], 0.3, lambda _l: None
        )
        self.assertEqual(result.failure, "timeout")

    def test_missing_binary_is_reported(self):
        from pc_ai_monitor.blocks import motores

        result = motores._stream_process(["/nonexistent/macacoview-x"], 5, lambda _l: None)
        self.assertEqual(result.failure, "missing")

    def test_stream_manager_falls_back_for_plain_runners(self):
        """Los dobles de los tests (y el guard de aislamiento) no hacen
        streaming: las líneas llegan igual, al final."""
        from pc_ai_monitor.blocks import motores

        def plain(args, timeout=None):
            del args, timeout
            return motores.CommandResult(0, "out", "[ 50%] x")

        lines = []
        result = motores.stream_manager(plain, ("update", "x"), 5, lines.append)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(lines, ["[ 50%] x", "out"])

    def test_real_run_manager_declares_streaming(self):
        self.assertTrue(getattr(_original_run_manager(), "streams_lines", False))

    # -- T18 (S20): review findings R3-002 / R4-002 on the streaming runner --

    def test_a_raising_on_line_never_stops_draining_the_child(self):
        """A broken callback must not kill the reader: the child would block
        on a full pipe (64 KiB) and the update would end as a timeout."""
        from pc_ai_monitor.blocks import motores

        script = (
            "import sys\n"
            "for i in range(4000):\n"
            "    sys.stdout.write('x' * 80 + '\\n')\n"
            "print('{\"verified\": true}', flush=True)\n"
        )

        def broken(_line):
            raise RuntimeError("callback roto")

        result = motores._stream_process([sys.executable, "-c", script], 5, broken)
        self.assertIsNone(result.failure)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith('{"verified": true}'), result.stdout[-80:])

    def test_non_utf8_output_is_replaced_not_lost(self):
        """cmake/git may print bytes that are not UTF-8: the line keeps
        flowing (with U+FFFD) instead of killing the reader."""
        from pc_ai_monitor.blocks import motores

        script = (
            "import sys\n"
            "sys.stderr.buffer.write(b'caf\\xe9\\n[ 50%] b\\n')\n"
            "sys.stderr.flush()\n"
            "print('ok', flush=True)\n"
        )
        lines = []
        result = motores._stream_process([sys.executable, "-c", script], 10, lines.append)
        self.assertEqual(result.returncode, 0)
        self.assertIn("[ 50%] b", lines)
        self.assertIn("caf\ufffd", result.stderr)
        self.assertEqual(result.stdout, "ok")

    def test_a_final_line_without_newline_survives_a_held_pipe(self):
        """A grandchild that inherits the pipe keeps it open after the child
        exits; the last JSON line (no newline) must not be dropped."""
        from pc_ai_monitor.blocks import motores

        script = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])\n"
            "sys.stdout.write('{\"verified\": true}')\n"
            "sys.stdout.flush()\n"
        )
        lines = []
        with mock.patch.object(motores, "READER_GRACE_S", 0.5, create=True):
            result = motores._stream_process(
                [sys.executable, "-c", script], 10, lines.append
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"verified": true}')
        self.assertIn('{"verified": true}', lines)

    def test_timeout_keeps_the_output_read_so_far(self):
        """«Ver detalle» of a timed-out update shows what it printed."""
        from pc_ai_monitor.blocks import motores

        script = (
            "import sys, time\n"
            "print('[ 40%] a', flush=True)\n"
            "time.sleep(5)\n"
        )
        result = motores._stream_process([sys.executable, "-c", script], 0.5, lambda _l: None)
        self.assertEqual(result.failure, "timeout")
        self.assertIsNone(result.returncode)
        self.assertEqual(result.stdout, "[ 40%] a")

    def test_run_manager_tolerates_non_utf8_output(self):
        """The plain runner raised UnicodeDecodeError into its worker, which
        then never finished its activity."""
        from pc_ai_monitor.blocks import motores

        script = "import sys; sys.stdout.buffer.write(b'v\\xff1')"
        with mock.patch.object(motores, "MANAGER", sys.executable):
            result = _original_run_manager()(("-c", script), 10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "v\ufffd1")


def _original_run_manager():
    """La función real (el guard de aislamiento la sustituye en el módulo)."""
    for patcher in _aislamiento._PATCHES:
        if getattr(patcher, "attribute", "") == "run_manager" and getattr(
            patcher.target, "__name__", ""
        ).endswith("blocks.motores"):
            return patcher.temp_original
    raise AssertionError("sin patch de motores.run_manager")


class StreamingRunner:
    """Doble de ``run_manager`` que emite líneas y espera al test."""

    streams_lines = True

    def __init__(self, lines_by_command, results, hold=None):
        self.lines_by_command = lines_by_command
        self.results = results
        self.hold = hold
        self.calls = []
        self.emitted = threading.Event()

    def __call__(self, args, timeout=None, on_line=None):
        del timeout
        args = tuple(args)
        self.calls.append(args)
        for prefix, lines in self.lines_by_command.items():
            if args[: len(prefix)] == prefix:
                for line in lines:
                    if on_line is not None:
                        on_line(line)
                self.emitted.set()
                if self.hold is not None:
                    self.hold.wait(5.0)
                return self.results[prefix]
        return self.results.get((), _cli(0, "{}"))


def _cli(rc, stdout="", stderr="", failure=None):
    from pc_ai_monitor.blocks.motores import CommandResult

    return CommandResult(rc, stdout, stderr, failure)


# ---------------------------------------------------------------------------
# GTK: widget en línea, indicador del header y cableado
# ---------------------------------------------------------------------------


class _GtkCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")
        Adw.init()

    def setUp(self):
        from pc_ai_monitor.blocks import progreso

        self.progreso = progreso
        progreso.HUB.reset()
        self.addCleanup(progreso.HUB.reset)


def _labels(widget):
    """Texto de todos los Gtk.Label/Gtk.Button visibles bajo ``widget``."""
    out = []
    child = widget.get_first_child()
    while child is not None:
        if child.get_visible():
            if isinstance(child, Gtk.Label):
                out.append(child.get_text())
            elif isinstance(child, Gtk.Button) and child.get_label():
                out.append(child.get_label())
            out.extend(_labels(child))
        child = child.get_next_sibling()
    return out


class InlineWidgetTests(_GtkCase):
    def test_determinate_shows_the_real_percentage_and_stages(self):
        hub = self.progreso.HUB
        inline = self.progreso.ProgressInline("update:engine-abito")
        activity = pl.update_action("engine-abito", "Abito", None, with_build=True)
        hub.begin(activity)
        activity.set_stage(2)
        activity.set_percent(62)
        hub.changed()

        self.assertTrue(inline.get_visible())
        self.assertAlmostEqual(inline._bar.get_fraction(), 0.62)
        self.assertIn("Actualizando Abito · etapa 2 de 3 · compilando", _labels(inline))
        self.assertEqual(len(inline._steps), 3)
        self.assertNotIn("Cancelar", _labels(inline))

    def test_indeterminate_has_no_percentage_text(self):
        hub = self.progreso.HUB
        inline = self.progreso.ProgressInline("engine:abito")
        hub.begin(pl.engine_action("abito", "ensure"))
        hub.changed()
        texts = _labels(inline)
        self.assertIn("Encendiendo Abito…", texts)
        self.assertFalse(any("%" in text for text in texts), texts)
        self.assertIn("límite 90 s", texts)

    def test_never_widens_the_column_of_its_card(self):
        """Render: Home's 276 px update card doubled its width while the
        block was visible — the inner labels' hexpand leaked upwards."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inline = self.progreso.ProgressInline("engine-update", max_chars=28)
        card.append(inline)
        self.progreso.HUB.begin(pl.engine_update_action())
        self.progreso.HUB.changed()
        self.assertFalse(card.compute_expand(Gtk.Orientation.HORIZONTAL))
        self.assertLessEqual(inline.measure(Gtk.Orientation.HORIZONTAL, -1).natural, 276)

    def test_hidden_without_a_matching_activity(self):
        inline = self.progreso.ProgressInline("engine:gufo")
        self.progreso.HUB.begin(pl.engine_action("abito", "ensure"))
        self.progreso.HUB.changed()
        self.assertFalse(inline.get_visible())

    def test_error_offers_retry_and_detail_but_never_cancel(self):
        retried = []
        hub = self.progreso.HUB
        inline = self.progreso.ProgressInline("engine:abito")
        activity = pl.engine_action("abito", "ensure", retry=lambda: retried.append(1))
        hub.begin(activity)
        activity.fail(
            "No se encendió",
            "No alcanza la memoria para conservar 20 GiB libres. Nada cambió.",
            log="Arranque local rechazado para conservar 20 GiB libres.",
        )
        hub.changed()

        texts = _labels(inline)
        self.assertIn("No se encendió", texts)
        # La razón se lee sin abrir nada; el detalle crudo, a pedido.
        self.assertIn("No alcanza la memoria para conservar 20 GiB libres. Nada cambió.", texts)
        self.assertIn("Reintentar", texts)
        self.assertIn("Ver detalle", texts)
        self.assertNotIn("Cancelar", texts)
        self.assertFalse(inline._detail.get_visible())
        inline._detail_button.emit("clicked")
        self.assertTrue(inline._detail.get_visible())
        self.assertEqual(inline._detail.get_text(), "Arranque local rechazado para conservar 20 GiB libres.")
        inline._retry_button.emit("clicked")
        self.assertEqual(retried, [1])


class HeaderIndicatorTests(_GtkCase):
    def test_hidden_when_idle_and_visible_while_running(self):
        opened = []
        indicator = self.progreso.HeaderIndicator(
            lambda section, anchor: opened.append((section, anchor))
        )
        self.assertFalse(indicator.get_visible())

        anchor = Gtk.Box()
        activity = pl.engine_action("abito", "ensure", section="home", anchor=anchor)
        self.progreso.HUB.begin(activity)
        self.progreso.HUB.changed()

        self.assertTrue(indicator.get_visible())
        self.assertTrue(indicator._label.get_text().startswith("Encendiendo Abito · 0:0"))
        indicator.emit("clicked")
        self.assertEqual(opened, [("home", anchor)])

    def test_finished_error_paints_the_pill_red(self):
        indicator = self.progreso.HeaderIndicator(lambda *_a: None)
        activity = pl.engine_action("abito", "ensure")
        self.progreso.HUB.begin(activity)
        activity.fail("Abito no se encendió", "x")
        self.progreso.HUB.changed()
        self.assertTrue(indicator.has_css_class("gpill-err"))
        self.assertEqual(indicator._label.get_text(), "Abito no se encendió")


class ShellIndicatorTests(_GtkCase):
    """Cada página del shell lleva el indicador; tocarlo lleva a la tarjeta."""

    def _app(self):
        from pc_ai_monitor.app import MonitorApplication

        application = MonitorApplication()
        application._nav = Adw.NavigationView()
        application._split = Adw.OverlaySplitView()
        application._build_sidebar()
        return application

    def test_every_section_header_has_the_indicator_and_it_navigates(self):
        application = self._app()
        application._select_section("recursos")
        page = application._sections["recursos"]
        indicator = application._indicators[page]

        activity = pl.update_action("engine-abito", "Abito", None, with_build=True)
        activity.section = "motores"
        self.progreso.HUB.begin(activity)
        self.progreso.HUB.changed()
        self.assertTrue(indicator.get_visible())

        indicator.emit("clicked")
        assert application._listbox is not None
        selected = application._listbox.get_selected_row()
        self.assertEqual(application._rows[selected], "motores")

    def test_finishing_shows_a_toast(self):
        application = self._app()
        application._toasts = Adw.ToastOverlay()
        application._watch_activities()
        shown = []
        application._toasts.add_toast = shown.append  # type: ignore[method-assign]

        activity = pl.engine_action("abito", "ensure", section="home")
        self.progreso.HUB.begin(activity)
        activity.succeed("Abito encendido")
        self.progreso.HUB.changed()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0].get_title(), "Abito encendido")

    def test_toast_title_is_plain_text(self):
        """R3-005: Adw.Toast parses its title as Pango markup by default; a
        name from the CLI with «&» or «<» would render an empty toast."""
        application = self._app()
        application._toasts = Adw.ToastOverlay()
        application._watch_activities()
        shown = []
        application._toasts.add_toast = shown.append  # type: ignore[method-assign]

        activity = pl.update_action("router-pi", "Router & <Pi>", None, section="motores")
        self.progreso.HUB.begin(activity)
        activity.fail("No se pudo actualizar Router & <Pi>", "x")
        self.progreso.HUB.changed()
        self.assertEqual(len(shown), 1)
        self.assertFalse(shown[0].get_use_markup())
        self.assertEqual(shown[0].get_title(), "No se pudo actualizar Router & <Pi>")


class ActualizacionesProgressTests(_GtkCase):
    """«Actualizar» de una fila: barra con el % real de cmake en la fila."""

    def _block(self, runner):
        from pc_ai_monitor.blocks import actualizaciones
        from pc_ai_monitor.blocks.actualizaciones_logica import CliResult as UpdatesCli

        patcher = mock.patch.object(actualizaciones, "run_manager", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        block = actualizaciones.ActualizacionesBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        fixture = (_FIXTURES / "updates_real_2026-11-04.json").read_text()
        block._on_list_loaded(UpdatesCli(0, stdout=fixture))
        return block

    def test_build_update_streams_the_cmake_percentage_into_the_row(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        results = json.loads((_FIXTURES / "update_results_real_2026-11-04.json").read_text())
        runner = StreamingRunner(
            {
                ("update", "engine-abito"): [
                    "From https://github.com/ggml-org/llama.cpp",
                    "[ 12%] Building C object ggml.c.o",
                    "[ 62%] Building CXX object llama.cpp.o",
                ],
                ("updates",): [],
            },
            {
                ("update", "engine-abito"): _cli(0, json.dumps(results["success_applied"]["stdout"])),
                ("updates",): _cli(0, (_FIXTURES / "updates_real_2026-11-04.json").read_text()),
            },
            hold=hold,
        )
        block = self._block(runner)
        row = next(r for r in block._view.rows if r.id == "engine-abito")
        block._begin([(row, True)])

        self.assertTrue(runner.emitted.wait(5.0))
        hub = self.progreso.HUB
        self.assertTrue(dialogos.pump_until(
            lambda: _percent_of("update:engine-abito") == 62
        ))
        inline = block._rows["engine-abito"]["progress"]
        self.assertTrue(inline.get_visible())
        self.assertAlmostEqual(inline._bar.get_fraction(), 0.62)
        current = hub.current()
        assert current is not None
        self.assertIn("compilando 62 %", pl.header_text(current.progress()))
        # El botón de la fila queda bloqueado mientras corre y lo dice.
        button = block._rows["engine-abito"]["button"]
        self.assertFalse(button.get_sensitive())
        self.assertEqual(button.get_label(), "Actualizando…")

        hold.set()
        self.assertTrue(dialogos.pump_until(lambda: not block._busy))
        self.assertEqual(_progress_of("update:engine-abito").state, pl.SUCCESS)

    def test_rollback_ends_in_error_with_retry(self):
        results = json.loads((_FIXTURES / "update_results_real_2026-11-04.json").read_text())
        rollback = results["rollback_isolated"]
        runner = StreamingRunner(
            {("update", "router-pi"): ["npm ERR! x"], ("updates",): []},
            {
                ("update", "router-pi"): _cli(1, json.dumps(rollback["stdout"]), "npm ERR! x"),
                ("updates",): _cli(0, (_FIXTURES / "updates_real_2026-11-04.json").read_text()),
            },
        )
        block = self._block(runner)
        row = next(r for r in block._view.rows if r.id == "router-pi")
        block._begin([(row, False)])
        self.assertTrue(dialogos.pump_until(lambda: not block._busy))

        progress = _progress_of("update:router-pi")
        self.assertEqual(progress.state, pl.ERROR)
        self.assertTrue(progress.retry)
        self.assertIn("restauró", progress.detail)
        self.assertIn("npm ERR! x", progress.log)


class MotoresEngineProgressTests(_GtkCase):
    """Encender en «Motores locales»: barra pulsante en la tarjeta."""

    def test_ensure_shows_inline_progress_in_the_card(self):
        from pc_ai_monitor.blocks import motores

        hold = threading.Event()
        self.addCleanup(hold.set)
        stopped = dict(_ABITO, healthy=False)
        healthy = json.dumps([dict(_ABITO, healthy=True)])

        def fake(*args, **_kw):
            if args[:1] == ("ensure",):
                hold.wait(5.0)
                return _cli(0, "abito listo")
            if args[:2] == ("status", "--json"):
                return _cli(0, healthy)
            return _cli(0, "route=local")

        with mock.patch.object(motores, "_run_manager", fake):
            block = motores.ModelsInventoryBlock()
            dialogos.pump_until(lambda: not block._refreshing)
            block._on_inventory_loaded([stopped], None)
            card = block._cards["abito"]
            card._start_button.emit("clicked")
            dialogos.pump()

            activity = _activity("engine:abito")
            self.assertEqual(activity.progress().state, pl.INDETERMINATE)
            self.assertTrue(card._progress.get_visible())
            hold.set()
            self.assertTrue(dialogos.pump_until(lambda: activity.progress().finished))
        self.assertEqual(activity.progress().state, pl.SUCCESS)


class SwitchProgressTests(_GtkCase):
    """Cambiar a Gufo (Motores): etapa leída de ``gufo-flash status``."""

    def test_switch_to_gufo_follows_the_guard_phase_without_a_percentage(self):
        from pc_ai_monitor.blocks import motores_vista, rendimiento

        hold = threading.Event()
        self.addCleanup(hold.set)
        phases = iter(["loading", "loading", "postload", "postload"])

        def status():
            phase = next(phases, "postload")
            return json.dumps({"status": {"phase": phase}, "engine_ready": False}), 0, None

        def runner(args, timeout=None, **_kw):
            del timeout
            if tuple(args)[:1] == ("switch",):
                hold.wait(5.0)
                return _cli(0, '{"status": "SWITCHED", "resident": "gufo"}')
            return _cli(0, json.dumps({"resident": "gufo", "gufo": {}, "abito": {}}))

        with (
            mock.patch.object(motores_vista, "run_manager", runner),
            mock.patch.object(rendimiento, "_run_gufo_flash_status", status),
            mock.patch.object(self.progreso, "STAGE_POLL_S", 0.05),
        ):
            block = motores_vista.ResidenteVistaBlock()
            dialogos.pump_until(lambda: not block._refreshing)
            block._start_switch("gufo")
            self.assertTrue(dialogos.pump_until(
                lambda: _progress_of("switch").stage >= 2
            ))
            progress = _progress_of("switch")
            self.assertIn(progress.state, (pl.STAGE,))
            self.assertIsNone(progress.percent)
            self.assertTrue(block._progress.get_visible())
            hold.set()
            self.assertTrue(dialogos.pump_until(lambda: block._switching is None))
        self.assertEqual(_progress_of("switch").state, pl.SUCCESS)


class HomeUpdateProgressTests(_GtkCase):
    def test_engine_update_from_home_shows_inline_progress(self):
        from pc_ai_monitor.blocks import home

        hold = threading.Event()
        self.addCleanup(hold.set)

        def runner(args, timeout=None):
            del timeout
            if tuple(args)[:2] == ("engine", "update"):
                hold.wait(5.0)
                return _cli(0, '{"from": "a", "to": "b", "verified": true}')
            return _cli(1, "", "x")

        poller = _FakePoller()
        with mock.patch.object(home, "run_manager", runner):
            card = home.UpdateCardBlock(poller=poller)
            card._begin_update()
            dialogos.pump()
            activity = _activity("engine-update")
            self.assertEqual(activity.progress().state, pl.INDETERMINATE)
            self.assertTrue(card._progress.get_visible())
            hold.set()
            self.assertTrue(dialogos.pump_until(lambda: activity.progress().finished))
        self.assertEqual(activity.progress().state, pl.SUCCESS)


# ---------------------------------------------------------------------------
# T19 (S21): «Detener» mientras un modelo carga
# ---------------------------------------------------------------------------


class StopLogicTests(unittest.TestCase):
    """Lógica pura de Detener: estado neutro, una sola orden, sin linger."""

    def setUp(self):
        self.clock = Clock()

    def _ensure(self, stopper=None):
        activity = pl.engine_action("abito", "ensure", clock=self.clock)
        activity.stopper = stopper
        return activity

    def test_only_a_load_with_a_stopper_can_be_stopped(self):
        self.assertFalse(self._ensure().progress().stoppable)
        calls = []
        activity = self._ensure(lambda: calls.append(1))
        self.assertTrue(activity.progress().stoppable)

    def test_request_stop_runs_the_stopper_once_and_disables_it(self):
        calls = []
        activity = self._ensure(lambda: calls.append(1))
        self.assertTrue(activity.request_stop())
        self.assertFalse(activity.request_stop())
        self.assertEqual(calls, [1])
        progress = activity.progress()
        self.assertTrue(progress.running)
        self.assertTrue(progress.stopping)
        self.assertFalse(progress.stoppable)
        self.assertEqual(pl.headline(progress), "Deteniendo la carga…")

    def test_a_finished_load_ignores_a_late_stop(self):
        calls = []
        activity = self._ensure(lambda: calls.append(1))
        activity.succeed("Abito encendido")
        self.assertFalse(activity.request_stop())
        self.assertEqual(calls, [])
        self.assertFalse(activity.progress().stoppable)

    def test_halt_is_a_neutral_end_distinct_from_success_and_error(self):
        activity = self._ensure(lambda: None)
        activity.request_stop()
        self.clock.now += 4
        activity.halt(pl.STOPPED_MESSAGE, "Ningún motor quedó cargado.")
        progress = activity.progress()
        self.assertEqual(progress.state, pl.STOPPED)
        self.assertNotIn(progress.state, (pl.SUCCESS, pl.ERROR))
        self.assertTrue(progress.finished)
        self.assertFalse(progress.running)
        self.assertFalse(progress.retry)
        self.assertEqual(progress.message, "Carga detenida")
        self.assertEqual(pl.headline(progress), "Detenido")
        self.assertEqual(pl.meta_texts(progress), ("Tras 0:04", ""))

    def test_a_stopped_load_clears_the_header_at_once(self):
        hub = pl.ActivityHub(clock=self.clock)
        activity = hub.begin(self._ensure(lambda: None))
        self.assertIs(hub.current(), activity)
        activity.halt(pl.STOPPED_MESSAGE)
        hub.changed()
        self.assertIsNone(hub.current())
        # ...but the card still shows «Detenido» for a while.
        self.assertTrue(hub.lingering())
        self.clock.now += pl.LINGER_S + 1
        self.assertFalse(hub.lingering())

    def test_stopped_is_announced_once_like_any_outcome(self):
        hub = pl.ActivityHub(clock=self.clock)
        seen = []
        hub.on_finished(seen.append)
        activity = hub.begin(self._ensure(lambda: None))
        activity.halt(pl.STOPPED_MESSAGE)
        hub.changed()
        hub.changed()
        self.assertEqual(seen, [activity])

    def test_confirmation_texts(self):
        for target in ("abito", "gufo"):
            title, body = pl.stop_confirm_text(target)
            self.assertEqual(title, f"¿Detener la carga de {pl.display_name(target)}?")
            self.assertIn("deja de cargarse", body)
            self.assertIn("ningún motor queda cargado", body)

    def test_stop_outcome(self):
        ok = _cli(0)
        self.assertEqual(
            pl.stop_outcome("abito", ok, "none")[0], pl.STOPPED
        )
        state, message, detail = pl.stop_outcome("abito", _cli(1, "", "unit busy"), "none")
        self.assertEqual(state, pl.ERROR)
        self.assertEqual(message, "No se pudo detener Abito")
        self.assertIn("unit busy", detail)
        state, _m, detail = pl.stop_outcome("gufo", _cli(None, failure="missing"), None)
        self.assertEqual(state, pl.ERROR)
        self.assertIn("gufo-flash", detail)
        # El backend dice que sigue cargado: nunca «Detenido».
        state, _m, _d = pl.stop_outcome("abito", ok, "abito")
        self.assertEqual(state, pl.ERROR)
        # Sin relectura: nunca un estado que el backend no confirmó.
        state, _m, detail = pl.stop_outcome("abito", ok, None)
        self.assertEqual(state, pl.ERROR)
        self.assertIn("no se pudo leer", detail)
        # Gufo todavía cargando: tampoco.
        self.assertEqual(pl.stop_outcome("gufo", ok, "gufo-loading")[0], pl.ERROR)


class StopChildProcessTests(unittest.TestCase):
    """Detener termina el hijo propio del runner: SIGTERM, luego SIGKILL."""

    def _run_in_thread(self, call):
        box = {}

        def _target():
            box["result"] = call()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        return thread, box

    def test_stop_terminates_a_real_sleep_and_returns_promptly(self):
        import time

        from pc_ai_monitor.blocks import motores

        stop = motores.StopRequest()
        started = time.monotonic()
        thread, box = self._run_in_thread(
            lambda: motores._stream_process(["sleep", "30"], 60, None, stop=stop)
        )
        time.sleep(0.3)
        self.assertTrue(thread.is_alive())
        stop.request()
        thread.join(5.0)
        self.assertFalse(thread.is_alive(), "el runner no volvió tras Detener")
        self.assertLess(time.monotonic() - started, 5.0)
        result = box["result"]
        self.assertEqual(result.failure, "stopped")
        self.assertIsNone(result.returncode)

    def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(self):
        from pc_ai_monitor.blocks import motores

        script = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        ready = threading.Event()
        stop = motores.StopRequest()
        with mock.patch.object(motores, "STOP_GRACE_S", 0.3):
            thread, box = self._run_in_thread(
                lambda: motores._stream_process(
                    [sys.executable, "-c", script], 60,
                    lambda line: ready.set() if line == "ready" else None, stop=stop,
                )
            )
            self.assertTrue(ready.wait(5.0))
            stop.request()
            thread.join(5.0)
        self.assertFalse(thread.is_alive(), "SIGKILL no llegó")
        self.assertEqual(box["result"].failure, "stopped")

    def test_a_child_that_already_finished_is_its_own_outcome(self):
        """Regla 4: si la carga terminó antes del pedido, no hay «stopped»."""
        from pc_ai_monitor.blocks import motores

        stop = motores.StopRequest()
        result = motores._stream_process(["true"], 10, None, stop=stop)
        stop.request()
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.failure)

    def test_the_real_run_manager_stops_its_own_child(self):
        import time

        from pc_ai_monitor.blocks import motores

        stop = motores.StopRequest()
        with mock.patch.object(motores, "MANAGER", "sleep"):
            thread, box = self._run_in_thread(
                lambda: _original_run_manager()(("30",), 60, stop=stop)
            )
            time.sleep(0.3)
            stop.request()
            thread.join(5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(box["result"].failure, "stopped")


class InlineStopButtonTests(_GtkCase):
    """El bloque en línea: «Detener» sólo en cargas que lo ofrecen."""

    def _open(self, inline):
        dialogs = dialogos.presented_dialogs(lambda: inline._stop_button.emit("clicked"))
        for dialog in dialogs:
            self.addCleanup(dialogos.close, dialog)
        self.assertEqual(len(dialogs), 1, "Detener no abrió su confirmación")
        return dialogs[0]

    def _load(self, stopper):
        inline = self.progreso.ProgressInline("engine:abito")
        activity = pl.engine_action("abito", "ensure")
        activity.stopper = stopper
        self.progreso.HUB.begin(activity)
        return inline, activity

    def test_no_detener_without_a_stopper(self):
        inline = self.progreso.ProgressInline("engine:abito")
        self.progreso.HUB.begin(pl.engine_action("abito", "ensure"))
        self.assertNotIn("Detener", _labels(inline))

    def test_detener_asks_like_apagar_and_cancel_does_nothing(self):
        calls = []
        inline, activity = self._load(lambda: calls.append(1))
        self.assertIn("Detener", _labels(inline))
        dialog = self._open(inline)
        title, body = pl.stop_confirm_text("abito")
        self.assertIsInstance(dialog, Gtk.MessageDialog)
        self.assertEqual(dialog.get_property("text"), title)
        self.assertEqual(dialog.get_property("secondary-text"), body)
        self.assertEqual(dialogos.button_label(dialog, Gtk.ResponseType.CANCEL), "Cancelar")
        self.assertEqual(dialogos.button_label(dialog, Gtk.ResponseType.ACCEPT), "Detener")
        dialog.response(Gtk.ResponseType.CANCEL)
        dialogos.pump(0.1)
        self.assertEqual(calls, [])
        self.assertTrue(activity.running)
        self.assertTrue(inline._stop_button.get_sensitive())

    def test_confirm_stops_once_and_disables_the_button(self):
        calls = []
        inline = self._load(lambda: calls.append(1))[0]
        dialog = self._open(inline)
        dialog.response(Gtk.ResponseType.ACCEPT)
        dialogos.pump(0.1)
        self.assertEqual(calls, [1])
        self.assertFalse(inline._stop_button.get_sensitive())
        self.assertIn("Deteniendo la carga…", _labels(inline))
        inline._stop_button.emit("clicked")  # desactivado: nada nuevo
        dialogos.pump(0.1)
        self.assertEqual(calls, [1])

    def test_completion_while_the_dialog_is_open_sends_nothing(self):
        calls = []
        inline, activity = self._load(lambda: calls.append(1))
        dialog = self._open(inline)
        activity.succeed("Abito encendido")
        self.progreso.HUB.changed()
        dialog.response(Gtk.ResponseType.ACCEPT)
        dialogos.pump(0.1)
        self.assertEqual(calls, [])
        self.assertEqual(activity.progress().state, pl.SUCCESS)

    def test_stopped_reads_detenido_and_has_no_retry(self):
        inline, activity = self._load(lambda: None)
        activity.request_stop()
        activity.halt(pl.STOPPED_MESSAGE, "Ningún motor quedó cargado.")
        self.progreso.HUB.changed()
        texts = _labels(inline)
        self.assertTrue(inline.get_visible())
        self.assertTrue(inline.has_css_class("act-stop"))
        self.assertIn("Detenido", texts)
        self.assertIn("Ningún motor quedó cargado.", texts)
        self.assertNotIn("Reintentar", texts)
        self.assertNotIn("Detener", texts)

    def test_header_indicator_clears_when_stopped(self):
        indicator = self.progreso.HeaderIndicator(lambda *_a: None)
        _inline, activity = self._load(lambda: None)
        self.progreso.HUB.changed()
        self.assertTrue(indicator.get_visible())
        activity.halt(pl.STOPPED_MESSAGE)
        self.progreso.HUB.changed()
        self.assertFalse(indicator.get_visible())

    def test_stopped_toast_says_carga_detenida(self):
        application = ShellIndicatorTests._app(self)
        application._toasts = Adw.ToastOverlay()
        application._watch_activities()
        shown = []
        application._toasts.add_toast = shown.append  # type: ignore[method-assign]
        _inline, activity = self._load(lambda: None)
        activity.halt(pl.STOPPED_MESSAGE)
        self.progreso.HUB.changed()
        self.assertEqual([toast.get_title() for toast in shown], ["Carga detenida"])


class DetenerOnlyForLoadsTests(_GtkCase):
    """Regla 1: nunca en actualizaciones ni en apagados."""

    def test_motores_ensure_abito_offers_detener_and_stop_does_not(self):
        from pc_ai_monitor.blocks import motores

        held = _aislamiento.HeldLoad(("ensure", "abito"), inventory=[_ABITO])
        self.addCleanup(held.finish)
        with mock.patch.object(motores, "_run_manager", held.manager_args):
            block = motores.ModelsInventoryBlock()
            dialogos.pump_until(lambda: not block._refreshing)
            block._on_inventory_loaded([dict(_ABITO)], None)
            card = block._cards["abito"]
            card._start_button.emit("clicked")
            self.assertTrue(held.started.wait(5.0))
            dialogos.pump(0.1)
            self.assertIn("Detener", _labels(card._progress))
            held.finish()
            self.assertTrue(dialogos.pump_until(
                lambda: not _activity("engine:abito").running
            ))
        # «Apagar» (un apagado): sin Detener.
        self.progreso.HUB.reset()
        inline = self.progreso.ProgressInline("engine:abito")
        block._request_action("abito", "stop")
        dialogos.pump(0.05)
        self.assertNotIn("Detener", _labels(inline))

    def test_updates_never_offer_detener(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        results = json.loads((_FIXTURES / "update_results_real_2026-11-04.json").read_text())
        runner = StreamingRunner(
            {("update", "engine-abito"): ["[ 12%] Building C object ggml.c.o"], ("updates",): []},
            {
                ("update", "engine-abito"): _cli(0, json.dumps(results["success_applied"]["stdout"])),
                ("updates",): _cli(0, (_FIXTURES / "updates_real_2026-11-04.json").read_text()),
            },
            hold=hold,
        )
        block = ActualizacionesProgressTests._block(self, runner)
        row = next(r for r in block._view.rows if r.id == "engine-abito")
        block._begin([(row, True)])
        self.assertTrue(runner.emitted.wait(5.0))
        dialogos.pump(0.1)
        inline = block._rows["engine-abito"]["progress"]
        self.assertTrue(inline.get_visible())
        self.assertNotIn("Detener", _labels(inline))
        hold.set()
        dialogos.pump_until(lambda: not block._busy)

    def test_engine_update_never_offers_detener(self):
        from pc_ai_monitor.blocks import home

        hold = threading.Event()
        self.addCleanup(hold.set)

        def runner(args, timeout=None, **_kw):
            if tuple(args)[:2] == ("engine", "update"):
                hold.wait(5.0)
                return _cli(0, '{"verified": true}')
            return _cli(1, "", "x")

        with mock.patch.object(home, "run_manager", runner):
            card = home.UpdateCardBlock(poller=_FakePoller())
            card._begin_update()
            dialogos.pump(0.1)
            self.assertTrue(card._progress.get_visible())
            self.assertNotIn("Detener", _labels(card._progress))
            hold.set()
            dialogos.pump_until(lambda: not _activity("engine-update").running)


class MotoresDetenerTests(_GtkCase):
    """Motores «Encender» Abito → Detener → ``stop abito`` → «Detenido»."""

    def _start(self, held):
        from pc_ai_monitor.blocks import motores

        patcher = mock.patch.object(motores, "_run_manager", held.manager_args)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(held.finish)
        block = motores.ModelsInventoryBlock()
        dialogos.pump_until(lambda: not block._refreshing)
        block._on_inventory_loaded([dict(_ABITO)], None)
        card = block._cards["abito"]
        card._start_button.emit("clicked")
        self.assertTrue(held.started.wait(5.0))
        dialogos.pump(0.05)
        return block, card

    def _confirm(self, card):
        dialogs = dialogos.presented_dialogs(
            lambda: card._progress._stop_button.emit("clicked")
        )
        for dialog in dialogs:
            self.addCleanup(dialogos.close, dialog)
        self.assertEqual(len(dialogs), 1)
        start = len(_aislamiento.ATTEMPTS)
        dialogs[0].response(Gtk.ResponseType.ACCEPT)
        return start

    def test_confirm_terminates_the_load_and_stops_abito(self):
        held = _aislamiento.HeldLoad(
            ("ensure", "abito"), inventory=[_ABITO], resident=_RESIDENT_NONE
        )
        block, card = self._start(held)
        start = self._confirm(card)
        activity = _activity("engine:abito")
        self.assertTrue(dialogos.pump_until(lambda: not activity.running))
        self.assertIsNotNone(held.stop_seen[0])
        self.assertTrue(held.stop_seen[0].requested)
        sent = [a for a in _aislamiento.ATTEMPTS[start:] if a[1][:1] == ("stop",)]
        self.assertEqual(sent, [("local-ai-models", ("stop", "abito"))])
        self.assertEqual(activity.progress().state, pl.STOPPED)
        self.assertIn("Detenido", _labels(card._progress))
        self.assertEqual(card._state_label.get_text(), "detenido")
        self.assertTrue(card._start_button.get_sensitive())
        del block

    def test_a_failed_stop_route_ends_as_an_error_with_its_reason(self):
        held = _aislamiento.HeldLoad(
            ("ensure", "abito"), inventory=[_ABITO], resident=_RESIDENT_NONE, stop_rc=4
        )
        _block, card = self._start(held)
        self._confirm(card)
        activity = _activity("engine:abito")
        self.assertTrue(dialogos.pump_until(lambda: not activity.running))
        progress = activity.progress()
        self.assertEqual(progress.state, pl.ERROR)
        self.assertEqual(progress.message, "No se pudo detener Abito")
        self.assertIn("stop rechazado", progress.detail)


def _activity(key: str) -> pl.Activity:
    from pc_ai_monitor.blocks import progreso

    activity = progreso.HUB.get(key)
    assert activity is not None, f"sin actividad {key}"
    return activity


def _percent_of(key: str) -> float | None:
    """None until the worker has registered the activity (it is posted)."""
    from pc_ai_monitor.blocks import progreso

    activity = progreso.HUB.get(key)
    return activity.progress().percent if activity is not None else None


def _progress_of(key: str) -> pl.Progress:
    return _activity(key).progress()


class _FakePoller:
    def __init__(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory
        from pc_ai_monitor.blocks.home_logica import HomeState

        self.state = HomeState()
        self.history = MotorHistory()
        self.subscribers = []

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def refresh(self, _snapshot, force=False):
        del force

    def begin_update(self):
        self.state.updating = True

    def end_update(self, returncode, message, status=None):
        self.state.updating = False
        self.state.last_update_rc = returncode
        self.state.update_message = message

    def begin_switch(self, target):
        self.state.switching = target

    def end_switch(self, ok, message, info=None):
        del ok, message
        self.state.switching = None
        if info is not None:
            self.state.resident_info = info


_ABITO = {
    "model": "abito", "alias": "abito", "port": 11002, "ctx": 131072,
    "healthy": False, "problem": None, "capacity_reason": None,
}

_RESIDENT_NONE = {"resident": "none", "abito": {"healthy": False}, "gufo": {}}

_FIXTURES = Path(__file__).parent / "fixtures"


if __name__ == "__main__":
    unittest.main()
