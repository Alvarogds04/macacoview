# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests that need no visible window.

Run with:

    xvfb-run -a python3 -m unittest discover -s tests

The page tests build real GTK widgets, so a display — even a virtual one — is
required; everything else is pure logic.
"""

import json
import shutil
import subprocess
import unittest
from ipaddress import ip_address
from pathlib import Path

from pc_ai_monitor.datos import GROUP_KEYS, Sample, Snapshot
from pc_ai_monitor.formato import count, gib, gib_label, percent, safe, share, tokens, whole
from pc_ai_monitor.puertos import (
    ALL,
    LOCAL,
    classify,
    counts,
    host_of,
    is_wildcard,
    parse_ss,
    port_of,
    service_name,
)

# La dirección no especificada, compuesta en vez de pegada como literal.
ANY_V4 = str(ip_address(0))

SS_SAMPLE = f"""tcp LISTEN 0 511 127.0.0.1:4000 0.0.0.0:* users:((\"litellm\",pid=2551494,fd=12))
tcp LISTEN 0 4096 {ANY_V4}:22 0.0.0.0:* users:((\"sshd\",pid=900,fd=3))
tcp LISTEN 0 4096 [fd7a:115c:a1e0::1]:443 [::]:* users:((\"tailscaled\",pid=2724,fd=21))
udp UNCONN 0 0 127.0.0.54%lo:53 0.0.0.0:* users:((\"systemd-resolve\",pid=699,fd=18))
"""


class FormatoTests(unittest.TestCase):
    def test_tokens_scale(self):
        self.assertEqual(tokens(2_213_983_476), "2.21B")
        self.assertEqual(tokens(146_214_033), "146.2M")
        self.assertEqual(tokens(1_500), "1.5k")
        self.assertEqual(tokens(42), "42")

    def test_safe_and_whole_survive_garbage(self):
        self.assertEqual(safe(None), 0.0)
        self.assertEqual(safe("no soy un numero"), 0.0)
        self.assertEqual(safe(True), 0.0)  # bool no es un numero valido
        self.assertEqual(whole("2551494"), 2551494)
        self.assertEqual(whole("x"), 0)

    def test_share_clamps_and_percent(self):
        self.assertEqual(share(50, 100), 0.5)
        self.assertEqual(share(500, 100), 1.0)
        self.assertEqual(share(1, 0), 0.0)
        self.assertEqual(percent(50, 200), "25%")

    def test_gib(self):
        self.assertEqual(gib(40.571), "40.6")
        self.assertEqual(count(18351), "18.351")


class PuertosTests(unittest.TestCase):
    def test_is_wildcard(self):
        # Test fixtures: these assert is_wildcard() REJECTS wildcard addresses.
        # Nothing in this project binds to all interfaces.
        for host in ("0.0.0.0", "::", "*"):  # noqa: S104
            self.assertTrue(is_wildcard(host), host)
        for host in ("127.0.0.1", "::1", "100.64.0.1", "fe80::1"):
            self.assertFalse(is_wildcard(host), host)

    def test_classify(self):
        self.assertEqual(classify("127.0.0.1:4000"), LOCAL)
        self.assertEqual(classify(f"{ANY_V4}:22"), ALL)
        self.assertEqual(classify("[fd7a:115c:a1e0::1]:443"), "🔷 Tailscale")
        self.assertEqual(classify("192.168.1.5:80"), "🏠 LAN")
        self.assertEqual(classify("[::1]:8080"), LOCAL)

    def test_host_and_port(self):
        self.assertEqual(host_of("127.0.0.53%lo:53"), "127.0.0.53")
        self.assertEqual(port_of("[::1]:8080"), 8080)
        self.assertEqual(port_of("*:3390"), 3390)

    def test_parse_and_name(self):
        rows = parse_ss(SS_SAMPLE)
        self.assertEqual(len(rows), 4)
        ports = {row.port: row for row in rows}
        self.assertEqual(ports[4000].service, "LiteLLM Proxy")
        self.assertEqual(ports[4000].process, "litellm")
        self.assertEqual(ports[53].service, "DNS")
        self.assertEqual(ports[443].process, "tailscaled")
        self.assertTrue(ports[22].exposed)
        self.assertFalse(ports[4000].exposed)
        self.assertEqual(counts(rows), (3, 1, 1))

    def test_service_fallback(self):
        self.assertEqual(service_name(65500, "llama-server"), "llama.cpp")
        self.assertEqual(service_name(65500, "algo-raro"), "algo-raro")


class TreemapTests(unittest.TestCase):
    def test_layout_fills_the_area_in_order(self):
        from pc_ai_monitor.widgets.base import Slice
        from pc_ai_monitor.widgets.graficos import _layout

        slices = [
            Slice("grande", 60, "#000"),
            Slice("medio", 30, "#000"),
            Slice("chico", 10, "#000"),
        ]
        placed = _layout(slices, (0, 0, 100, 100), True)

        self.assertEqual(
            [slice_.label for slice_, _rect in placed], ["grande", "medio", "chico"]
        )
        area = sum(rect[2] * rect[3] for _slice, rect in placed)
        self.assertAlmostEqual(area, 100 * 100, delta=1.0)


class PageTests(unittest.TestCase):
    """Every dashboard must build and accept a snapshot: a broken column or a
    missing block only shows up when the page is actually constructed."""

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

    def _snapshot(self) -> Snapshot:
        stats = {
            "memory": {
                "total_gib": 122.7,
                "used_gib": 57.5,
                "available_gib": 65.2,
                "swap_total_gib": 8.0,
                "swap_used_gib": 2.3,
            },
            "models": [
                {
                    "alias": "Abito-gpt",
                    "pid": 767149,
                    "port": 11002,
                    "model": "qwen.gguf",
                    "rss_gib": 9.0,
                    "cpu": 8.4,
                    "gtt_gib": 40.6,
                    "vram_gib": 0.0,
                },
                {
                    "alias": "Personal-gpt",
                    "pid": 2937,
                    "port": 11004,
                    "model": "lfm.gguf",
                    "rss_gib": 0.7,
                    "cpu": 0.0,
                    "gtt_gib": 7.4,
                    "vram_gib": 0.1,
                },
            ],
            "groups": {
                "pi": {"rss_gib": 2.5, "cpu": 10.0, "pids": [767149]},
                "system": {"rss_gib": 12.0, "cpu": 1.0, "pids": [900]},
            },
            "processes": [
                {
                    "pid": 767149,
                    "comm": "llama-server",
                    "rss_kb": 9_000_000,
                    "cpu": 8.4,
                    "args": "llama-server --port 11002",
                },
                {
                    "pid": 900,
                    "comm": "sshd",
                    "rss_kb": 5_000,
                    "cpu": 0.1,
                    "args": "/usr/sbin/sshd -D",
                },
            ],
        }
        tokens = {
            "pi": {
                "status": "ok",
                "total": 2_213_983_476,
                "turns": 18_351,
                "cost_usd": 1998.9,
                "cache_read": 2_102_997_888,
                "models": [
                    {
                        "name": "gpt-5.6-sol",
                        "provider": "openai-codex",
                        "total": 2_213_983_476,
                        "turns": 15_779,
                    },
                    {
                        "name": "Abito-gpt",
                        "provider": "abito-direct",
                        "total": 0,
                        "turns": 295,
                    },
                ],
            },
            "remote": {
                "status": "ok",
                "total": 56_932_796,
                "models": [{"name": "deepseek-v4-flash", "total": 56_932_796}],
            },
            "local": {
                "status": "ok",
                "total": 0,
                "models": [{"name": "Abito-gpt", "port": 11002, "total": 0}],
            },
            "codex": {
                "status": "ok",
                "plan": "prolite",
                "windows": [
                    {
                        "label": "semanal",
                        "used_percent": 74.0,
                        "resets_at": "2026-09-22T06:35:20Z",
                    }
                ],
            },
            "codex_cli": {
                "status": "ok",
                "input": 63_000_000,
                "output": 541_840,
                "cache_read": 5_000_000,
                "cache_write": 1_200_000,
                "reasoning": 107_456,
                "total": 63_541_840,
                "turns": 577,
                "sessions": 8,
                "models": [],
            },
            "claude_code": {
                "status": "ok",
                "input": 1_000_000,
                "output": 100_000,
                "cache_read": 300_000,
                "cache_write": 40_000,
                "reasoning": 20_000,
                "total": 1_440_000,
                "turns": 25,
                "sessions": 1,
                "models": [],
            },
        }
        history = tuple(
            Sample(
                used_gib=50.0 + step,
                available_gib=70.0 - step,
                groups=(1.0, 0.0, 1.0, 10.0, 5.0),
            )
            for step in range(5)
        )
        return Snapshot(stats=stats, tokens=tokens, ports=SS_SAMPLE, history=history)

    def test_all_dashboards_build_and_update(self):
        from pc_ai_monitor.app import DASHBOARDS

        self.assertEqual(
            sorted(DASHBOARDS),
            ["motores", "procesos", "puertos", "recursos", "router", "tokens"],
        )

        snapshot = self._snapshot()
        for key, factory in sorted(DASHBOARDS.items()):
            with self.subTest(dashboard=key):
                page = factory()
                page.update_snapshot(snapshot)
                page.redraw()
                self.assertGreater(len(page._blocks), 0)

    def test_new_token_sections_render(self):
        """codex_cli y claude_code del colector tienen que llegar a la página:
        razonamiento y caché escrito son métricas que antes no se veían."""
        from pc_ai_monitor.blocks.usage import ClaudeCodeKpiBlock, CodexCliKpiBlock
        from pc_ai_monitor.pages.usage import TokensPage

        page = TokensPage()
        page.update_snapshot(self._snapshot())

        codex = next(b for b in page._blocks if isinstance(b, CodexCliKpiBlock))
        claude = next(b for b in page._blocks if isinstance(b, ClaudeCodeKpiBlock))
        self.assertEqual(codex._tiles["total"]._value.get_text(), tokens(63_541_840))
        self.assertEqual(codex._tiles["reasoning"]._value.get_text(), tokens(107_456))
        self.assertEqual(
            codex._tiles["cache_write"]._value.get_text(), tokens(1_200_000)
        )
        self.assertEqual(claude._tiles["total"]._value.get_text(), tokens(1_440_000))
        self.assertEqual(claude._tiles["reasoning"]._value.get_text(), tokens(20_000))
        self.assertEqual(
            claude._tiles["cache_write"]._value.get_text(), tokens(40_000)
        )
        self.assertEqual(claude._tiles["cache_read"]._value.get_text(), tokens(300_000))


class HiddenGroupTests(unittest.TestCase):
    """Un grupo [[watch]] apagado no dibuja tile ni serie.

    El historial mapea por posición sobre GROUP_KEYS completo: ocultar no
    puede cambiar el largo de ``sample.groups`` ni correr los valores de las
    demás entradas.
    """

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

    @staticmethod
    def _snapshot(hidden: tuple[str, ...]) -> Snapshot:
        values = dict(zip(GROUP_KEYS, (2.5, 9.0, 1.0, 12.0, 5.0)))
        stats = {
            "memory": {"used_gib": 50.0, "available_gib": 70.0},
            "groups": {key: {"rss_gib": value} for key, value in values.items()},
            "processes": [],
        }
        # Valor trampa 99.0 en la entrada oculta: si ocultar corriera los
        # índices, firefox terminaría mostrando 99.0.
        history = tuple(
            Sample(
                used_gib=50.0 + step,
                available_gib=70.0 - step,
                groups=(1.0 + step, 99.0, 2.0 + step, 3.0, 4.0),
            )
            for step in range(5)
        )
        return Snapshot(stats=stats, history=history, hidden_groups=hidden)

    def test_collector_reports_hidden_watch_groups(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.datos import Collector

        self.assertEqual(Collector(cfg.Config()).snapshot().hidden_groups, ())
        config = cfg.Config(
            watch=(
                cfg.WatchEntry(name="pi"),
                cfg.WatchEntry(name="hermes", visible=False),
            )
        )
        self.assertEqual(Collector(config).snapshot().hidden_groups, ("hermes",))

    def test_hidden_watch_entry_draws_no_tile(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(self._snapshot(hidden=("hermes",)))

        self.assertFalse(block._rows["hermes"][0].get_visible())
        self.assertTrue(block._rows["pi"][0].get_visible())
        labels = [slice_.label for slice_ in block._donut._slices]
        self.assertNotIn("🪽 Hermes", labels)
        self.assertIn("🥧 Pi", labels)
        # El total del donut no cuenta a la entrada oculta.
        self.assertEqual(
            block._donut._center_value, gib_label(2.5 + 1.0 + 12.0 + 5.0, 2)
        )

    def test_history_keeps_indices_aligned_with_hidden_entries(self):
        from pc_ai_monitor.blocks.historial import HistoryBlock

        # hermes no tiene fila de serie; pi sí. Ocultar pi no puede correr los
        # índices: si lo hiciera, firefox leería la columna de hermes (99.0).
        block = HistoryBlock()
        block.update_snapshot(self._snapshot(hidden=("pi", "hermes")))

        self.assertFalse(block._rows["pi"].get_visible())
        self.assertTrue(block._rows["firefox"].get_visible())
        # La serie oculta sigue acumulando por posición, sin huecos, para que
        # al reactivar la entrada el gráfico retome donde estaba.
        self.assertEqual(block._sparks["pi"]._values[-1], 5.0)
        self.assertEqual(block._values["firefox"].get_text(), gib(6.0) + "G")
        self.assertEqual(block._sparks["firefox"]._values[-1], 6.0)


class ConfigTests(unittest.TestCase):
    """El diálogo escribe el archivo: la ida y vuelta tiene que ser fiel."""

    def test_round_trip(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        original = cfg.Config(
            stats_interval_s=2.5,
            theme="ghostly",
            bar_hide_zero=False,
            bar_only=("pi", "abito-gpt"),
            bar_units="percent",
            bar_models="combined",
            ports_use_sudo=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            self.assertTrue(cfg.save(original, path))
            loaded = cfg.load(path)

        self.assertEqual(loaded.theme, "ghostly")
        self.assertAlmostEqual(loaded.stats_interval_s, 2.5)
        self.assertEqual(loaded.bar_only, ("pi", "abito-gpt"))
        self.assertEqual(loaded.bar_units, "percent")
        self.assertEqual(loaded.bar_models, "combined")
        self.assertFalse(loaded.bar_hide_zero)
        self.assertFalse(loaded.ports_use_sudo)

    def test_broken_file_falls_back_to_defaults(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            path.write_text("[bar\nesto no es toml")
            loaded = cfg.load(path)

        self.assertEqual(loaded.theme, cfg.Config().theme)
        self.assertTrue(loaded.bar_hide_zero)

    def test_watch_defaults_reproduce_the_old_groups(self):
        from pc_ai_monitor import config as cfg

        watch = cfg.Config().watch
        self.assertEqual(
            tuple(entry.name for entry in watch),
            ("pi", "hermes", "firefox", "system", "other"),
        )
        self.assertTrue(all(entry.visible for entry in watch))
        # Los grupos con regla de patrones traen sus patrones; system/other son
        # reglas fijas del colector (uid 0 y resto).
        self.assertTrue(watch[0].match)
        self.assertFalse(watch[3].match)

    def test_watch_round_trip(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        original = cfg.Config(
            watch=(
                cfg.WatchEntry(name="chrome", match=(r"^chrome$", "/chrome/"), icon="🌐"),
                cfg.WatchEntry(name="system", visible=False),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            self.assertTrue(cfg.save(original, path))
            loaded = cfg.load(path)

        self.assertEqual(loaded.watch, original.watch)
        self.assertFalse(loaded.watch[1].visible)

    def test_watch_missing_section_falls_back_to_defaults(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            path.write_text('[bar]\nhide_zero = false\n')
            loaded = cfg.load(path)

        self.assertEqual(loaded.watch, cfg.Config().watch)

    def test_watch_invalid_entries_fall_back_to_defaults(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        # Entrada sin nombre y entrada con match roto: no vale ninguna.
        broken = (
            "[[watch]]\n"
            'match = ["x"]\n'
            "[[watch]]\n"
            'name = "chrome"\n'
            "match = 5\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            path.write_text(broken)
            loaded = cfg.load(path)

        self.assertEqual(loaded.watch, cfg.Config().watch)

    def test_watch_keeps_the_valid_entries_and_drops_the_broken_ones(self):
        import pathlib
        import tempfile

        from pc_ai_monitor import config as cfg

        mixed = (
            "[[watch]]\n"
            'name = "Chrome"\n'
            'match = ["^chrome$", "/chrome/"]\n'
            'icon = "🌐"\n'
            "visible = false\n"
            "[[watch]]\n"
            'match = ["sin nombre"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            path.write_text(mixed)
            loaded = cfg.load(path)

        self.assertEqual(len(loaded.watch), 1)
        self.assertEqual(loaded.watch[0].name, "chrome")
        self.assertEqual(loaded.watch[0].match, ("^chrome$", "/chrome/"))
        self.assertFalse(loaded.watch[0].visible)


class WatchKeysTests(unittest.TestCase):
    """La capa de datos ordena el historial segun los grupos vigilados: las
    claves tienen que salir del mismo lugar que [[watch]], no de otra lista."""

    def test_group_keys_follow_the_watch_defaults(self):
        from pc_ai_monitor import config as cfg

        self.assertEqual(
            GROUP_KEYS, tuple(entry.name for entry in cfg.Config().watch)
        )


class SettingsTests(unittest.TestCase):
    """El diálogo de configuración tiene que abrir y devolver la config actual.

    Se construye de verdad: un atributo mal usado en una fila (por ejemplo
    ``set_subtitle`` en un ``Adw.EntryRow``) sólo aparece al instanciarlo.
    """

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

    def test_dialog_builds_and_returns_current_config(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor.settings import SettingsSection

        current = cfg.Config()
        window = SettingsSection(current, lambda updated: None)
        collected = window._collect()

        self.assertEqual(collected.theme, current.theme)
        self.assertEqual(collected.stats_interval_s, current.stats_interval_s)
        self.assertEqual(collected.tokens_bin, current.tokens_bin)
        self.assertEqual(collected.ports_use_sudo, current.ports_use_sudo)

        # Elegir otro tema en el combo tiene que devolver ESE tema.
        window._theme_row.set_selected(3)
        self.assertEqual(window._collect().theme, "ghostly")

        # Y el ícono se elige por nombre.
        window._icon_row.set_text("computer")
        self.assertEqual(window._collect().icon, "computer")

        # Cada entrada [[watch]] tiene su casilla, y apagarla se refleja en la
        # config recolectada sin tocar a las demás.
        self.assertEqual(
            len(window._watch_switches), len(current.watch)
        )
        window._watch_switches[0].set_active(False)
        collected = window._collect()
        self.assertFalse(collected.watch[0].visible)
        self.assertTrue(all(entry.visible for entry in collected.watch[1:]))


class TokenParserTests(unittest.TestCase):
    """Parsers de transcripts de otros CLIs (Codex, Claude Code), sobre
    fixtures sinteticos con datos inventados: nunca un transcript real, que
    traeria rutas y procesos de esta maquina.

    Cada fuente autodetecta su esquema y una linea mal formada no baja la
    lectura del resto del archivo.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from importlib.machinery import SourceFileLoader

        script = Path(__file__).resolve().parents[2] / "scripts" / "macacoview-tokens"
        if not script.exists():
            raise unittest.SkipTest("no se encontro scripts/macacoview-tokens")
        loader = SourceFileLoader("macacoview_tokens", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.tokens = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.tokens)

    def setUp(self):
        import tempfile
        from unittest.mock import patch

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.codex_dir = root / "codex" / "sessions"
        self.claude_dir = root / "claude" / "projects"
        for name, value in (
            ("CODEX_SESSIONS_DIR", self.codex_dir),
            ("CLAUDE_PROJECTS_DIR", self.claude_dir),
            ("CODEX_CLI_CACHE", root / "codex-cli.json"),
            ("CLAUDE_CACHE", root / "claude-code.json"),
        ):
            patcher = patch.object(self.tokens, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- fixtures -------------------------------------------------------

    def _codex_turn(self, usage: dict) -> str:
        return json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": usage["total"],
                        "last_token_usage": usage["last"],
                        "model_context_window": 272000,
                    },
                },
            }
        )

    def _write_codex_fixture(self, path: Path) -> None:
        turn_a = {
            "total": {
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "cache_write_input_tokens": 200,
                "output_tokens": 300,
                "reasoning_output_tokens": 150,
                "total_tokens": 1900,
            },
            "last": {
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "cache_write_input_tokens": 200,
                "output_tokens": 300,
                "reasoning_output_tokens": 150,
                "total_tokens": 1900,
            },
        }
        turn_b = {
            "total": {
                "input_tokens": 1500,
                "cached_input_tokens": 500,
                "cache_write_input_tokens": 250,
                "output_tokens": 500,
                "reasoning_output_tokens": 230,
                "total_tokens": 2750,
            },
            "last": {
                "input_tokens": 500,
                "cached_input_tokens": 100,
                "cache_write_input_tokens": 50,
                "output_tokens": 200,
                "reasoning_output_tokens": 80,
                "total_tokens": 850,
            },
        }
        lines = [
            json.dumps(
                {"type": "session_meta", "payload": {"type": "session_meta"}}
            ),
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "modelo-fixture-a", "cwd": "/tmp"},
                }
            ),
            self._codex_turn(turn_a),
            '{"payload": roto',  # linea mal formada: se saltea, no aborta
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "token_count", "rate_limits": {}},
                }
            ),  # token_count sin info: se ignora
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "modelo-fixture-b", "cwd": "/tmp"},
                }
            ),
            self._codex_turn(turn_b),
            # Esquema ajeno (estilo Claude Code dentro de un rollout de Codex):
            # el parser de Codex no tiene que contarla.
            json.dumps(
                {"type": "response_item", "message": {"usage": {"input_tokens": 99999}}}
            ),
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    def _claude_assistant(self, model: str, usage: dict) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "message": {"model": model, "usage": usage},
            }
        )

    def _write_claude_fixture(self, path: Path) -> None:
        lines = [
            json.dumps(
                {"type": "user", "message": {"role": "user", "content": "hola"}}
            ),  # sin usage: se ignora
            self._claude_assistant(
                "claude-fixture-a",
                {
                    "input_tokens": 800,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 1200,
                    "output_tokens": 450,
                    "output_tokens_details": {"thinking_tokens": 175},
                    "server_tool_use": {"web_search_requests": 0},
                },
            ),
            '{linea rota',  # mal formada: se saltea
            self._claude_assistant(
                "claude-fixture-b",
                {
                    "input_tokens": 200,
                    "cache_creation_input_tokens": 40,
                    "cache_read_input_tokens": 500,
                    "output_tokens": 100,
                    "output_tokens_details": {"thinking_tokens": 30},
                },
            ),
            # Esquema ajeno (estilo Pi, camelCase): el parser de Claude Code
            # no tiene que contarla.
            json.dumps(
                {
                    "message": {
                        "model": "pi-no-debe-contar",
                        "usage": {"input": 7, "cacheRead": 8, "totalTokens": 9},
                    }
                }
            ),
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    # -- casos ----------------------------------------------------------

    def test_codex_cli_parses_rollout_transcripts(self):
        self._write_codex_fixture(
            self.codex_dir / "2026" / "01" / "01" / "rollout-fixture.jsonl"
        )

        payload = self.tokens.collect_codex_cli()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["input"], 1500)
        self.assertEqual(payload["output"], 500)
        self.assertEqual(payload["cache_read"], 500)  # cached_input_tokens
        self.assertEqual(payload["cache_write"], 250)  # cache_write_input_tokens
        self.assertEqual(payload["reasoning"], 230)  # reasoning_output_tokens
        self.assertEqual(payload["total"], 2750)
        self.assertEqual(payload["turns"], 2)
        self.assertEqual(payload["sessions"], 1)
        models = {row["name"]: row for row in payload["models"]}
        self.assertEqual(set(models), {"modelo-fixture-a", "modelo-fixture-b"})
        self.assertEqual(models["modelo-fixture-a"]["total"], 1900)
        self.assertEqual(models["modelo-fixture-a"]["reasoning"], 150)
        self.assertEqual(models["modelo-fixture-b"]["total"], 850)
        self.assertEqual(models["modelo-fixture-b"]["cache_write"], 50)

    def test_claude_code_parses_project_transcripts(self):
        self._write_claude_fixture(
            self.claude_dir / "proyecto-fixture" / "sesion-fixture.jsonl"
        )

        payload = self.tokens.collect_claude_code()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["input"], 1000)
        self.assertEqual(payload["output"], 550)
        self.assertEqual(payload["cache_read"], 1700)  # cache_read_input_tokens
        self.assertEqual(
            payload["cache_write"], 340
        )  # cache_creation_input_tokens
        self.assertEqual(payload["reasoning"], 205)  # thinking_tokens
        self.assertEqual(payload["total"], 3590)
        self.assertEqual(payload["turns"], 2)
        self.assertEqual(payload["sessions"], 1)
        models = {row["name"]: row for row in payload["models"]}
        self.assertEqual(set(models), {"claude-fixture-a", "claude-fixture-b"})
        self.assertEqual(models["claude-fixture-a"]["reasoning"], 175)
        self.assertEqual(models["claude-fixture-a"]["cache_write"], 300)
        self.assertEqual(models["claude-fixture-b"]["total"], 840)

    def test_each_parser_autodetects_only_its_schema(self):
        # Los fixtures ya mezclan lineas ajenas; si un parser se los comiera,
        # los totales de arriba no darian. Esto lo pina explicitamente.
        self._write_codex_fixture(
            self.codex_dir / "2026" / "01" / "01" / "rollout-fixture.jsonl"
        )
        self._write_claude_fixture(
            self.claude_dir / "proyecto-fixture" / "sesion-fixture.jsonl"
        )

        codex = self.tokens.collect_codex_cli()
        claude = self.tokens.collect_claude_code()

        self.assertNotIn(99999, [row["input"] for row in codex["models"]])
        self.assertNotIn("pi-no-debe-contar", [row["name"] for row in claude["models"]])

    def test_incremental_read_only_consumes_appended_bytes(self):
        path = self.codex_dir / "2026" / "01" / "01" / "rollout-fixture.jsonl"
        self._write_codex_fixture(path)
        first = self.tokens.collect_codex_cli()
        self.assertEqual(first["total"], 2750)

        appended = {
            "total": {
                "input_tokens": 2000,
                "cached_input_tokens": 600,
                "cache_write_input_tokens": 300,
                "output_tokens": 700,
                "reasoning_output_tokens": 300,
                "total_tokens": 3600,
            },
            "last": {
                "input_tokens": 500,
                "cached_input_tokens": 100,
                "cache_write_input_tokens": 50,
                "output_tokens": 200,
                "reasoning_output_tokens": 70,
                "total_tokens": 850,
            },
        }
        with path.open("a") as handle:
            handle.write(
                self._codex_turn(appended) + "\n"
            )

        second = self.tokens.collect_codex_cli()
        self.assertEqual(second["total"], first["total"] + 850)
        self.assertEqual(second["turns"], first["turns"] + 1)
        self.assertEqual(second["reasoning"], first["reasoning"] + 70)

    def test_shrunk_file_is_reread_from_zero(self):
        path = self.claude_dir / "proyecto-fixture" / "sesion-fixture.jsonl"
        self._write_claude_fixture(path)
        first = self.tokens.collect_claude_code()
        self.assertEqual(first["total"], 3590)

        # Truncamiento/rotacion: el archivo queda con un solo turno.
        path.write_text(
            self._claude_assistant(
                "claude-fixture-a",
                {
                    "input_tokens": 800,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 1200,
                    "output_tokens": 450,
                    "output_tokens_details": {"thinking_tokens": 175},
                },
            )
            + "\n"
        )

        second = self.tokens.collect_claude_code()
        self.assertEqual(second["total"], 2750)
        self.assertEqual(second["turns"], 1)
        self.assertEqual(
            [row["name"] for row in second["models"]], ["claude-fixture-a"]
        )

    def test_missing_directory_reports_ok_with_zeros(self):
        payload = self.tokens.collect_codex_cli()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["turns"], 0)
        self.assertEqual(payload["models"], [])


