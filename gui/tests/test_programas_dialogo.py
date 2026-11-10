# pyright: reportAttributeAccessIssue=false
"""Short «RAM por programa» + «Agregar programa…» (S19, T16 of odd/tasks/macacoview-gtk.md).

Configuración no longer lists every discovered program: it shows the pinned
programs (human name, live memory, «Quitar»), an empty state, and an
«Agregar programa…» dialog with a search box and the 15 largest by RAM.

The snapshot is rebuilt from a real collector capture (``pc-ai-stats``,
2026-11-10, 647 programs: 149 with a readable executable, 498 «comm»): the
60 programs of 50 MiB or more are copied below (name, executable, RSS kB,
process count; the home directory renamed), the 587 smaller ones are
generated with the same identity split and RSS below 50 MiB. IDs are
synthetic: the collector's are opaque hashes.
"""

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

import hashlib
import unittest

from pc_ai_monitor.blocks import programas_vista as programas
from pc_ai_monitor.datos import Snapshot

_aislamiento.isolate()

# (name, executable or None = «comm», rss_kb, processes) — real, 2026-11-10.
REAL_LARGE = (
    ("node", "/usr/bin/node", 7475344, 8),
    ("firefox", "/snap/firefox/9137/usr/lib/firefox/firefox", 3558528, 14),
    ("python3.14", "/usr/bin/python3.14", 1712784, 8),
    (
        "chrome",
        "/home/user/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
        1608120,
        15,
    ),
    ("node", "/usr/bin/node", 1202412, 8),
    (
        "node",
        "/usr/local/lib/node_modules/@colbymchenry/codegraph/node_modules/@colbymchenry/codegraph-linux-x64/node",
        941600,
        10,
    ),
    ("node", "/usr/bin/node", 873996, 4),
    ("node", "/usr/bin/node", 816320, 8),
    ("node", "/usr/bin/node", 807952, 8),
    ("node", "/usr/bin/node", 790688, 8),
    ("node", "/usr/bin/node", 759972, 8),
    ("node", "/usr/bin/node", 698304, 8),
    ("node", "/usr/bin/node", 591300, 8),
    ("rustdesk", "/usr/share/rustdesk/rustdesk", 523020, 1),
    ("python3.13", "/usr/bin/python3.13", 482792, 1),
    ("node", "/usr/bin/node", 478912, 8),
    ("node", "/usr/bin/node", 466976, 8),
    ("node", "/usr/bin/node", 425244, 2),
    (
        "python3.12",
        "/home/user/.local/share/uv/python/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12",
        421056,
        1,
    ),
    (
        "node",
        "/usr/local/lib/node_modules/@colbymchenry/codegraph/node_modules/@colbymchenry/codegraph-linux-x64/node",
        371168,
        7,
    ),
    ("gnome-shell", "/usr/bin/gnome-shell", 310132, 1),
    ("marksman", "/home/user/.pi-lens/bin/marksman", 280420, 3),
    ("node", "/usr/bin/node", 276720, 2),
    ("java", "/opt/java/openjdk/bin/java", 272524, 1),
    ("python3.14", "/usr/bin/python3.14", 250156, 1),
    ("gjs-console", "/usr/bin/gjs-console", 236832, 3),
    ("node", "/usr/bin/node", 236496, 2),
    (
        "gnome-remote-desktop-daemon",
        "/usr/libexec/gnome-remote-desktop-daemon",
        209064,
        2,
    ),
    (
        "opengrep-core",
        "/home/user/.cache/opengrep/v1.33.1/semgrep/bin/opengrep-core",
        205660,
        1,
    ),
    ("engram", "/home/user/.local/bin/engram", 195776, 9),
    ("gnome-shell", None, 188940, 1),
    ("rustdesk", None, 165556, 3),
    ("nautilus", "/usr/bin/nautilus", 162876, 1),
    ("python3.14", "/usr/bin/python3.14", 162420, 1),
    ("node", "/usr/bin/node", 153860, 2),
    ("nginx", None, 144704, 33),
    (
        "python3.13",
        "/home/user/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13",
        143984,
        1,
    ),
    ("postgres", "/usr/local/bin/postgres", 135672, 10),
    ("vikunja", "/home/user/.local/bin/vikunja", 131932, 1),
    ("xdg-desktop-portal-gnome", "/usr/libexec/xdg-desktop-portal-gnome", 118480, 1),
    ("ghostty", "/snap/ghostty/1016/bin/ghostty", 112900, 1),
    ("node", "/usr/bin/node", 107576, 1),
    ("postgres", None, 102760, 11),
    ("systemd-journal", None, 94284, 1),
    ("herdr", "/home/user/.local/bin/herdr", 89896, 2),
    ("xdg-desktop-por", None, 86996, 3),
    (
        "python3.11",
        "/home/user/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11",
        76156,
        2,
    ),
    ("Xvfb", "/usr/bin/Xvfb", 70712, 1),
    ("php-fpm8.5", None, 65436, 3),
    ("prometheus", "/home/user/.local/lib/prometheus-3.14.0/prometheus", 63172, 1),
    ("pipewire", "/usr/bin/pipewire", 61916, 3),
    ("syncthing", "/usr/bin/syncthing", 61036, 2),
    ("gjs", None, 60684, 2),
    ("localsearch-3", "/usr/libexec/localsearch-3", 60672, 1),
    ("zsh", "/usr/bin/zsh", 60168, 9),
    ("journalctl", None, 57612, 2),
    (
        "evolution-alarm-notify",
        "/usr/libexec/evolution-data-server/evolution-alarm-notify",
        57508,
        1,
    ),
    (
        "python3.11",
        "/home/user/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11",
        55048,
        1,
    ),
    ("dash", "/usr/bin/dash", 51676, 25),
    ("snapd", None, 51520, 1),
)
SMALL_COMM = 488  # 498 «comm» in the capture, 10 of them listed above
SMALL_EXECUTABLE = 99  # 149 with an executable, 50 listed above
MIB = 1024


