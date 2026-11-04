# pyright: reportAttributeAccessIssue=false
"""Page tests must never reach the live machine (manager, engine, gufo-flash)."""

import time
import unittest

import _aislamiento


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
