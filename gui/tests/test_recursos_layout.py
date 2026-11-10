# pyright: reportAttributeAccessIssue=false
"""Recursos layout with Pi open (S17, T14 follow-up of odd/tasks/macacoview-gtk.md).

Design 08 tab 4: tapping the Pi group opens the sessions list as a wide card
on the left with the groups card narrowed to 250 px on its right
(``.rec-grid { grid-template-columns: 1fr 250px }``); every other group keeps
opening its top-10 process list inside the groups card, and closing Pi puts
the page back in its two columns. Checked on the real page after a layout
pass, never on properties alone:

    xvfb-run -a python3 -m unittest tests.test_recursos_layout -v
"""

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

try:
    import test_pi_sesiones as pi_fixture
except ModuleNotFoundError:
    from tests import test_pi_sesiones as pi_fixture

import unittest

from gi.repository import GLib, Gtk

_aislamiento.isolate()

GB = 1024 * 1024  # KiB


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _walk(widget):
    stack = [widget]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(_children(node))


def _texts(widget):
    return [n.get_text() for n in _walk(widget) if isinstance(n, Gtk.Label)]


class RecursosPiLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")
        cls.sessions, cls.home = pi_fixture.live_like_sessions()

    def snapshot(self, sessions=None):
        from pc_ai_monitor.datos import Snapshot

        firefox = [
            {
                "pid": 7000 + n,
                "comm": f"firefox-tab-{n:02d}",
                "rss_kb": (20 - n) * 50 * 1024,
                "cpu": 1.0,
                "args": "firefox",
            }
            for n in range(12)
        ]
        stats = {
            "memory": {"total_gib": 128.0, "used_gib": 40.0, "available_gib": 88.0},
            "groups": {
                "pi": {"rss_gib": 6.34, "pids": [5544]},
                "firefox": {"rss_gib": 3.1, "pids": [p["pid"] for p in firefox]},
                "other": {"rss_gib": 2.0, "pids": [1]},
            },
            "processes": [
                {"pid": 5544, "comm": "pi", "rss_kb": GB, "cpu": 0.0, "args": "pi"},
                {"pid": 1, "comm": "systemd", "rss_kb": 10, "cpu": 0.0, "args": "init"},
                *firefox,
            ],
            "pi_sessions": self.sessions if sessions is None else sessions,
        }
        return Snapshot(stats=stats)

    def setUp(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock
        from pc_ai_monitor.blocks.memoria import MemoryBlock
        from pc_ai_monitor.blocks.modelos import ModelsBlock
        from pc_ai_monitor.pages.recursos import RecursosPage

        self.page = RecursosPage()
        self.window = Gtk.Window()
        self.window.set_default_size(1180, 900)
        self.window.set_child(self.page)
        self.window.present()
        self.addCleanup(self.window.destroy)
        self.page.update_snapshot(self.snapshot())
        self.settle()
        blocks = self.page._blocks
        self.groups = next(b for b in blocks if isinstance(b, GroupsBlock))
        self.memory = next(b for b in blocks if isinstance(b, MemoryBlock))
        self.models = next(b for b in blocks if isinstance(b, ModelsBlock))

    def settle(self):
        # A re-parented widget is only allocated on a later frame, and under a
        # loaded machine that can take well over a fixed 300 ms (L39
        # R3-settle-wallclock). Run frames until every block has a non-zero
        # allocation that stayed the same for 150 ms, with a 5 s ceiling.
        context = GLib.MainContext.default()
        start = GLib.get_monotonic_time()
        last, stable_since = None, start
        while True:
            context.iteration(False)
            now = GLib.get_monotonic_time()
            shape = tuple(
                (block.get_width(), block.get_height(), block.get_parent() is not None)
                for block in self.page._blocks
            )
            if shape != last:
                last, stable_since = shape, now
            settled = all(w > 0 and h > 0 for w, h, _ in shape)
            if settled and now - stable_since >= 150_000 and now - start >= 100_000:
                return
            if now - start >= 5_000_000:
                return

    def shown_session_views(self):
        from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

        return [
            node
            for node in _walk(self.page)
            if isinstance(node, PiSessionsView) and node.get_mapped()
        ]

    def assert_two_columns(self):
        self.assertEqual(self.shown_session_views(), [])
        right = self.groups.get_parent()
        self.assertIs(self.models.get_parent(), right)
        self.assertIs(right.get_first_child(), self.groups)
        columns = right.get_parent()
        self.assertTrue(columns.get_homogeneous())
        self.assertEqual(list(_children(columns)), [self.memory.get_parent(), right])
        # Groups back at half width, in the right column.
        self.assertGreater(self.groups.get_width(), 400)
        ok, point = self.groups.compute_point(self.page, _point(0, 0))
        self.assertTrue(ok)
        self.assertGreater(point.x, self.page.get_width() / 3)

    # -- (1) Pi open: wide sessions card | groups card at 250 px ----------

    def test_opening_pi_shows_wide_sessions_card_beside_250px_groups(self):
        self.groups._toggle("pi")
        self.settle()
        views = self.shown_session_views()
        self.assertEqual(len(views), 1, "the sessions list appears exactly once")
        view = views[0]
        self.assertTrue(view.has_css_class("card"), "the wide list is framed")
        row = view.get_parent()
        self.assertIs(self.groups.get_parent(), row)
        ok, view_at = view.compute_point(self.page, _point(0, 0))
        ok2, groups_at = self.groups.compute_point(self.page, _point(0, 0))
        self.assertTrue(ok and ok2)
        # Same row, sessions on the left, groups on the right.
        self.assertEqual(round(view_at.y), round(groups_at.y))
        self.assertLess(view_at.x + view.get_width(), groups_at.x)
        self.assertEqual(self.groups.get_width(), 250)
        self.assertGreater(view.get_width(), 600)
        # No blank band under the row: it is as tall as its tallest card.
        tallest = max(view.get_height(), self.groups.get_height())
        self.assertLessEqual(row.get_height(), tallest + 1)
        # Nothing of the list inside the groups card itself.
        self.assertNotIn("SESIÓN", _texts(self.groups))
        # Memory/History and Models stay visible below.
        for block in (self.memory, self.models):
            self.assertTrue(block.get_mapped())
        ok, models_at = self.models.compute_point(self.page, _point(0, 0))
        self.assertTrue(ok)
        self.assertGreater(models_at.y, view_at.y + view.get_height())

    # -- (2) closing Pi restores the two columns -------------------------

    def test_closing_pi_restores_the_two_columns(self):
        self.assert_two_columns()
        self.groups._toggle("pi")
        self.settle()
        self.groups._toggle("pi")
        self.settle()
        self.assert_two_columns()
        self.assertNotIn("SESIÓN", _texts(self.page))

    # -- (3) other groups keep their top 10 inside the groups card --------

    def test_other_group_keeps_two_columns_and_its_top10(self):
        for first in ("", "pi"):
            with self.subTest(after=first or "nothing"):
                if first:
                    self.groups._toggle(first)
                    self.settle()
                self.groups._toggle("firefox")
                self.settle()
                self.assert_two_columns()
                texts = _texts(self.groups)
                self.assertIn("PROCESOS DE 🦊 FIREFOX", texts)
                tabs = [t for t in texts if t.startswith("firefox-tab-")]
                self.assertEqual(len(tabs), 10)
                self.assertTrue(self.groups._detail.get_mapped())
                self.groups._toggle("firefox")
                self.settle()

    # -- (4) a new snapshot keeps the wide list fresh ----------------------

    def test_new_snapshot_updates_the_wide_list(self):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        self.groups._toggle("pi")
        self.settle()
        before = self.shown_session_views()[0]
        self.assertEqual(len(before._rows), 11)
        one = [s for s in self.sessions if s["pid"] == 277162]
        self.page.update_snapshot(self.snapshot(one))
        self.settle()
        views = self.shown_session_views()
        self.assertEqual(len(views), 1)
        self.assertEqual(len(views[0]._rows), 1)
        self.assertIn(logica.mib(one[0]["pss_kb"]), _texts(views[0]))
        self.assertEqual(self.groups.get_width(), 250)


def _point(x: float, y: float):
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    point = Graphene.Point()
    point.x, point.y = x, y
    return point


if __name__ == "__main__":
    unittest.main()