def _key(index: int) -> str:
    return "program:v1:" + hashlib.sha256(f"t16-{index}".encode()).hexdigest()


def _program(index, name, executable, rss_kb, processes):
    return {
        "id": _key(index),
        "name": name,
        "executable": executable,
        "identity_source": "executable" if executable else "comm",
        "pids": list(range(1000 + index * 40, 1000 + index * 40 + processes)),
        "cpu": 0.0,
        "rss_kb": rss_kb,
        "pss_kb": None,
        "memory_kb": rss_kb,
        "memory_metric": "rss",
        "coverage": {
            "status": "not_sampled",
            "total": processes,
            "measured": 0,
            "unreadable": 0,
            "raced": 0,
            "not_sampled": processes,
        },
    }


def real_programs() -> list[dict]:
    """The 647 programs of the capture (large ones verbatim, tail generated)."""
    programs = [_program(i, *row) for i, row in enumerate(REAL_LARGE)]
    index = len(programs)
    for n in range(SMALL_COMM):
        programs.append(_program(index, f"kthread-{n}", None, 49 * MIB - n * 90, 1))
        index += 1
    for n in range(SMALL_EXECUTABLE):
        programs.append(
            _program(
                index, f"helper-{n}", f"/usr/libexec/helper-{n}", 48 * MIB - n * 400, 1
            )
        )
        index += 1
    return programs


def real_snapshot(programs=None) -> Snapshot:
    programs = real_programs() if programs is None else programs
    total = sum(len(p["pids"]) for p in programs)
    return Snapshot(
        stats={
            "memory": {"total_gib": 122.0, "used_gib": 70.0, "available_gib": 52.0},
            "programs": programs,
            "program_coverage": {
                "status": "not_sampled",
                "total": total,
                "measured": 0,
                "unreadable": 0,
                "raced": 0,
                "not_sampled": total,
            },
            "program_enumeration": {"complete": True, "process_count": total},
        }
    )


