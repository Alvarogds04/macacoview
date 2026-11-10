# pyright: reportAttributeAccessIssue=false
"""Page tests must never reach the live machine (manager, engine, gufo-flash)."""

import time
import unittest
from pathlib import Path

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
    import _sandbox
except ModuleNotFoundError:
    from tests import _aislamiento, _sandbox


class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")
        _aislamiento.isolate()

    def _spin(self, seconds: float) -> None:
        from gi.repository import GLib

        context = GLib.MainContext.default()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)

    def test_building_every_page_never_runs_the_real_manager(self):
        import subprocess
        from unittest import mock

        from pc_ai_monitor.pages.home import HomePage
        from pc_ai_monitor.pages.motores import MotoresPage
        from pc_ai_monitor.pages.router import RouterPage

        real_run = subprocess.run
        escaped: list = []

        def watch(argv, *args, **kwargs):
            name = str(argv[0]) if isinstance(argv, (list, tuple)) and argv else str(argv)
            if any(tool in name for tool in ("local-ai-models", "gufo-flash")):
                escaped.append(tuple(argv))
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(subprocess, "run", watch):
            pages = [HomePage(), MotoresPage(), RouterPage()]
            self._spin(1.5)

        self.assertEqual(escaped, [])
        self.assertTrue(pages)
        # The blocks did try: the guard answered instead of the machine.
        self.assertTrue(any(tool == "local-ai-models" for tool, _ in _aislamiento.ATTEMPTS))


class GufoFlashStopIsolationTests(unittest.TestCase):
    """L31: «apagar Gufo» runs ``gufo-flash stop --no-restore``; no test may
    reach the real gufo-flash through that route."""

    @classmethod
    def setUpClass(cls):
        _aislamiento.isolate()

    def test_gufo_flash_stop_is_refused_and_recorded(self):
        import subprocess
        from unittest import mock

        from pc_ai_monitor.blocks import motores

        spawned: list = []

        def watch(argv, *args, **kwargs):
            spawned.append(tuple(argv))
            raise AssertionError(f"spawned {argv}")

        start = len(_aislamiento.ATTEMPTS)
        with (
            mock.patch.object(subprocess, "run", watch),
            mock.patch.object(subprocess, "Popen", watch),
        ):
            result = motores.run_gufo_flash(("stop", "--no-restore"), 30)

        self.assertEqual(spawned, [])
        self.assertIsNone(result.returncode)
        self.assertIn(
            ("gufo-flash", ("stop", "--no-restore")), _aislamiento.ATTEMPTS[start:]
        )


class DetenerRoutesIsolationTests(unittest.TestCase):
    """S21: the «Detener» routes (a stoppable load, ``stop abito``,
    ``gufo-flash stop --no-restore``) are recorded and refused, never spawned."""

    @classmethod
    def setUpClass(cls):
        _aislamiento.isolate()

    def test_stoppable_load_and_stop_routes_never_spawn(self):
        import subprocess
        from unittest import mock

        from pc_ai_monitor.blocks import (
            home,
            motores,
            motores_vista,
            progreso,
            residente,
        )

        def watch(argv, *args, **kwargs):
            raise AssertionError(f"spawned {argv}")

        start = len(_aislamiento.ATTEMPTS)
        stop = motores.StopRequest()
        with (
            mock.patch.object(subprocess, "run", watch),
            mock.patch.object(subprocess, "Popen", watch),
        ):
            results = [
                motores._run_manager("ensure", "abito", stop=stop),
                motores.run_manager(("switch", "gufo"), 900, stop=stop),
                home._run_manager("ensure", "abito", stop=stop),
                home.run_manager(("switch", "gufo"), 900, stop=stop),
                residente.run_manager(("switch", "gufo"), 900, stop=stop),
                motores_vista.run_manager(("switch", "gufo"), 900, stop=stop),
                progreso.run_stop_route("abito", home._run_manager),
                progreso.run_stop_route("gufo", home._run_manager),
            ]

        self.assertTrue(all(result.returncode is None for result in results))
        recorded = _aislamiento.ATTEMPTS[start:]
        self.assertIn(("local-ai-models", ("stop", "abito")), recorded)
        self.assertIn(("gufo-flash", ("stop", "--no-restore")), recorded)
        self.assertEqual(recorded.count(("local-ai-models", ("switch", "gufo"))), 4)


class ConfigIsolationTests(unittest.TestCase):
    """Tests must never write the user's real ~/.config/macacoview/config.toml.

    A SettingsSection leaves a 600 ms apply timer armed; any later test that
    iterates the GTK main loop fires it, and the apply saves the collected
    config. Without the guard that save went to the user's real file.
    """

    REAL_DIRS = (
        Path(_sandbox.REAL_HOME) / ".config" / "macacoview",
        Path(_sandbox.REAL_HOME) / ".config" / "pc-ai-monitor",
        Path(_sandbox.REAL_HOME) / ".local" / "share" / "applications",
    )

    @classmethod
    def setUpClass(cls):
        _aislamiento.isolate()

    def _is_real(self, path) -> bool:
        resolved = Path(path).expanduser().resolve()
        return any(resolved.is_relative_to(base.resolve()) for base in self.REAL_DIRS)

    def test_config_paths_point_to_a_sandbox(self):
        from pc_ai_monitor import config as cfg

        self.assertFalse(self._is_real(cfg.CONFIG_PATH), cfg.CONFIG_PATH)
        self.assertFalse(self._is_real(cfg.LEGACY_CONFIG_PATH), cfg.LEGACY_CONFIG_PATH)

    def test_a_pending_settings_apply_never_reaches_the_real_file(self):
        from unittest import mock

        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk

        if not Gtk.init_check():
            self.skipTest("sin display (usá xvfb-run)")

        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.settings import SettingsSection

        real_write = Path.write_text
        blocked: list[Path] = []

        def guarded_write(path, *args, **kwargs):
            if self._is_real(path):
                blocked.append(path)
                return 0
            return real_write(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", guarded_write):
            section = SettingsSection(cfg.Config(), lambda updated: None)
            section._theme_row.set_selected(3)
            deadline = time.monotonic() + 1.5
            context = GLib.MainContext.default()
            while time.monotonic() < deadline:
                context.iteration(False)
                time.sleep(0.01)

        self.assertEqual(blocked, [])