if __name__ == "__main__":
    unittest.main()


class ExtensionTests(unittest.TestCase):
    """The pill is plain JS with no runtime to exercise here, so these tests
    pin the structure that broke: a user path baked into the source, a click
    that opened a one-item menu, and a class name the shell reload needs."""

    EXT = Path(__file__).resolve().parent.parent / "gnome-extension"

    def setUp(self):
        self.js = (self.EXT / "extension.js").read_text()
        self.meta = json.loads((self.EXT / "metadata.json").read_text())

    def test_no_hardcoded_user_paths(self):
        self.assertNotIn("/home/", self.js)
        self.assertIn("GLib.getenv('HOME')", self.js)

    def test_left_click_opens_app_without_a_menu(self):
        self.assertIn("button-press-event", self.js)
        self.assertIn("Clutter.EVENT_STOP", self.js)
        # The launch goes through systemd: gnome-shell gives its children no
        # session environment, so a direct spawn dies silently.
        self.assertIn("['systemctl', '--user', 'start', SERVICE]", self.js)

    def test_menu_has_no_informational_items(self):
        self.assertNotIn("_statusItem", self.js)
        self.assertNotIn("PopupSeparatorMenuItem", self.js)

    def test_shell_version_covers_the_running_shell(self):
        """A pin to one shell release silently bricks the pill on other distros.

        The extension uses ESM imports, PanelMenu.Button and
        communicate_utf8_async only, which is the GNOME 45+ API surface, so 45
        is the honest floor rather than every release we cannot test.
        """
        supported = self.meta["shell-version"]
        self.assertIn("45", supported)
        self.assertIn("50", supported)
        self.assertNotIn("42", supported, "no declare soporte de lo no probado")

        if shutil.which("gnome-shell") is None:
            # A headless CI box has no shell to match against: the release list
            # is still checked above, only the "this machine's shell is listed"
            # part needs a real GNOME session.
            self.skipTest("gnome-shell not installed on this host")
        out = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True)
        words = out.stdout.strip().split()
        # ubuntu-24.04 runners DO ship gnome-shell on PATH -- it comes in as a
        # desktop dependency -- but it answers with an empty stdout there. A
        # version that cannot be parsed is not a host without GNOME: skip, do
        # not invent a comparison.
        if out.returncode != 0 or not words or not words[-1][0].isdigit():
            self.skipTest(f"gnome-shell no reporto version: {out.stdout!r}")
        current = words[-1].split(".")[0]
        self.assertIn(current, supported, f"shell {current} no esta soportado")

    def test_class_matches_metadata_uuid(self):
        self.assertEqual(self.meta["uuid"], "macacoview@alvaro")
        # No `class` key in metadata: the shell finds the single exported
        # default class, and renaming it breaks live reloads.
        self.assertNotIn("class", self.meta)
        self.assertIn("export default class MacacoViewExtension", self.js)

    def test_parsers_survive_the_click_fix(self):
        # The collector protocol is "<used>|<total>|<chips>"; the rewrite of
        # enable() must not have touched how that gets read.
        self.assertIn("communicate_utf8_async", self.js)
        self.assertIn("split('|')", self.js)
        self.assertEqual(self.js.count("("), self.js.count(")"))