def by_name(programs, name, executable=True):
    return next(
        p for p in programs if p["name"] == name and bool(p["executable"]) is executable
    )


class FixtureTests(unittest.TestCase):
    def test_fixture_matches_the_capture_shape(self):
        programs = real_programs()
        self.assertEqual(len(programs), 647)
        comm = [p for p in programs if p["identity_source"] == "comm"]
        self.assertEqual(len(comm), 498)
        self.assertEqual(len({p["id"] for p in programs}), 647)
        self.assertEqual(sum(1 for p in programs if p["rss_kb"] >= 50 * MIB), 60)


# -- naming (pure) ------------------------------------------------------------


def _named(name, executable, key_char="a", **extra):
    program = {
        "id": "program:v1:" + key_char * 64,
        "name": name,
        "executable": executable,
        "identity_source": "executable" if executable else "comm",
        "rss_kb": 300 * MIB,
        "memory_kb": 300 * MIB,
        "memory_metric": "rss",
        "coverage": {"status": "not_sampled", "total": 1},
    }
    program.update(extra)
    return program


class NamingTests(unittest.TestCase):
    """Rules 2, 3, 5 and 6 of proposal 10 section N (1 and 4 deferred, L34)."""

    def test_browsers_by_executable_path(self):
        for name, executable, human in (
            ("firefox", "/snap/firefox/9137/usr/lib/firefox/firefox", "Firefox"),
            (
                "chrome",
                "/snap/chromium/3200/usr/lib/chromium-browser/chrome",
                "Chromium",
            ),
            (
                "chrome",
                "/home/user/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
                "Chromium (Playwright)",
            ),
            ("chrome", "/opt/google/chrome/chrome", "Chrome"),
            ("google-chrome-s", "/usr/bin/google-chrome-stable", "Chrome"),
        ):
            with self.subTest(executable=executable):
                program = _named(name, executable)
                self.assertEqual(programas.human_name(program), human)
                self.assertIn("navegador", programas.program_kind(program))

    def test_known_apps_by_alias(self):
        for name, executable, human in (
            ("rustdesk", "/usr/share/rustdesk/rustdesk", "RustDesk"),
            ("gnome-shell", "/usr/bin/gnome-shell", "GNOME Shell"),
            ("update-manager", "/usr/bin/update-manager", "Gestor de actualizaciones"),
            ("Xwayland", "/usr/bin/Xwayland", "Xwayland"),
            ("gnome-software", "/usr/bin/gnome-software", "Software de GNOME"),
            ("nautilus", "/usr/bin/nautilus", "Archivos (Nautilus)"),
            ("code", "/usr/share/code/code", "VS Code"),
        ):
            with self.subTest(name=name):
                program = _named(name, executable)
                self.assertEqual(programas.human_name(program), human)
                self.assertNotIn("sin nombre conocido", programas.program_kind(program))

    def test_unreadable_executable_is_flagged_as_system(self):
        program = _named("nginx", None)
        self.assertEqual(programas.human_name(program), "nginx · aaaaaaaa")
        self.assertIn("inaccesible", programas.program_kind(program))
        self.assertTrue(programas.is_minor(program))
        # A known app keeps its name but still says where it comes from.
        shell = _named("gnome-shell", None)
        self.assertEqual(programas.human_name(shell), "GNOME Shell")
        self.assertIn("inaccesible", programas.program_kind(shell))

    def test_fallback_is_name_and_short_id_full_id_only_in_tooltip(self):
        program = _named("node", "/usr/bin/node", key_char="7")
        key = program["id"]
        self.assertEqual(programas.human_name(program), "node · 77777777")
        self.assertIn("sin nombre conocido", programas.program_kind(program))
        tooltip = programas.program_tooltip(program)
        self.assertIn(key, tooltip)
        self.assertIn("/usr/bin/node", tooltip)
        self.assertNotIn(key, programas.human_name(program))

    def test_names_never_carry_arguments_nor_the_separator(self):
        program = _named("ed|it", "/opt/x/ed|it", args="--token SECRET")
        text = programas.human_name(program) + programas.program_kind(program)
        self.assertNotIn("|", programas.human_name(program))
        self.assertNotIn("SECRET", text + programas.program_tooltip(program))

    def test_memory_text_pss_rss_and_small_values(self):
        pss = _named("x", "/x", memory_metric="pss", memory_kb=int(3.42 * 1024**2))
        self.assertEqual(programas.memory_text(pss), "3.42 GiB PSS")
        rss = _named("x", "/x", memory_kb=300 * MIB)
        self.assertEqual(programas.memory_text(rss), "300 MiB RSS")
        self.assertIsNone(programas.memory_text(None))

    def test_search_is_case_and_accent_insensitive_on_name_and_path(self):
        nautilus = _named("nautilus", "/usr/bin/nautilus")
        codegraph = _named(
            "node",
            "/usr/local/lib/node_modules/@colbymchenry/codegraph/node",
            key_char="b",
        )
        updates = _named("update-manager", "/usr/bin/update-manager", key_char="c")
        for query, program in (
            ("NÁUTILUS", nautilus),
            ("archivos", nautilus),
            ("CodeGraph", codegraph),
            ("node", codegraph),
            ("actualización", updates),
        ):
            with self.subTest(query=query):
                self.assertTrue(programas.matches(program, query))
        self.assertFalse(programas.matches(nautilus, "firefox"))


