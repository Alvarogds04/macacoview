# pyright: reportAttributeAccessIssue=false
"""Tests de Puertos, Procesos y Configuración con el diseño 07 (T6, S10/S12).

Las reglas nuevas de datos viven puras en ``blocks.tablas_vista`` (sin GTK);
las de construcción usan widgets reales y necesitan display:

    xvfb-run -a python3 -m unittest tests.test_puertos_procesos_config -v

Todo lo que ya funcionaba (filtro de expuestos, órdenes, top 40/80, notas,
filas de Configuración con debounce de 600 ms) se sigue probando por la
interfaz pública de los widgets. La config de las pruebas se escribe en un
directorio temporal: nunca en la config real del usuario.
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento
from gi.repository import Gtk

_aislamiento.isolate()

# Puertos de INVENTARIO-APP.md «Values for the simulation»: sólo estos, nada
# inventado. PIDs ilustrativos.
SS_FIXTURE = """tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))
tcp LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=900,fd=4))
udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("avahi-daemon",pid=901,fd=12))
tcp LISTEN 0 4096 127.0.0.1:631 0.0.0.0:* users:(("cupsd",pid=902,fd=6))
tcp LISTEN 0 4096 127.0.0.1:7420 0.0.0.0:* users:(("hermes",pid=903,fd=8))
tcp LISTEN 0 4096 127.0.0.1:7600 0.0.0.0:* users:(("open-pencil",pid=904,fd=9))
tcp LISTEN 0 4096 127.0.0.1:9222 0.0.0.0:* users:(("chrome",pid=905,fd=10))
tcp LISTEN 0 4096 127.0.0.1:11009 0.0.0.0:* users:(("pc-ai-router",pid=906,fd=11))
tcp LISTEN 0 4096 127.0.0.1:11012 0.0.0.0:* users:(("embed-small",pid=907,fd=13))
tcp LISTEN 0 4096 127.0.0.1:11070 0.0.0.0:* users:(("llama-server",pid=908,fd=14))
"""

GB = 1024 * 1024  # en KiB: 1 GiB

PROCESSES_FIXTURE = [
    {
        "pid": 908,
        "comm": "llama-server",
        "rss_kb": int(49.2 * GB),
        "cpu": 812.0,
        "args": "llama-server --port 11070",
    },
    {
        "pid": 905,
        "comm": "firefox",
        "rss_kb": int(2.8 * GB),
        "cpu": 12.4,
        "args": "/usr/lib/firefox/firefox",
    },
    {
        "pid": 904,
        "comm": "node",
        "rss_kb": int(1.9 * GB),
        "cpu": 3.2,
        "args": "node open-pencil",
    },
    {
        "pid": 903,
        "comm": "hermes",
        "rss_kb": int(1.6 * GB),
        "cpu": 1.1,
        "args": "hermes serve",
    },
    {
        "pid": 909,
        "comm": "gnome-shell",
        "rss_kb": int(1.1 * GB),
        "cpu": 4.5,
        "args": "/usr/bin/gnome-shell",
    },
]

STATS_FIXTURE = {
    "memory": {
        "total_gib": 122.0,
        "used_gib": 70.9,
        "available_gib": 51.1,
    },
    "models": [
        {"alias": "gufo", "rss_gib": 49.2, "gtt_gib": 90.0},
        {"alias": "embed-small", "rss_gib": 0.4, "gtt_gib": 0.5},
    ],
    "groups": {
        "pi": {"rss_gib": 6.8},
        "hermes": {"rss_gib": 2.1},
        "firefox": {"rss_gib": 4.3},
        "system": {"rss_gib": 3.9},
        "other": {"rss_gib": 4.6},
    },
    "processes": PROCESSES_FIXTURE,
}

ROUTER_FIXTURE = {
    "router": {"active_sessions": 3},
    "abito": {"name": "abito", "healthy": True, "queue": 0},
    "flash": {"name": "flash", "healthy": False, "queue": 0},
    "jev": {"key_present": True},
}


def make_snapshot():
    from pc_ai_monitor.datos import Snapshot

    return Snapshot(
        stats=dict(STATS_FIXTURE), ports=SS_FIXTURE, router_status=dict(ROUTER_FIXTURE)
    )


class TablasVistaTests(unittest.TestCase):
    """Reglas puras nuevas: fracción de la barra de RAM, estilo de la pill de
    acceso y chips de la vista previa del pill del panel."""

    def test_ram_bar_fraction_scales_to_the_largest_shown_row(self):
        from pc_ai_monitor.blocks.tablas_vista import ram_fraction

        self.assertAlmostEqual(ram_fraction(49.2 * GB, 49.2 * GB), 1.0)
        self.assertAlmostEqual(ram_fraction(2.8 * GB, 49.2 * GB), 2.8 / 49.2, places=4)

    def test_ram_bar_fraction_floors_tiny_rows_so_the_bar_is_visible(self):
        from pc_ai_monitor.blocks.tablas_vista import ram_fraction

        fraction = ram_fraction(0.001 * GB, 49.2 * GB)
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 0.05)

    def test_ram_bar_fraction_is_zero_without_data(self):
        from pc_ai_monitor.blocks.tablas_vista import ram_fraction

        self.assertEqual(ram_fraction(0, 0), 0.0)
        self.assertEqual(ram_fraction(10 * GB, 0), 0.0)

    def test_ram_text_keeps_the_two_decimals_gib_format(self):
        from pc_ai_monitor.blocks.tablas_vista import ram_text

        self.assertEqual(ram_text(49.2 * GB), "49.20G")

    def test_access_pill_kind_marks_exposed_rows(self):
        from pc_ai_monitor.blocks.tablas_vista import access_pill_kind
        from pc_ai_monitor.puertos import ALL, IFACE, LAN, LOCAL, TAILSCALE

        self.assertEqual(access_pill_kind(ALL), "ex")
        self.assertEqual(access_pill_kind(LOCAL), "lo")
        self.assertEqual(access_pill_kind(LAN), "lo")
        self.assertEqual(access_pill_kind(TAILSCALE), "lo")
        self.assertEqual(access_pill_kind(IFACE), "lo")

    def test_access_tips_cover_every_badge(self):
        from pc_ai_monitor.blocks.tablas_vista import ACCESS_TIPS
        from pc_ai_monitor.puertos import ALL, IFACE, LAN, LOCAL, TAILSCALE

        for access in (LOCAL, LAN, TAILSCALE, ALL, IFACE):
            self.assertTrue(ACCESS_TIPS[access], access)

    def test_pill_chips_default_shows_the_named_groups_and_models(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        chips = pill_chips(cfg.Config(), make_snapshot())
        texts = [text for text, _tip in chips]
        # pi, hermes y firefox salen siempre; system/other no.
        self.assertTrue(any("🥧" in text and "6.8G" in text for text in texts), texts)
        self.assertTrue(any("🪶" in text and "2.1G" in text for text in texts), texts)
        self.assertTrue(any("🦊" in text and "4.3G" in text for text in texts), texts)
        self.assertFalse(any("⚙" in text for text in texts), texts)
        # Modelos por separado: un chip por alias.
        self.assertTrue(
            any("gufo" in text and "49.2G" in text for text in texts), texts
        )
        self.assertTrue(
            any("embed-small" in text and "0.4G" in text for text in texts), texts
        )
        # El router está apagado de fábrica: sin chip de estado.
        self.assertFalse(any("JEV" in text for text in texts), texts)

    def test_pill_chips_hide_zero_hides_small_values_unless_always(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        snapshot = make_snapshot()
        base = pill_chips(cfg.Config(), snapshot)
        self.assertTrue(any("embed-small" in text for text, _ in base))

        strict = cfg.Config(bar_hide_zero=True)
        # 0.4 GiB > 0.05: sigue. Un modelo de 0.01 GiB no.
        assert snapshot.stats is not None
        snapshot.stats["models"] = [
            {"alias": "gufo", "rss_gib": 49.2},
            {"alias": "tiny", "rss_gib": 0.01},
        ]
        chips = pill_chips(strict, snapshot)
        texts = [text for text, _ in chips]
        self.assertTrue(any("gufo" in text for text in texts))
        self.assertFalse(any("tiny" in text for text in texts), texts)

        always = cfg.Config(bar_hide_zero=True, bar_always_show=("tiny",))
        chips = pill_chips(always, snapshot)
        self.assertTrue(any("tiny" in text for text, _ in chips))

    def test_pill_chips_only_filters_everything_else(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        config = cfg.Config(bar_only=("pi",))
        chips = pill_chips(config, make_snapshot())
        texts = [text for text, _ in chips]
        self.assertTrue(any("🥧" in text for text in texts), texts)
        self.assertEqual(len(chips), 1, texts)

    def test_pill_chips_percent_units_need_total_memory(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        config = cfg.Config(bar_units="percent")
        chips = pill_chips(config, make_snapshot())
        self.assertTrue(any("6%" in text for text, _ in chips), chips)  # 6.8/122 ≈ 5.6

    def test_pill_chips_combined_models_sum_into_one_chip(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        config = cfg.Config(bar_models="combined")
        chips = pill_chips(config, make_snapshot())
        texts = [text for text, _ in chips]
        combined = [text for text in texts if "49.6G" in text]
        self.assertEqual(len(combined), 1, texts)
        self.assertFalse(any("gufo" in text for text in texts), texts)

    def test_pill_chips_skip_hidden_groups(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips
        from pc_ai_monitor.datos import Snapshot

        snapshot = Snapshot(stats=dict(STATS_FIXTURE), hidden_groups=("hermes",))
        chips = pill_chips(cfg.Config(), snapshot)
        self.assertFalse(any("🪶" in text for text, _ in chips))

    def test_pill_chips_router_indicator_uses_live_status_only(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips
        from pc_ai_monitor.datos import Snapshot

        config = cfg.Config(bar_router_indicators=True)
        chips = pill_chips(config, make_snapshot())
        router = [text for text, _ in chips if "JEV" in text]
        self.assertEqual(len(router), 1, chips)
        self.assertIn("A●", router[0])
        self.assertIn("F○", router[0])

        # Sin router: chip honesto de «sin datos», nunca inventado.
        bare = Snapshot(stats=dict(STATS_FIXTURE))
        chips = pill_chips(config, bare)
        self.assertTrue(any("sin datos" in tip.lower() for _text, tip in chips), chips)

    def test_pill_chips_without_snapshot_is_empty(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.blocks.tablas_vista import pill_chips

        self.assertEqual(pill_chips(cfg.Config(), None), [])


def _click(button) -> None:
    button.emit("clicked")


class _GtkCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")


class PuertosPageTests(_GtkCase):
    """Funciones existentes de la pestaña Puertos, por la interfaz pública."""

    def _page(self, ports=SS_FIXTURE):
        from pc_ai_monitor.datos import Snapshot
        from pc_ai_monitor.pages.puertos import PuertosPage

        page = PuertosPage()
        page.update_snapshot(Snapshot(stats=dict(STATS_FIXTURE), ports=ports))
        return page

    def _table_block(self, page):
        return page._blocks[1]

    def _rows(self, page):
        from pc_ai_monitor.widgets.tabla import row_labels

        return row_labels(self._table_block(page)._rows)

    def _exposed_toggle(self, block):
        from pc_ai_monitor.widgets.tabla import find_widgets

        toggles = find_widgets(block, Gtk.CheckButton)
        self.assertEqual(len(toggles), 1)
        return toggles[0]

    def test_kpis_show_tcp_udp_and_exposed(self):
        from pc_ai_monitor.blocks.puertos import PortsKpiBlock
        from pc_ai_monitor.datos import Snapshot

        block = PortsKpiBlock()
        block.update_snapshot(Snapshot(ports=SS_FIXTURE))
        values = [tile._value.get_text() for tile in block._tiles.values()]
        # 22 (v4+v6) + 7 tcp de loopback, 1 udp, 3 expuestos (22x2, 5353).
        self.assertEqual(values, ["9", "1", "3"])

    def test_filter_exposed_changes_the_rows(self):
        page = self._page()
        block = self._table_block(page)
        self.assertEqual(len(self._rows(page)), 10)

        toggle = self._exposed_toggle(block)
        toggle.set_active(True)
        texts = self._rows(page)
        self.assertEqual(len(texts), 3, texts)
        # Nota con el filtro puesto: N de M.
        self.assertIn("3 de 10 puertos", block._note.get_text())

        toggle.set_active(False)
        self.assertEqual(len(self._rows(page)), 10)

    def test_sort_rules_new_key_asc_and_same_key_toggles(self):
        page = self._page()
        block = self._table_block(page)

        # Por defecto PUERTO ascendente.
        ports = [int(texts[1]) for texts in self._rows(page)]
        self.assertEqual(ports, sorted(ports))

        # Misma clave: alterna a descendente.
        _click(block._header_buttons["port"])
        ports = [int(texts[1]) for texts in self._rows(page)]
        self.assertEqual(ports, sorted(ports, reverse=True))

        # Clave nueva (PID): arranca ascendente.
        _click(block._header_buttons["pid"])
        pids = [int(texts[5]) for texts in self._rows(page)]
        self.assertEqual(pids, sorted(pids))

    def test_exposed_rows_are_highlighted(self):
        from pc_ai_monitor.widgets.tabla import row_flags

        page = self._page()
        rows = self._rows(page)
        flags = row_flags(self._table_block(page)._rows, "row-exposed")
        self.assertEqual(sum(flags), 3, rows)
        # Las tres filas expuestas: 22 v4, 22 v6 y 5353.
        exposed_ports = [texts[1] for texts, flag in zip(rows, flags) if flag]
        self.assertEqual(sorted(exposed_ports), ["22", "22", "5353"])

    def test_max_80_rows(self):
        lines = [
            f"tcp LISTEN 0 4096 127.0.0.1:{20000 + index} 0.0.0.0:* "
            f'users:(("srv",pid={index + 1},fd=3))'
            for index in range(100)
        ]
        page = self._page(ports="\n".join(lines))
        self.assertEqual(len(self._rows(page)), 80)
        self.assertIn("80 de 100 puertos", self._table_block(page)._note.get_text())

    def test_note_and_empty_state(self):
        page = self._page()
        block = self._table_block(page)
        self.assertIn("local", block._note.get_text())

        empty = self._page(ports="")
        empty_block = empty._blocks[1]
        from pc_ai_monitor.widgets.tabla import widget_texts

        texts = "\n".join(widget_texts(empty_block._rows))
        self.assertIn("Sin datos de puertos: falta el helper privilegiado.", texts)
        self.assertEqual(
            empty_block._note.get_text(),
            "Sin el helper, la pestaña queda vacía en vez de pedir credenciales.",
        )

    def test_access_cell_is_a_pill_with_tooltip(self):
        from pc_ai_monitor.widgets.tabla import find_css

        page = self._page()
        pills = find_css(self._table_block(page)._rows, "access-pill", Gtk.Label)
        self.assertTrue(pills, "la columna ACCESO usa pills")
        labels = {pill.get_text() for pill in pills}
        self.assertIn("🌐 Todas", labels)
        self.assertIn("🔒 Local", labels)
        for pill in pills:
            self.assertTrue(pill.get_tooltip_text(), pill.get_text())


class ProcesosPageTests(_GtkCase):
    """Funciones existentes de la pestaña Procesos, por la interfaz pública."""

    def _page(self, stats=None):
        from pc_ai_monitor.datos import Snapshot
        from pc_ai_monitor.pages.procesos import ProcesosPage

        page = ProcesosPage()
        page.update_snapshot(Snapshot(stats=stats or dict(STATS_FIXTURE)))
        return page

    def _pids(self, page):
        from pc_ai_monitor.widgets.tabla import row_labels

        return [int(texts[0]) for texts in row_labels(page._blocks[0]._rows)]

    def test_default_sort_is_ram_desc(self):
        page = self._page()
        self.assertEqual(self._pids(page)[0], 908)  # llama-server, 49.2G

    def test_new_key_starts_desc_except_pid(self):
        page = self._page()
        block = page._blocks[0]

        _click(block._header_buttons["cpu"])
        pids = self._pids(page)
        # CPU desc: llama-server 812 % primero.
        self.assertEqual(pids[0], 908)

        _click(block._header_buttons["pid"])
        pids = self._pids(page)
        self.assertEqual(pids, sorted(pids))  # PID nuevo: ascendente

    def test_same_key_toggles(self):
        page = self._page()
        block = page._blocks[0]

        _click(block._header_buttons["ram"])  # ya estaba en ram: ahora asc
        pids = self._pids(page)
        self.assertEqual(pids[-1], 908)

    def test_top_40_and_note(self):
        stats = dict(STATS_FIXTURE)
        stats["processes"] = [
            {
                "pid": index,
                "comm": f"p{index}",
                "rss_kb": 1000 * (1000 - index),
                "cpu": 0.5,
                "args": f"proc {index}",
            }
            for index in range(60)
        ]
        page = self._page(stats=stats)
        block = page._blocks[0]
        self.assertEqual(len(self._pids(page)), 40)
        self.assertIn("Mostrando 40 de 60 procesos", block._note.get_text())
        self.assertIn("ordenado por ram", block._note.get_text())

    def test_ram_row_has_an_inline_bar(self):
        from pc_ai_monitor.widgets.tabla import find_css

        page = self._page()
        bars = find_css(page._blocks[0]._rows, "ram-bar", Gtk.ProgressBar)
        self.assertEqual(len(bars), 5)
        # La fila más pesada llega al tope de la barra.
        fractions = [bar.get_fraction() for bar in bars]
        self.assertAlmostEqual(max(fractions), 1.0)
        self.assertGreater(min(fractions), 0.0)


class SettingsPreviewTests(_GtkCase):
    """La filas de Configuración existen, aplican tras el debounce de 600 ms
    y la vista previa del pill usa sólo la config actual + la última medición."""

    def setUp(self):
        import pc_ai_monitor.config as cfg

        self._tmp = tempfile.TemporaryDirectory()
        self._config_path = Path(self._tmp.name) / "config.toml"
        patcher = mock.patch.object(cfg, "CONFIG_PATH", self._config_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _section(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.settings import SettingsSection

        self.applied: list = []
        return SettingsSection(
            cfg.Config(), self.applied.append, snapshot=make_snapshot()
        )

    def _drain(self, seconds=0.9):
        """Suelta el main loop de GTK hasta que pase el debounce."""
        from gi.repository import GLib

        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            context.iteration(False)

    def test_rows_exist_for_every_setting(self):
        section = self._section()
        from pc_ai_monitor import config as cfg

        current = cfg.Config()
        collected = section._collect()
        self.assertEqual(collected.theme, current.theme)
        self.assertEqual(collected.stats_interval_s, current.stats_interval_s)
        self.assertEqual(collected.ports_interval_s, current.ports_interval_s)
        self.assertEqual(collected.tokens_interval_s, current.tokens_interval_s)
        self.assertEqual(collected.stats_bin, current.stats_bin)
        self.assertEqual(collected.ports_helper, current.ports_helper)
        self.assertEqual(collected.ports_use_sudo, current.ports_use_sudo)
        self.assertEqual(collected.bar_hide_zero, current.bar_hide_zero)
        self.assertEqual(collected.bar_units, current.bar_units)
        self.assertEqual(collected.bar_models, current.bar_models)
        self.assertEqual(collected.bar_router_indicators, current.bar_router_indicators)
        self.assertEqual(len(collected.watch), len(current.watch))

    def test_apply_after_debounce_writes_tmp_config(self):
        section = self._section()
        section._stats.set_value(3.0)
        self.assertEqual(self.applied, [])  # todavía no: 600 ms de debounce
        self.assertFalse(self._config_path.exists())

        self._drain()
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[0].stats_interval_s, 3.0)
        self.assertTrue(self._config_path.exists())
        self.assertIn("stats_s = 3.0", self._config_path.read_text())

    def test_pill_preview_chips_follow_config_and_snapshot(self):
        from pc_ai_monitor.widgets.tabla import widget_texts

        section = self._section()
        texts = widget_texts(section._pill_preview)
        self.assertTrue(any("🥧" in text and "6.8G" in text for text in texts), texts)
        self.assertTrue(any("gufo" in text for text in texts), texts)
        # Cambiar unidades a porcentaje re-pinta la vista previa al aplicar.
        section._units.set_selected(1)
        self._drain()
        texts = widget_texts(section._pill_preview)
        self.assertTrue(any("6%" in text for text in texts), texts)

    def test_pill_preview_without_snapshot_is_not_fake(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.settings import SettingsSection
        from pc_ai_monitor.widgets.tabla import widget_texts

        section = SettingsSection(cfg.Config(), lambda updated: None)
        texts = widget_texts(section._pill_preview)
        self.assertTrue(any("sin datos" in text.lower() for text in texts), texts)
        self.assertFalse(any(text.endswith("G") for text in texts), texts)

    def test_update_snapshot_refreshes_the_preview(self):
        from pc_ai_monitor.datos import Snapshot
        from pc_ai_monitor.widgets.tabla import widget_texts

        section = self._section()
        stats = dict(STATS_FIXTURE)
        stats["groups"] = dict(STATS_FIXTURE["groups"])
        stats["groups"]["pi"] = {"rss_gib": 1.5}
        section.update_snapshot(Snapshot(stats=stats))
        texts = widget_texts(section._pill_preview)
        self.assertTrue(any("1.5G" in text for text in texts), texts)