class ConfigMigrationTests(unittest.TestCase):
    """El renombre del directorio de config migrA el archivo viejo.

    Reglas del contrato: copiar, nunca mover ni borrar; no pisar una config
    nueva ya existente; sin archivo viejo no se inventa nada. Todo contra
    directorios temporales: la config real del usuario jamás se toca.
    """

    def test_migrates_old_config_when_new_is_missing(self):
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "pc-ai-monitor" / "config.toml"
            new = Path(tmp) / "macacoview" / "config.toml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("[[watch]]\nname = \"chrome\"\nmatch = ['^chrome$']\n")

            self.assertTrue(cfg.migrate_legacy_config(new, legacy))

            self.assertEqual(
                new.read_text(), legacy.read_text(), "el contenido queda igual",
            )
            self.assertTrue(
                legacy.is_file(), "el original se copia, nunca se mueve ni borra",
            )

    def test_does_not_overwrite_an_existing_new_config(self):
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "pc-ai-monitor" / "config.toml"
            new = Path(tmp) / "macacoview" / "config.toml"
            legacy.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            legacy.write_text("[[watch]]\nname = \"viejo\"\n")
            new.write_text("[[watch]]\nname = \"nuevo\"\n")

            self.assertFalse(cfg.migrate_legacy_config(new, legacy))
            self.assertEqual(
                new.read_text(), "[[watch]]\nname = \"nuevo\"\n",
                "la config nueva nunca se pisa con la vieja",
            )

    def test_without_legacy_file_nothing_happens(self):
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            new = Path(tmp) / "macacoview" / "config.toml"

            self.assertFalse(cfg.migrate_legacy_config(new, Path(tmp) / "no-existe.toml"))
            self.assertFalse(
                new.exists(), "sin nada que migrar no se crea el directorio nuevo",
            )

    def test_migration_copies_only_the_config_file(self):
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            legacy_dir = Path(tmp) / "pc-ai-monitor"
            legacy_dir.mkdir()
            legacy = legacy_dir / "config.toml"
            legacy.write_text("[ui]\ntheme = \"gentle\"\n")
            (legacy_dir / ".bak-precfg-bar").write_text("no es nuestro")
            new = Path(tmp) / "macacoview" / "config.toml"

            self.assertTrue(cfg.migrate_legacy_config(new, legacy))
            self.assertEqual(
                [p.name for p in new.parent.iterdir()], ["config.toml"],
                "solo config.toml viaja al directorio nuevo",
            )

    def test_load_runs_the_migration_for_the_default_path(self):
        import tempfile

        from pc_ai_monitor import config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "pc-ai-monitor" / "config.toml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("[[watch]]\nname = \"chrome\"\nmatch = ['^chrome$']\n")

            original_config, original_legacy = cfg.CONFIG_PATH, cfg.LEGACY_CONFIG_PATH
            cfg.CONFIG_PATH = Path(tmp) / "macacoview" / "config.toml"
            cfg.LEGACY_CONFIG_PATH = legacy
            try:
                loaded = cfg.load(cfg.CONFIG_PATH)
            finally:
                cfg.CONFIG_PATH, cfg.LEGACY_CONFIG_PATH = original_config, original_legacy

            self.assertEqual(
                tuple(entry.name for entry in loaded.watch), ("chrome",),
                "load usa la config migrada, no los defaults",
            )
            self.assertTrue(
                legacy.is_file(), "el original sigue intacto después de migrar",
            )


class StatsConfigPathTests(unittest.TestCase):
    """El colector macacoview-stats lee la ruta nueva de la config."""

    def test_stats_reads_the_renamed_config_path(self):
        import os
        import tempfile

        script = Path(__file__).resolve().parents[2] / "scripts" / "macacoview-stats"
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".config" / "macacoview" / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "[[watch]]\n"
                "name = \"grupo-de-prueba\"\n"
                "match = ['^este-proceso-no-existe$']\n"
            )
            env = dict(os.environ, HOME=tmp)
            out = subprocess.run(
                ["python3", str(script)], capture_output=True, text=True, env=env,
            )

        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertIn(
            "grupo-de-prueba", data["groups"],
            "el [[watch]] de la ruta nueva define los grupos del colector",
        )