class CandidateTests(unittest.TestCase):
    def test_default_is_the_15_largest_non_system_of_50_mib_or_more(self):
        programs = real_programs()
        shown, total = programas.candidates(programs)
        expected = sorted(
            (
                p
                for p in programs
                if p["identity_source"] == "executable" and p["rss_kb"] >= 50 * MIB
            ),
            key=lambda p: -p["rss_kb"],
        )[:15]
        self.assertEqual([p["id"] for p in shown], [p["id"] for p in expected])
        self.assertEqual(total, 50)

    def test_query_returns_every_match_capped_at_50(self):
        programs = real_programs()
        shown, total = programas.candidates(programs, "node")
        self.assertEqual(len(shown), total)
        self.assertGreater(total, 15)
        shown, total = programas.candidates(programs, "e", include_all=True)
        self.assertEqual(len(shown), programas.SEARCH_CAP)
        self.assertGreater(total, programas.SEARCH_CAP)
        self.assertEqual(shown, sorted(shown, key=lambda p: -p["rss_kb"]))


# -- GTK -----------------------------------------------------------------------


def _drain():
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class _GtkCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Adw, Gtk

            if not Gtk.init_check():
                raise unittest.SkipTest("sin display (usá xvfb-run)")
            Adw.init()
        except (ImportError, ValueError) as error:  # pragma: no cover
            raise unittest.SkipTest(f"PyGObject no disponible: {error}") from error

    def _page(self, pins=(), programs=None):
        from unittest.mock import patch

        from gi.repository import Adw, GLib

        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.settings import SettingsSection

        saver = patch.object(cfg, "save", return_value=True)
        self.save = saver.start()
        self.addCleanup(saver.stop)
        page = SettingsSection(
            cfg.Config(bar_program_ids=tuple(pins)),
            lambda updated: None,
            snapshot=real_snapshot(programs),
        )
        # Like the app (Adw.ApplicationWindow): the dialog is hosted inside
        # the window, not as a floating toplevel.
        window = Adw.Window()
        window.set_content(page)

        def teardown():
            if page._pending:
                GLib.source_remove(page._pending)
                page._pending = None
            dialog = getattr(page, "_add_dialog", None)
            if dialog is not None:
                dialog.force_close()
            _drain()
            window.destroy()
            _drain()

        self.addCleanup(teardown)
        return page

    @staticmethod
    def _descendants(widget):
        """Visible descendants (hidden branches skipped), in screen order."""
        stack = [widget]
        while stack:
            current = stack.pop()
            if not current.get_visible():
                continue
            yield current
            child = current.get_last_child()
            while child is not None:
                stack.append(child)
                child = child.get_prev_sibling()

    def _rows(self, group):
        from gi.repository import Adw

        return [
            w for w in self._descendants(group) if isinstance(w, Adw.PreferencesRow)
        ]

    def _texts(self, widget):
        from gi.repository import Adw, Gtk

        found = []
        for w in self._descendants(widget):
            if isinstance(w, Gtk.Label):
                found.append(w.get_text())
            elif isinstance(w, Gtk.Button) and w.get_label():
                found.append(w.get_label())
            elif isinstance(w, Adw.PreferencesRow):
                found.append(w.get_title())
        return found

    def _buttons(self, widget, label):
        from gi.repository import Gtk

        return [
            w
            for w in self._descendants(widget)
            if isinstance(w, Gtk.Button) and w.get_label() == label
        ]


