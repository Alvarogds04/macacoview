# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests de la página Router reconstruida (T5, S8/S11/S12).

Las reglas de datos viven puras en ``router_logica`` (sin GTK); las de
construcción y filtros usan widgets reales y necesitan display:

    xvfb-run -a python3 -m unittest tests.test_router -v

Nunca se toca la red: las lecturas al motor Gufo (:11070) van mockeadas con
los parseos reales de ``rendimiento_logica``, y los datos del router salen de
los fixtures medidos ``router_status_real_2026-11-04`` / ``router_metrics_real_2026-11-04``.
"""

import json
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

_aislamiento.isolate()

FIXTURES = Path(__file__).parent / "fixtures"
STATUS = json.loads((FIXTURES / "router_status_real_2026-11-04.json").read_text())
METRICS = json.loads((FIXTURES / "router_metrics_real_2026-11-04.json").read_text())

NOW = 10_000.0

# Texto Prometheus mínimo del motor Gufo: en curso 1, ventana KV al 31.4 %,
# especulación 300/400 borradores → MTP 0.750.
GUFO_METRICS_TEXT = (
    "llamacpp:requests_processing 1\n"
    "llamacpp:kv_cache_usage_ratio 0.314\n"
    "gufo_spec_accepted_total 300\n"
    "gufo_spec_proposed_total 400\n"
)
GUFO_MODELS_TEXT = json.dumps({"data": [{"id": "gufo", "context_length": 131072}]})
GUFO_SLOTS_TEXT = json.dumps([{"is_processing": False, "n_ctx": 131072}])


def decision(age_s, route, decider, session, confidence=None, latency=None):
    """Una decisión con ``_seen`` (lo que el colector le agrega) a ``age_s``."""
    return {
        "ts": "2026-11-04T10:00:00+0100",
        "session": session,
        "chosen": route,
        "decider": decider,
        "confidence": confidence,
        "total_latency_ms": latency,
        "_seen": NOW - age_s,
    }


SAMPLE_DECISIONS = [
    decision(30, "abito", "jev", "rtr-aaa", 0.96, 778),
    decision(120, "nan-qwen-flash", "affinity", "pi-bbb", None, 9.8),
    decision(600, "queue", "human_confirmation", "cnf-ccc"),
    decision(840, "abito", "deterministic_privacy", "rtr-ddd", 0.55, 640),
]


# ---------------------------------------------------------------------------
# router_logica: KPIs del strip, desde el fixture real de /metrics
# ---------------------------------------------------------------------------


class KpiTests(unittest.TestCase):
    def test_kpi_values_from_the_real_metrics_fixture(self):
        from pc_ai_monitor.blocks.router_logica import kpi_values

        kpis = {k["label"]: k["value"] for k in kpi_values(STATUS["router"], METRICS["telemetry"])}

        self.assertEqual(kpis["DECISIONES"], "118")
        self.assertEqual(kpis["% LOCAL"], "53.4 %")
        self.assertEqual(kpis["% CLOUD"], "37.3 %")
        self.assertEqual(kpis["% COLA"], "9.3 %")
        self.assertEqual(kpis["LATENCIA MEDIA"], "470 ms")
        self.assertEqual(kpis["CONFIANZA MEDIA"], "0.77")

    def test_kpis_tolerate_missing_values(self):
        from pc_ai_monitor.blocks.router_logica import kpi_values

        kpis = {k["label"]: k["value"] for k in kpi_values({}, {})}

        self.assertEqual(kpis["DECISIONES"], "0")
        self.assertEqual(kpis["LATENCIA MEDIA"], "—")
        self.assertEqual(kpis["CONFIANZA MEDIA"], "—")


# ---------------------------------------------------------------------------
# router_logica: filtros (ruta, decisor, ventana, búsqueda)
# ---------------------------------------------------------------------------


class FilterTests(unittest.TestCase):
    def test_filter_by_route(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        out = filter_decisions(SAMPLE_DECISIONS, route="abito", now=NOW)

        self.assertEqual([d["session"] for d in out], ["rtr-aaa", "rtr-ddd"])

    def test_filter_by_decider(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        out = filter_decisions(SAMPLE_DECISIONS, decider="affinity", now=NOW)

        self.assertEqual([d["session"] for d in out], ["pi-bbb"])

    def test_window_drops_old_decisions(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        out = filter_decisions(SAMPLE_DECISIONS, window_min=2, now=NOW)

        # Ventana de 2 minutos: la de 30 s entra, la de 120 s cae (límite
        # excluyente >120 s) y las de 600/840 s también.
        self.assertEqual([d["session"] for d in out], ["rtr-aaa"])

    def test_search_matches_session_route_and_decisor_label(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        self.assertEqual(
            [d["session"] for d in filter_decisions(SAMPLE_DECISIONS, search="cnf", now=NOW)],
            ["cnf-ccc"],
        )
        self.assertEqual(
            [d["session"] for d in filter_decisions(SAMPLE_DECISIONS, search="afinidad", now=NOW)],
            ["pi-bbb"],
        )

    def test_combined_filters_can_yield_an_empty_result(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        self.assertEqual(
            filter_decisions(SAMPLE_DECISIONS, route="abito", decider="affinity", now=NOW), []
        )

    def test_newest_first(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions

        out = filter_decisions(SAMPLE_DECISIONS, now=NOW)

        self.assertEqual(out[0]["session"], "rtr-aaa")


# ---------------------------------------------------------------------------
# router_logica: cubetas por minuto para «rutas en el tiempo»
# ---------------------------------------------------------------------------


class BucketTests(unittest.TestCase):
    def test_buckets_stack_local_cloud_and_cola(self):
        from pc_ai_monitor.blocks.router_logica import minute_buckets

        buckets = minute_buckets(SAMPLE_DECISIONS, window_min=15, now=NOW)

        self.assertEqual(len(buckets), 15)
        self.assertEqual(buckets[0]["label"], "-14m")  # la más vieja primero
        self.assertEqual(buckets[-1]["label"], "-0m")
        self.assertEqual(buckets[14]["local"], 1)  # hace 30 s → abito
        self.assertEqual(buckets[12]["cloud"], 1)  # hace 120 s → nan-* (minuto 2)
        self.assertEqual(buckets[4]["cola"], 1)  # hace 600 s → queue (minuto 10)
        self.assertEqual(buckets[0]["local"], 1)  # hace 840 s → abito (minuto 14)

    def test_buckets_respect_the_same_filters(self):
        from pc_ai_monitor.blocks.router_logica import filter_decisions, minute_buckets

        filtered = filter_decisions(SAMPLE_DECISIONS, route="abito", now=NOW)
        buckets = minute_buckets(filtered, window_min=15, now=NOW)

        self.assertEqual(sum(b["local"] for b in buckets), 2)
        self.assertEqual(sum(b["cloud"] for b in buckets), 0)

    def test_decision_without_seen_falls_in_the_newest_bucket(self):
        """Lo que acaba de llegar del colector sin marca entra como recién visto."""
        from pc_ai_monitor.blocks.router_logica import minute_buckets

        buckets = minute_buckets([{"chosen": "abito", "session": "rtr-x"}], window_min=5, now=NOW)

        self.assertEqual(buckets[-1]["local"], 1)


# ---------------------------------------------------------------------------
# router_logica: tarjetas de motor — Abito del /status, Gufo de sondas vivas
# ---------------------------------------------------------------------------


class EngineCardsTests(unittest.TestCase):
    def test_two_cards_abito_and_gufo_flash_never_a_card(self):
        from pc_ai_monitor.blocks.router_logica import engine_cards

        cards = engine_cards(STATUS, {})

        self.assertEqual([c["name"] for c in cards], ["Abito", "Gufo"])

    def test_abito_card_from_router_status(self):
        from pc_ai_monitor.blocks.router_logica import engine_cards

        abito = engine_cards(STATUS, {})[0]

        self.assertEqual(abito["port"], 11002)
        self.assertEqual(abito["lamp"], "ok")
        self.assertEqual(abito["status"], "vivo")
        self.assertEqual(abito["ctx_total"], 131072)  # L9: nunca exclusive_ctx 65536
        self.assertEqual(abito["slots"], "0/1")
        self.assertEqual(abito["queue"], 0)
        self.assertIsNone(abito["mtp"])  # /status reporta mtp_acceptance null

    def test_gufo_card_from_mocked_engine_reads(self):
        from pc_ai_monitor.blocks.rendimiento_logica import parse_metrics, parse_slots
        from pc_ai_monitor.blocks.router_logica import gufo_card

        card = gufo_card(
            parse_metrics(GUFO_METRICS_TEXT),
            131072,
            parse_slots(GUFO_SLOTS_TEXT),
        )

        self.assertEqual(card["name"], "Gufo")
        self.assertEqual(card["port"], 11070)
        self.assertEqual(card["status"], "vivo")
        self.assertEqual(card["ctx_total"], 131072)
        self.assertEqual(card["ctx_pct"], 31.4)
        self.assertEqual(card["mtp"], "0.750")
        self.assertEqual(card["queue"], 1)

    def test_gufo_without_live_reads_shows_sin_datos(self):
        from pc_ai_monitor.blocks.router_logica import gufo_card

        card = gufo_card(None, None, ())

        self.assertEqual(card["status"], "sin datos")
        self.assertEqual(card["lamp"], "off")
        self.assertIsNone(card["ctx_total"])

    def test_flash_is_a_retired_line_not_a_card(self):
        from pc_ai_monitor.blocks.router_logica import flash_line

        self.assertEqual(flash_line(STATUS), "flash · motor retirado")
        self.assertIsNone(flash_line({}))


# ---------------------------------------------------------------------------
# router_logica: confirmaciones con cuenta regresiva
# ---------------------------------------------------------------------------


class CountdownTests(unittest.TestCase):
    def test_countdown_formatting(self):
        from pc_ai_monitor.blocks.router_logica import countdown_text

        self.assertEqual(countdown_text(42), "caduca en 42s")
        self.assertEqual(countdown_text(-5), "caduca en 0s")
        self.assertEqual(countdown_text(None), "caduca en —")

    def test_pending_confirmations_row(self):
        from pc_ai_monitor.blocks.router_logica import (
            confirm_row,
            pending_confirmations,
        )

        status = dict(
            STATUS,
            pending_confirmations=[
                {
                    "kind": "desborde a cloud",
                    "id": "cnf-abc123",
                    "expires_in_s": 90,
                    "reason": "contexto 118k > 65k de Abito",
                    "options": [{"route": "nan-qwen-flash", "note": "cloud permitido"}],
                    "default": "queue",
                }
            ],
        )

        pending = pending_confirmations(status)
        self.assertEqual(len(pending), 1)
        row = confirm_row(pending[0])
        self.assertEqual(row["kind"], "desborde a cloud")
        self.assertEqual(row["id"], "cnf-abc123")
        self.assertEqual(row["countdown"], "caduca en 90s")
        self.assertEqual(row["default"], "queue")

    def test_nothing_pending_message(self):
        from pc_ai_monitor.blocks.router_logica import (
            NOTHING_PENDING_TEXT,
            pending_confirmations,
        )

        self.assertEqual(pending_confirmations(STATUS), [])
        self.assertEqual(NOTHING_PENDING_TEXT, "nada pendiente de tu decisión")


# ---------------------------------------------------------------------------
# router_logica: «router no responde», ciclo de vida flash, teselas, sesiones
# ---------------------------------------------------------------------------


class StateTests(unittest.TestCase):
    def test_router_down_state(self):
        from pc_ai_monitor.blocks.router_logica import NO_RESPONSE_TEXT, router_down
        from pc_ai_monitor.datos import Snapshot

        self.assertTrue(router_down(Snapshot()))
        self.assertFalse(router_down(Snapshot(router_status=STATUS)))
        self.assertEqual(NO_RESPONSE_TEXT, "router no responde en :11009")

    def test_flash_lifecycle_compact_legacy_line(self):
        from pc_ai_monitor.blocks.router_logica import (
            FLASH_LEGACY_TITLE,
            flash_lifecycle_line,
        )

        line = flash_lifecycle_line(STATUS["flash_lifecycle"]) or ""

        self.assertIn("down", line)
        self.assertIn("10 min", line)
        self.assertIn("inicio", line)
        self.assertEqual(FLASH_LEGACY_TITLE, "CICLO DE VIDA DE FLASH (MOTOR RETIRADO)")

    def test_tiles_jev_cloud_engram(self):
        from pc_ai_monitor.blocks.router_logica import tiles

        tiles_out = {t["key"]: t for t in tiles(STATUS, METRICS["telemetry"])}

        self.assertEqual(set(tiles_out), {"jev", "cloud", "engram"})
        self.assertEqual(tiles_out["jev"]["lamp"], "ok")
        self.assertIn("con clave", tiles_out["jev"]["value"])
        # OpenAI sin credencial: CLOUD enciende ámbar, no verde.
        self.assertEqual(tiles_out["cloud"]["lamp"], "warn")
        self.assertIn("LiteLLM vivo", tiles_out["cloud"]["value"])
        self.assertEqual(tiles_out["engram"]["lamp"], "ok")
        self.assertIn("3.6.0", tiles_out["engram"]["value"])

    def test_tiles_survive_an_empty_status(self):
        from pc_ai_monitor.blocks.router_logica import tiles

        tiles_out = tiles({}, {})

        self.assertEqual(len(tiles_out), 3)
        self.assertTrue(all(t["lamp"] in ("ok", "warn", "crit") for t in tiles_out))
        # S11: sin lectura no hay estado inventado — «sin datos», no «caído».
        self.assertEqual([t["value"] for t in tiles_out], ["—", "—", "sin respuesta"])

    def test_sessions_rows_compact(self):
        from pc_ai_monitor.blocks.router_logica import sessions_rows

        rows = sessions_rows(
            [
                {
                    "short_id": "pi-7f2c",
                    "workflow": "hybrid",
                    "route": "local",
                    "estimated_context": 18600,
                    "age_s": 372,
                }
            ]
        )

        self.assertEqual(rows[0]["id"], "pi-7f2c")
        self.assertEqual(rows[0]["route"], "local")
        self.assertEqual(rows[0]["age"], "6m")
        self.assertEqual(rows[0]["ctx"], 18600)
        self.assertEqual(sessions_rows([]), [])


# ---------------------------------------------------------------------------
# router_logica: filas de la tabla de decisiones
# ---------------------------------------------------------------------------


class DecisionRowsTests(unittest.TestCase):
    def test_rows_from_stamped_decisions(self):
        from pc_ai_monitor.blocks.router_logica import decision_rows

        rows = decision_rows(SAMPLE_DECISIONS, now=NOW)

        self.assertEqual(rows[0]["sesion"], "rtr-aaa")
        self.assertEqual(rows[0]["hace"], "30s")
        self.assertEqual(rows[0]["decisor"], "JEV")
        self.assertEqual(rows[0]["confianza"], "0.96")
        self.assertEqual(rows[0]["latencia"], "778 ms")
        self.assertEqual(rows[1]["confianza"], "—")

    def test_rows_from_the_real_fixture_without_seen(self):
        from pc_ai_monitor.blocks.router_logica import decision_rows, with_seen

        rows = decision_rows(with_seen(METRICS["recent"], NOW), now=NOW)

        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["sesion"], "cnf-fdd61157")
        self.assertEqual(rows[0]["decisor"], "confirmación humana")
        self.assertEqual(rows[0]["confianza"], "—")

    def test_duration_text(self):
        from pc_ai_monitor.blocks.router_logica import duration_text

        self.assertEqual(duration_text(30), "30s")
        self.assertEqual(duration_text(125), "2m")
        self.assertEqual(duration_text(3700), "1h 01m")


# ---------------------------------------------------------------------------
# Widgets (xvfb): la página construye y los filtros cambian las filas visibles
# ---------------------------------------------------------------------------


class WidgetRouterTests(unittest.TestCase):
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

    def _snapshot(self):
        import time

        from pc_ai_monitor.blocks.router_logica import with_seen
        from pc_ai_monitor.datos import Snapshot

        return Snapshot(
            router_status=STATUS,
            router_metrics=METRICS,
            decisions=tuple(with_seen(METRICS["recent"], time.monotonic())),
        )

    def _page(self):
        import pc_ai_monitor.blocks.router as router_blocks
        from pc_ai_monitor.pages.router import RouterPage

        with mock_http_none(router_blocks):
            page = RouterPage()
        return page

    def test_page_builds_and_filters_change_visible_rows(self):
        import time

        from pc_ai_monitor.datos import Snapshot

        page = self._page()
        with mock_http_none(page_module()):
            page.update_snapshot(self._snapshot())

        decisiones = page._blocks[1]
        self.assertEqual(decisiones.visible_rows(), 10)

        # Filtro de búsqueda: reduce las filas visibles de la tabla.
        decisiones._search.set_text("cnf-fdd61157")
        self.assertEqual(decisiones.visible_rows(), 1)
        decisiones._search.set_text("")
        self.assertEqual(decisiones.visible_rows(), 10)

        # Ventana de 5 minutos: las decisiones de más de 5 min desaparecen.
        decisiones._win_buttons[0].set_active(True)
        stamped = [
            {**entry, "_seen": time.monotonic() - 60 * i}
            for i, entry in enumerate(METRICS["recent"])
        ]
        page.update_snapshot(Snapshot(router_status=STATUS, router_metrics=METRICS, decisions=tuple(stamped)))
        self.assertLess(decisiones.visible_rows(), 10)
        self.assertGreater(decisiones.visible_rows(), 0)

    def test_kpi_strip_has_six_live_values(self):
        page = self._page()
        with mock_http_none(page_module()):
            page.update_snapshot(self._snapshot())

        kpis = page._blocks[0]
        values = [box.get_text() for box in kpis._strip.value_labels()]
        self.assertEqual(len(values), 6)
        self.assertEqual(values[0], "118")
        self.assertEqual(values[1], "53.4 %")

    def test_router_down_dims_the_page(self):
        from pc_ai_monitor.datos import Snapshot

        page = self._page()
        with mock_http_none(page_module()):
            page.update_snapshot(Snapshot())

        self.assertTrue(page._banner.get_visible())
        self.assertEqual(page._banner.get_text(), "router no responde en :11009")
        self.assertLess(page._body.get_opacity(), 1.0)

        with mock_http_none(page_module()):
            page.update_snapshot(self._snapshot())

        self.assertFalse(page._banner.get_visible())
        self.assertEqual(page._body.get_opacity(), 1.0)

    def test_engine_cards_render_abito_gufo_and_the_retired_flash_line(self):
        from pc_ai_monitor.blocks.rendimiento_logica import parse_metrics, parse_slots
        from pc_ai_monitor.blocks.router_logica import gufo_card

        page = self._page()
        with mock_http_none(page_module()):
            page.update_snapshot(self._snapshot())

        engines = page._blocks[2]
        self.assertEqual(sorted(engines._cards), ["abito", "gufo"])
        self.assertIn("flash · motor retirado", engines._flash.get_text())

        card = gufo_card(
            parse_metrics(GUFO_METRICS_TEXT), 131072, parse_slots(GUFO_SLOTS_TEXT)
        )
        engines._apply_gufo(card)

        self.assertIn("vivo", engines._cards["gufo"]._status.get_text())
        self.assertIn("31 % · 131.1k", engines._cards["gufo"]._ctx_value.get_text())

    def test_gufo_worker_uses_mocked_reads_not_the_network(self):
        import pc_ai_monitor.blocks.router as router_blocks

        engines = self._page()._blocks[2]
        texts = {
            "/metrics": GUFO_METRICS_TEXT,
            "/v1/models": GUFO_MODELS_TEXT,
            "/slots": GUFO_SLOTS_TEXT,
        }

        with mock.patch.object(
            router_blocks, "_http_text", side_effect=lambda path, timeout=3.0: texts[path]
        ):
            card = engines._gufo_worker()

        self.assertEqual(card["status"], "vivo")
        self.assertEqual(card["ctx_total"], 131072)

    def test_confirmations_render_countdown_and_empty_state(self):
        from gi.repository import Gtk

        from pc_ai_monitor.datos import Snapshot

        page = self._page()
        with mock_http_none(page_module()):
            page.update_snapshot(self._snapshot())

        confirmations = page._blocks[3]
        first = next(iter(confirmations._conf_box.observe_children()))
        self.assertIn("nada pendiente", first.get_text())

        status = dict(
            STATUS,
            pending_confirmations=[
                {
                    "kind": "desborde a cloud",
                    "id": "cnf-abc123",
                    "expires_in_s": 90,
                    "reason": "contexto 118k > 65k",
                    "options": [{"route": "nan-qwen-flash", "note": ""}],
                    "default": "queue",
                }
            ],
        )
        with mock_http_none(page_module()):
            page.update_snapshot(Snapshot(router_status=status, router_metrics=METRICS))

        widgets = confirmations._conf_box.observe_children()
        texts = []
        for child in widgets:
            if isinstance(child, Gtk.Label):
                texts.append(child.get_text())
            elif isinstance(child, Gtk.Box):
                texts.extend(label.get_text() for label in child.observe_children())
        joined = "\n".join(texts)
        self.assertIn("cnf-abc123", joined)
        self.assertIn("caduca en", joined)
# ---------------------------------------------------------------------------
# Doble de las lecturas HTTP al motor: sin red en los tests de widgets
# ---------------------------------------------------------------------------


def page_module():
    import pc_ai_monitor.blocks.router as router_blocks

    return router_blocks


def mock_http_none(module):
    """Parchea ``_http_text`` del módulo de bloques del router: responde None."""
    return mock.patch.object(module, "_http_text", return_value=None)


class CountdownTimerTests(unittest.TestCase):
    """La cuenta regresiva sigue viva aunque la página no esté a la vista."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        if not Gtk.init_check():
            raise unittest.SkipTest("sin display")

    def test_tick_keeps_the_timer_while_the_page_is_hidden(self):
        from pc_ai_monitor.blocks.router import ConfirmRowBlock

        block = ConfirmRowBlock()
        self.assertFalse(block.get_mapped())
        # True = GLib conserva la fuente; False la borraría para siempre.
        self.assertTrue(block._tick())
        block._on_destroy()

    def test_destroy_after_a_finished_timer_does_not_remove_a_dead_source(self):
        from unittest import mock

        from pc_ai_monitor.blocks import router as router_blocks

        block = router_blocks.ConfirmRowBlock()
        with mock.patch.object(router_blocks.GLib, "source_remove") as remove:
            block._on_destroy()
            block._on_destroy()
        self.assertEqual(remove.call_count, 1)
