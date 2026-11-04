# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Shell de MacacoView: orden de secciones, badges vivos, pill de edad y
banner de errores de colector.

Los tests de datos son puros; los de widgets construyen GTK real, así que
necesitan display (igual que PageTests):

    xvfb-run -a python3 -m unittest discover -s tests
"""

import time
import unittest

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

from pc_ai_monitor.datos import Snapshot

_aislamiento.isolate()


def _no_refresh(self, snapshot, force=False):
    """Sustituye al poller real de Home: estos tests no tocan el motor."""


_POLLER_PATCH = None


def setUpModule():
    global _POLLER_PATCH
    from unittest import mock

    from pc_ai_monitor.blocks import home as home_blocks

    _POLLER_PATCH = mock.patch.object(home_blocks._HomePoller, "refresh", _no_refresh)
    _POLLER_PATCH.start()


def tearDownModule():
    if _POLLER_PATCH is not None:
        _POLLER_PATCH.stop()

DASH = "\u2014"

SS_SAMPLE = """tcp LISTEN 0 511 127.0.0.1:4000 0.0.0.0:* users:(("litellm",pid=2551494,fd=12))
tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))
tcp LISTEN 0 4096 192.168.1.5:8081 0.0.0.0:* users:(("php",pid=5,fd=9))
"""


def shell_snapshot(**overrides: object) -> Snapshot:
    """Snapshot representativo: las cuatro fuentes con datos."""
    base: dict[str, object] = {
        "stats": {"memory": {"available_gib": 51.4, "used_gib": 71.3}, "models": []},
        "tokens": {"pi": {"status": "ok", "total": 1000}},
        "ports": SS_SAMPLE,
        "router_status": {
            "router": {"version": "1"},
            "abito": {"healthy": False, "port": 11002},
            "flash": {"healthy": True, "port": 11008},
        },
        "router_metrics": {"telemetry": {"pct_local": 87}},
        "errors": (),
    }
    base.update(overrides)
    return Snapshot(**base)  # pyright: ignore[reportArgumentType]


class SectionsTests(unittest.TestCase):
    """S2 + S3: nombre visible, Home primero y por defecto, identificadores fijos."""

    def test_sections_start_with_home_then_motores(self):
        from pc_ai_monitor.app import DEFAULT_SECTION, SECTIONS

        self.assertEqual(SECTIONS[0], ("home", "Home", "go-home-symbolic"))
        self.assertEqual(SECTIONS[1][0], "motores")
        self.assertEqual(DEFAULT_SECTION, "home")
        self.assertEqual(DEFAULT_SECTION, SECTIONS[0][0])

    def test_rest_of_the_order_unchanged(self):
        from pc_ai_monitor.app import SECTIONS

        self.assertEqual(
            [key for key, _label, _icon in SECTIONS],
            ["home", "motores", "recursos", "router", "tokens", "puertos",
             "procesos", "config"],
        )

    def test_visible_name_and_app_id(self):
        from pc_ai_monitor.app import APP_ID, WINDOW_TITLE

        self.assertEqual(APP_ID, "local.pcai.monitor.gnome")
        self.assertEqual(WINDOW_TITLE, "MacacoView")

    def test_home_is_a_shell_page_not_a_dashboard_entry(self):
        """PageTests fija DASHBOARDS en seis claves: Home vive al lado, no dentro."""
        from pc_ai_monitor import app
        from pc_ai_monitor.pages.home import HomePage

        self.assertEqual(
            sorted(app.DASHBOARDS),
            ["motores", "procesos", "puertos", "recursos", "router", "tokens"],
        )
        self.assertIs(app.SHELL_PAGES["home"], HomePage)

    def test_home_page_is_the_cockpit(self):
        from pc_ai_monitor.pages.home import HomePage

        page = HomePage()
        # T3: las tres filas del cockpit más la tarjeta de actualización.
        self.assertEqual(len(page._blocks), 4)

    def test_shell_tests_never_start_the_real_engine_poller(self):
        """El shell recibe snapshots; el poller real no debe lanzar subprocesos."""
        from pc_ai_monitor.blocks import home as home_blocks

        self.assertIs(home_blocks._HomePoller.refresh, _no_refresh)


class BadgeTests(unittest.TestCase):
    """Cada badge es una función pura sobre Snapshot: dato → texto, hueco → «—»."""

    def test_motores_badge_names_the_healthy_engine(self):
        from pc_ai_monitor.app import engines_badge

        router = {"abito": {"healthy": True, "port": 11002}}
        self.assertEqual(engines_badge(shell_snapshot(router_status=router)), "abito")

    def test_motores_badge_ignores_the_retired_flash_engine(self):
        """L9: «flash» del router es el motor viejo retirado, no Gufo."""
        from pc_ai_monitor.app import engines_badge

        self.assertEqual(engines_badge(shell_snapshot()), DASH)

    def test_motores_badge_dash_without_a_healthy_engine(self):
        from pc_ai_monitor.app import engines_badge

        router = {"abito": {"healthy": False}, "flash": {"healthy": False}}
        self.assertEqual(engines_badge(shell_snapshot(router_status=router)), DASH)
        self.assertEqual(engines_badge(shell_snapshot(router_status=None)), DASH)

    def test_recursos_badge_shows_free_ram(self):
        from pc_ai_monitor.app import memory_badge

        self.assertEqual(memory_badge(shell_snapshot()), "51G")

    def test_recursos_badge_dash_without_stats(self):
        from pc_ai_monitor.app import memory_badge

        self.assertEqual(memory_badge(shell_snapshot(stats=None)), DASH)

    def test_router_badge_shows_local_share(self):
        from pc_ai_monitor.app import local_badge

        self.assertEqual(local_badge(shell_snapshot()), "87%")

    def test_router_badge_dash_without_metrics(self):
        from pc_ai_monitor.app import local_badge

        self.assertEqual(local_badge(shell_snapshot(router_metrics=None)), DASH)

    def test_puertos_badge_counts_exposed_ports(self):
        from pc_ai_monitor.app import ports_badge

        # Solo 0.0.0.0:22 está en todas las interfaces.
        self.assertEqual(ports_badge(shell_snapshot()), "1")

    def test_puertos_badge_dash_without_ports(self):
        from pc_ai_monitor.app import ports_badge

        self.assertEqual(ports_badge(shell_snapshot(ports=None)), DASH)


class AgePillTests(unittest.TestCase):
    """Verde por debajo de 10 s, ámbar desde 10 s."""

    def test_classification_boundaries(self):
        from pc_ai_monitor.app import age_state

        self.assertEqual(age_state(0.0), "ok")
        self.assertEqual(age_state(9.9), "ok")
        self.assertEqual(age_state(10.0), "warn")
        self.assertEqual(age_state(60.0), "warn")


class BannerTests(unittest.TestCase):
    """El banner de una sección se enciende sólo con el error de SU colector."""

    def test_revealed_when_the_collector_of_the_section_failed(self):
        from pc_ai_monitor.app import error_title

        snapshot = shell_snapshot(errors=("recursos: timeout de 5s",))
        self.assertEqual(
            error_title("recursos", snapshot), "Recursos: el colector no responde"
        )
        self.assertEqual(
            error_title("home", snapshot), "Home: el colector no responde"
        )

    def test_hidden_for_other_collectors_and_clean_snapshots(self):
        from pc_ai_monitor.app import error_title

        recursos = shell_snapshot(errors=("recursos: timeout de 5s",))
        self.assertIsNone(error_title("puertos", recursos))
        self.assertIsNone(error_title("recursos", shell_snapshot()))

    def test_sections_without_a_collector_have_no_banner(self):
        from pc_ai_monitor.app import error_title

        snapshot = shell_snapshot(errors=("recursos: timeout",))
        self.assertIsNone(error_title("config", snapshot))


class SidebarRowTests(unittest.TestCase):
    """Fila activa seleccionada y badges vivos, sobre GTK real (xvfb)."""

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

    def _app(self):
        from pc_ai_monitor.app import MonitorApplication

        application = MonitorApplication()
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        application._nav = Adw.NavigationView()
        application._split = Adw.OverlaySplitView()
        application._build_sidebar()
        return application

    def test_default_section_row_is_selected(self):
        from pc_ai_monitor.app import DEFAULT_SECTION

        application = self._app()
        application._select_section(DEFAULT_SECTION)

        assert application._listbox is not None
        selected = application._listbox.get_selected_row()
        self.assertIsNotNone(selected, "al lanzar no hay ninguna fila activa")
        self.assertEqual(application._rows[selected], "home")

    def test_selection_follows_section_changes(self):
        application = self._app()
        application._select_section("home")
        application._select_section("motores")

        assert application._listbox is not None
        selected = application._listbox.get_selected_row()
        self.assertEqual(application._rows[selected], "motores")

    def test_sidebar_badges_update_from_snapshot(self):
        application = self._app()
        for key in ("motores", "recursos", "router", "puertos"):
            self.assertIn(key, application._badges)
            self.assertEqual(application._badges[key].get_text(), DASH)

        router = {"abito": {"healthy": True, "port": 11002}}
        application._on_snapshot(shell_snapshot(router_status=router))

        self.assertEqual(application._badges["motores"].get_text(), "abito")
        self.assertEqual(application._badges["recursos"].get_text(), "51G")
        self.assertEqual(application._badges["router"].get_text(), "87%")
        self.assertEqual(application._badges["puertos"].get_text(), "1")

    def test_badges_fall_back_to_dash_without_data(self):
        application = self._app()
        application._on_snapshot(shell_snapshot())
        application._on_snapshot(
            shell_snapshot(stats=None, ports=None, router_metrics=None,
                           router_status=None)
        )

        self.assertEqual(application._badges["motores"].get_text(), DASH)
        self.assertEqual(application._badges["recursos"].get_text(), DASH)
        self.assertEqual(application._badges["router"].get_text(), DASH)
        self.assertEqual(application._badges["puertos"].get_text(), DASH)


class SectionChromeTests(unittest.TestCase):
    """Cabecera de sección: pill de edad y banner de error, sobre GTK real."""

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

    def _app_with_sections(self, *keys):
        from pc_ai_monitor.app import MonitorApplication

        application = MonitorApplication()
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        application._nav = Adw.NavigationView()
        application._split = Adw.OverlaySplitView()
        application._build_sidebar()
        for key in keys:
            application._select_section(key)
        return application

    def _chrome_for(self, application, section):
        for candidate, (key, _pill, _banner) in application._chrome.items():
            if key == section:
                return candidate, application._chrome[candidate]
        self.fail(f"sin chrome para {section}")

    def test_section_page_records_pill_and_banner(self):
        application = self._app_with_sections("home")
        _page, (_section, pill, banner) = self._chrome_for(application, "home")

        self.assertIsNotNone(pill)
        self.assertFalse(banner.get_revealed())

    def test_snapshot_shows_pill_and_reveals_banner(self):
        application = self._app_with_sections("recursos")
        _page, (_section, pill, banner) = self._chrome_for(application, "recursos")

        application._on_snapshot(
            shell_snapshot(
                errors=("recursos: timeout de 5s",),
                collected_at={"stats": time.monotonic()},
            )
        )

        self.assertTrue(banner.get_revealed())
        self.assertEqual(banner.get_title(), "Recursos: el colector no responde")
        self.assertTrue(pill.get_visible())
        self.assertEqual(pill.get_text(), "0s")
        self.assertTrue(pill.has_css_class("age-ok"))

    def test_pill_dates_each_source_by_its_last_read_not_by_content(self):
        """Un listado de puertos idéntico pero recién leído está fresco."""
        application = self._app_with_sections("puertos")
        _page, (_section, pill, _banner) = self._chrome_for(application, "puertos")
        now = time.monotonic()

        application._on_snapshot(shell_snapshot(collected_at={"ports": now - 3.0}))
        self.assertEqual(pill.get_text(), "3s")
        self.assertTrue(pill.has_css_class("age-ok"))

        application._on_snapshot(shell_snapshot(collected_at={"ports": now - 42.0}))
        self.assertEqual(pill.get_text(), "42s")
        self.assertTrue(pill.has_css_class("age-warn"))
        self.assertFalse(pill.has_css_class("age-ok"))

    def test_pill_hidden_until_the_source_was_read(self):
        application = self._app_with_sections("tokens")
        _page, (_section, pill, _banner) = self._chrome_for(application, "tokens")

        application._on_snapshot(shell_snapshot(collected_at={}))

        self.assertFalse(pill.get_visible())

    def test_clean_snapshot_hides_the_banner(self):
        application = self._app_with_sections("recursos")
        _page, (_section, _pill, banner) = self._chrome_for(application, "recursos")

        application._on_snapshot(shell_snapshot())

        self.assertFalse(banner.get_revealed())


class SettingsSnapshotTests(SectionChromeTests):
    """Configuración se abre con la última medición, no con «sin datos»."""

    def test_settings_built_after_a_snapshot_starts_with_it(self):
        application = self._app_with_sections("home")
        snapshot = shell_snapshot()
        application._on_snapshot(snapshot)

        application._select_section("config")
        page = application._sections["config"]
        content = application._pages[page]

        self.assertIs(content._snapshot, snapshot)


if __name__ == "__main__":
    unittest.main()