class ShortSettingsTests(_GtkCase):
    def test_group_is_short_with_650_programs(self):
        page = self._page()
        group = page._program_group
        self.assertLessEqual(len(self._rows(group)), 2)
        _minimum, natural, _b1, _b2 = group.measure(
            __import__("gi").repository.Gtk.Orientation.VERTICAL, 800
        )
        self.assertLess(natural, 600)

    def test_empty_state_offers_the_dialog(self):
        page = self._page()
        texts = " ".join(self._texts(page._program_group))
        self.assertIn("Todavía no fijaste ningún programa", texts)
        buttons = self._buttons(page._program_group, "Agregar programa…")
        self.assertEqual(len(buttons), 1)
        buttons[0].emit("clicked")
        self.assertIsNotNone(page._add_dialog)

    def test_pinned_rows_show_name_memory_and_quitar(self):
        programs = real_programs()
        firefox = by_name(programs, "firefox")
        rustdesk = by_name(programs, "rustdesk")
        node = by_name(programs, "node")
        firefox["memory_metric"] = "pss"
        firefox["memory_kb"] = int(3.42 * 1024**2)
        page = self._page((firefox["id"], rustdesk["id"], node["id"]), programs)
        group = page._program_group
        rows = self._rows(group)
        # three pinned + «Agregar programa…»
        self.assertEqual(len(rows), 4)
        texts = self._texts(group)
        self.assertIn("Firefox", texts)
        self.assertIn("RustDesk", texts)
        self.assertIn(f"node · {node['id'][-64:-56]}", texts)
        self.assertIn("3.42 GiB PSS", texts)
        self.assertIn("511 MiB RSS", texts)
        self.assertNotIn("Todavía no fijaste ningún programa", " ".join(texts))
        self.assertEqual(len(self._buttons(group, "Quitar")), 3)
        self.assertEqual(len(self._buttons(group, "Agregar programa…")), 1)
        self.assertIn(
            firefox["id"], page._program_rows[firefox["id"]].get_tooltip_text()
        )
        _m, natural, _b1, _b2 = group.measure(
            __import__("gi").repository.Gtk.Orientation.VERTICAL, 800
        )
        self.assertLess(natural, 600)

    def test_quitar_unpins_and_keeps_pending_changes_through_polling(self):
        programs = real_programs()
        firefox = by_name(programs, "firefox")
        rustdesk = by_name(programs, "rustdesk")
        page = self._page((firefox["id"], rustdesk["id"]), programs)
        quitar = [
            w
            for w in self._descendants(page._program_rows[firefox["id"]])
            if getattr(w, "get_label", lambda: None)() == "Quitar"
        ]
        quitar[0].emit("clicked")
        self.assertNotIn(firefox["id"], page._program_rows)
        self.assertEqual(page._collect().bar_program_ids, (rustdesk["id"],))
        pending = page._pending
        self.assertIsNotNone(pending)
        page.update_snapshot(real_snapshot(programs))
        self.assertEqual(page._pending, pending)
        self.assertNotIn(firefox["id"], page._program_rows)
        self.assertEqual(page._collect().bar_program_ids, (rustdesk["id"],))
        # Removing the last one brings the empty state back.
        self._buttons(page._program_rows[rustdesk["id"]], "Quitar")[0].emit("clicked")
        self.assertEqual(page._collect().bar_program_ids, ())
        self.assertIn(
            "Todavía no fijaste ningún programa",
            " ".join(self._texts(page._program_group)),
        )
        self.save.assert_not_called()

    def test_absent_pin_says_ausente_and_can_be_removed(self):
        absent = "program:v1:" + "f" * 64
        page = self._page((absent,))
        row = page._program_rows[absent]
        self.assertIn("ausente", self._texts(row))
        self._buttons(row, "Quitar")[0].emit("clicked")
        self.assertEqual(page._collect().bar_program_ids, ())

    def test_switch_stays_in_the_panel_group_with_its_semantics(self):
        page = self._page()
        ancestor = page._programs_enabled.get_parent()
        while ancestor is not None and ancestor is not page._program_group:
            ancestor = ancestor.get_parent()
        self.assertIsNone(ancestor)
        page._programs_enabled.set_active(True)
        self.assertTrue(page._collect().bar_programs_enabled)


class AddDialogTests(_GtkCase):
    def _dialog(self, pins=(), programs=None):
        page = self._page(pins, programs)
        self._buttons(page._program_group, "Agregar programa…")[0].emit("clicked")
        dialog = page._add_dialog
        assert dialog is not None
        return page, dialog

    def test_default_list_is_the_15_largest_non_system(self):
        programs = real_programs()
        _page, dialog = self._dialog(programs=programs)
        expected = sorted(
            (
                p
                for p in programs
                if p["identity_source"] == "executable" and p["rss_kb"] >= 50 * MIB
            ),
            key=lambda p: -p["rss_kb"],
        )[:15]
        self.assertEqual(dialog.shown_ids(), [p["id"] for p in expected])
        self.assertFalse(dialog.include_all.get_active())
        texts = self._texts(dialog.list)
        self.assertIn("Firefox", texts)
        self.assertIn("Chromium (Playwright)", texts)
        self.assertIn("RustDesk", texts)

    def test_search_finds_firefox_and_system_only_with_the_switch(self):
        programs = real_programs()
        _page, dialog = self._dialog(programs=programs)
        dialog.search.set_text("FIREFOX")
        self.assertEqual(dialog.shown_ids(), [by_name(programs, "firefox")["id"]])
        dialog.search.set_text("nginx")
        self.assertEqual(dialog.shown_ids(), [])
        dialog.include_all.set_active(True)
        self.assertEqual(
            dialog.shown_ids(), [by_name(programs, "nginx", executable=False)["id"]]
        )

    def test_broad_query_is_capped_with_a_note(self):
        _page, dialog = self._dialog()
        dialog.include_all.set_active(True)
        dialog.search.set_text("e")
        self.assertEqual(len(dialog.shown_ids()), 50)
        self.assertIn("refiná la búsqueda", dialog.note.get_text())

    def test_fijar_pins_and_the_row_appears_in_the_group(self):
        programs = real_programs()
        page, dialog = self._dialog(programs=programs)
        firefox = by_name(programs, "firefox")
        row = dialog.rows[firefox["id"]]
        self._buttons(row, "Fijar")[0].emit("clicked")
        self.assertEqual(len(self._buttons(row, "Fijado ✓")), 1)
        self.assertIn(firefox["id"], page._program_rows)
        self.assertIn("Firefox", self._texts(page._program_group))
        self.assertEqual(page._collect().bar_program_ids, (firefox["id"],))
        self.assertIsNotNone(page._pending)
        # Fijado ✓ toggles back.
        self._buttons(row, "Fijado ✓")[0].emit("clicked")
        self.assertNotIn(firefox["id"], page._program_rows)
        self.assertEqual(page._collect().bar_program_ids, ())


if __name__ == "__main__":
    unittest.main()
