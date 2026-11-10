# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests de la página Home: el cockpit de MacacoView (diseño 07, T3).

Las reglas de datos se testean puras (``historia_motor`` y
``home_logica``); las de construcción y atajos construyen GTK real, así que
necesitan display — aunque sea virtual:

    xvfb-run -a python3 -m unittest tests.test_home -v

Nunca se llama al administrador ni al motor de verdad: los tests de widgets
inyectan un poller falso y mockean subprocesos y diálogos.
"""

import json
import threading
import time
import unittest
from unittest import mock

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento
from gi.repository import Gtk

from pc_ai_monitor.blocks.engine_logica import (
    CONFIRMATION_TEXT,
    CliResult,
    parse_engine_status,
)
from pc_ai_monitor.blocks.rendimiento_logica import (
    Guard,
    Metrics,
    SpeedTracker,
    speed_text,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.widgets import instrumentos_logica as logica

_aislamiento.isolate()

# ---------------------------------------------------------------------------
# Fijaciones compartidas
# ---------------------------------------------------------------------------

GUFO_TAG = "gufo:v0.28.0-r2"
GUFO_TAG_NUEVA = "gufo:v0.28.1-r1"

MODELS = [
    {
        "alias": "gufo",
        "pid": 101,
        "port": 11070,
        "model": "gufo.gguf",
        "rss_gib": 12.1,
        "cpu": 3.0,
        "gtt_gib": 49.2,
        "vram_gib": 0.0,
    },
    {
        "alias": "abito",
        "pid": 102,
        "port": 11002,
        "model": "abito.gguf",
        "rss_gib": 19.8,
        "cpu": 1.0,
        "gtt_gib": 0.4,
        "vram_gib": 0.0,
    },
]

STATUS_UPDATABLE = json.dumps(
    {
        "component": "engine-gufo",
        "running": {
            "tag": GUFO_TAG,
            "version_label": "0.28.0",
            "revision_label": "r2",
            "context": "131072",
            "port": "11070",
            "serving": True,
        },
        "available": {"tag": GUFO_TAG_NUEVA, "version_label": "0.28.1"},
        "updatable": True,
    }
)

STATUS_AL_DIA = json.dumps(
    {
        "component": "engine-gufo",
        "running": {
            "tag": GUFO_TAG,
            "version_label": "0.28.0",
            "revision_label": "r2",
            "context": "131072",
            "port": "11070",
            "serving": True,
        },
        "available": {"tag": GUFO_TAG_NUEVA, "version_label": "0.28.1"},
        "updatable": False,
        "reason": "ya corre la más nueva",
    }
)


def home_snapshot(models=None, memory=None):
    """Snapshot representativo: RAM de 122 GiB con los dos motores medidos."""
    return Snapshot(
        stats={
            "memory": memory
            or {
                "total_gib": 122.0,
                "used_gib": 71.3,
                "available_gib": 51.4,
                "swap_total_gib": 0.0,
                "swap_used_gib": 0.0,
            },
            "models": MODELS if models is None else models,
        }
    )


RESIDENT_GUFO = {
    "resident": "gufo",
    "abito": {"healthy": False},
    "gufo": {"context": 131072},
}


def sample(
    t, resident: str | None = "gufo", decode=None, prefill=None, kv=None, cache=None
):
    from pc_ai_monitor.blocks.historia_motor import Sample

    return Sample(t, resident, decode, prefill, kv, cache)


# ---------------------------------------------------------------------------
# blocks/historia_motor: buffer acotado de 15 minutos
# ---------------------------------------------------------------------------


class HistoriaTests(unittest.TestCase):
    """Reglas del buffer: acotado a 900 s, huecos y eventos, crash sin switch."""

    def test_buffer_bounds_to_900_seconds(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        for index in range(200):
            history.record(sample(index * 10.0, "gufo", decode=10.0 + index))

        samples = history.samples()
        self.assertLessEqual(samples[0].t, samples[-1].t - 900.0 + 10.0)
        self.assertTrue(all(s.t >= samples[-1].t - 900.0 for s in samples))

    def test_records_gaps_when_no_engine_is_resident(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        history.record(sample(0.0, "gufo"))
        history.record(sample(10.0, None))

        self.assertIsNone(history.samples()[-1].resident)

    def test_records_events_with_valid_kinds(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        event = history.record_event("switch", "cambio a Abito", t=15.0)

        self.assertEqual(event.kind, "switch")
        self.assertEqual(history.events()[-1].detail, "cambio a Abito")

    def test_rejects_unknown_event_kinds(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        with self.assertRaises(ValueError):
            history.record_event("cafe", "no es un evento")

    def test_crash_is_recorded_when_the_engine_dies_without_a_switch(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        history.record(sample(0.0, "gufo"))
        event = history.record(sample(30.0, None))

        assert event is not None
        self.assertEqual(event.kind, "crash")
        self.assertEqual(event.engine, "gufo")

    def test_no_crash_when_a_switch_explains_the_gap(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory

        history = MotorHistory()
        history.record(sample(0.0, "gufo"))
        history.record_event("switch", "cambio a Abito", t=10.0)
        event = history.record(sample(30.0, None))

        self.assertIsNone(event)
        kinds = [e.kind for e in history.events()]
        self.assertEqual(kinds, ["switch"])

    def test_band_segments_follow_the_resident(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory, band_segments

        history = MotorHistory()
        history.record(sample(0.0, "gufo"))
        history.record(sample(60.0, None))
        history.record(sample(120.0, "abito"))

        kinds = [segment.kind for segment in band_segments(history.samples())]
        self.assertEqual(kinds, ["gufo", "gap", "abito"])

    def test_lane_points_split_on_gaps(self):
        from pc_ai_monitor.blocks.historia_motor import lane_points

        points = lane_points(
            (sample(0.0, decode=5.0), sample(10.0, decode=6.0), sample(20.0)),
            "decode",
        )

        self.assertEqual(points, ((0.0, 5.0), (10.0, 6.0), (20.0, None)))


# ---------------------------------------------------------------------------
# blocks/home_logica: mapeo de datos del cockpit
# ---------------------------------------------------------------------------


class RamBlocksTests(unittest.TestCase):
    """S9: figura medida + reserva; S11: nunca el motor retirado."""

    def test_two_measured_figures_with_reservations(self):
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        blocks = ram_blocks(home_snapshot(), "gufo")
        labels = [block.label for block in blocks]

        self.assertEqual(labels, ["Gufo", "Otros", "Abito", "Libre"])
        gufo, _otros, abito, _libre = blocks
        self.assertEqual(gufo.used_gib, 49.2)
        self.assertEqual(gufo.reserved_gib, 85.0)
        self.assertEqual(gufo.kind, "active")
        self.assertEqual(abito.used_gib, 19.8)
        # L21.3: la reserva sólo se pinta para el residente (o uno cargando);
        # Abito medido pero no residente no reserva nada.
        self.assertEqual(abito.reserved_gib, 0.0)
        self.assertEqual(abito.kind, "idle")

    def test_tooltip_states_both_figures(self):
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        gufo = ram_blocks(home_snapshot(), "gufo")[0]
        text = logica.ram_tooltip(gufo)

        self.assertIn("49.2", text)
        self.assertIn("85", text)

    def test_resident_without_measurement_falls_back_to_the_reservation(self):
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        gufo = ram_blocks(home_snapshot(models=[]), "gufo")[0]

        self.assertEqual(gufo.used_gib, 85.0)
        self.assertEqual(gufo.kind, "active")

    def test_down_engine_is_dashed_with_zero_use(self):
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        gufo = ram_blocks(home_snapshot(models=[]), None)[0]

        self.assertEqual(gufo.used_gib, 0.0)
        self.assertEqual(gufo.kind, "idle")

    def test_no_resident_draws_no_reservation_live(self):
        """L21.3 (fixtures live_2026-11-05, resident "none", unidades
        inactivas): sin motor residente NADA está reservado — Gufo 85 GiB y
        Abito 20 GiB no se pintan como memoria held; los motores parados no
        toman ancho en la barra (uso medido + libre)."""
        from pathlib import Path

        from pc_ai_monitor.blocks.home_logica import ram_blocks

        live = json.loads(
            (
                Path(__file__).parent / "fixtures/live_2026-11-05/resident.json"
            ).read_text()
        )
        self.assertEqual(live["resident"], "none")
        self.assertEqual(live["gufo"]["unit"], "inactive")
        self.assertEqual(live["abito"]["unit"], "inactive")

        blocks = ram_blocks(
            Snapshot(
                stats={
                    "memory": {"total_gib": 122.0, "used_gib": 18.0},
                    "models": [],  # unidades inactivas: ningún proceso de motor
                }
            ),
            live["resident"],
        )
        by_label = {block.label: block for block in blocks}
        self.assertEqual(by_label["Gufo"].used_gib, 0.0)
        self.assertEqual(by_label["Gufo"].reserved_gib, 0.0)
        self.assertEqual(by_label["Abito"].used_gib, 0.0)
        self.assertEqual(by_label["Abito"].reserved_gib, 0.0)
        self.assertEqual(by_label["Gufo"].sublabel, "")
        self.assertEqual(by_label["Abito"].sublabel, "")

        # La barra reparte el ancho real: parados 0, libre el resto.
        rects = logica.ram_layout(blocks, 560.0)
        self.assertLessEqual(sum(rect.width for rect in rects), 560.0)
        widths = {rect.block.label: rect.width for rect in rects}
        self.assertEqual(widths["Gufo"], 0.0)
        self.assertEqual(widths["Abito"], 0.0)
        self.assertGreater(widths["Libre"], 0.0)

    def test_resident_gufo_still_draws_the_85_reservation(self):
        """S9 «los 2»: con Gufo residente la reserva de 85 GiB sigue
        pintándose (rayado) junto a la cifra medida."""
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        blocks = ram_blocks(home_snapshot(), "gufo")
        self.assertEqual(blocks[0].reserved_gib, 85.0)
        rects = logica.ram_layout(blocks, 560.0)
        self.assertGreater(rects[0].reserved_width, 0.0)

    def test_retired_flash_never_gets_a_block(self):
        """L9: la entrada «flash» del router (:11008) es el motor retirado."""
        from pc_ai_monitor.blocks.home_logica import ram_blocks

        models = MODELS + [
            {
                "alias": "flash",
                "pid": 103,
                "port": 11008,
                "model": "old.gguf",
                "rss_gib": 77.0,
                "cpu": 0.0,
                "gtt_gib": 0.0,
                "vram_gib": 0.0,
            }
        ]
        labels = [block.label for block in ram_blocks(home_snapshot(models), None)]

        self.assertEqual(labels, ["Gufo", "Otros", "Abito", "Libre"])


class MiniCardsTests(unittest.TestCase):
    """S3/S4: dos mini tarjetas de motor, compactas, sin flash (L9)."""

    def test_two_cards_abito_and_gufo_only(self):
        from pc_ai_monitor.blocks.home_logica import mini_cards

        info = dict(RESIDENT_GUFO, flash={"healthy": True, "port": 11008})
        cards = mini_cards(info)

        self.assertEqual([card["engine"] for card in cards], ["abito", "gufo"])

    def test_active_engine_is_highlighted_and_others_off(self):
        from pc_ai_monitor.blocks.home_logica import mini_cards

        cards = {card["engine"]: card for card in mini_cards(RESIDENT_GUFO)}

        self.assertEqual(cards["gufo"]["lamp"], "ok")
        self.assertTrue(cards["gufo"]["active"])
        self.assertEqual(cards["abito"]["lamp"], "off")
        self.assertFalse(cards["abito"]["active"])

    def test_ctx_shows_the_live_context_not_the_stale_payload(self):
        """L9: Abito sirve 131072; el 65536 del payload está desactualizado."""
        from pc_ai_monitor.blocks.home_logica import mini_cards

        cards = {card["engine"]: card for card in mini_cards(RESIDENT_GUFO)}

        self.assertEqual(cards["abito"]["ctx"], 131072)
        self.assertEqual(cards["gufo"]["ctx"], 131072)

    def test_down_engine_shows_the_crash(self):
        from pc_ai_monitor.blocks.home_logica import mini_cards

        cards = {card["engine"]: card for card in mini_cards(None, ("gufo",))}

        self.assertEqual(cards["gufo"]["lamp"], "down")
        self.assertEqual(cards["gufo"]["status"], "caído")


class UpdateCardTests(unittest.TestCase):
    """S5/S6: un estado, una versión humana, etiquetas sólo en tooltips."""

    def setUp(self):
        self.status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        self.available = "0.28.1"

    def test_updatable_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        card = update_card(self.status, self.available)

        self.assertEqual(card.pill, "1 actualización")
        self.assertEqual(card.rail_status, "nueva")
        self.assertEqual(card.from_label, "0.28.0")
        self.assertEqual(card.to_label, "0.28.1")
        self.assertEqual(card.button_label, "Actualizar a 0.28.1")
        self.assertTrue(card.button_enabled)

    def test_up_to_date_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        card = update_card(
            parse_engine_status(CliResult(0, stdout=STATUS_AL_DIA)), self.available
        )

        self.assertEqual(card.pill, "Todo al día")
        self.assertEqual(card.rail_status, "al_dia")
        self.assertFalse(card.button_enabled)

    def test_updating_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        card = update_card(self.status, self.available, updating=True)

        self.assertEqual(card.pill, "Actualizando…")
        self.assertEqual(card.rail_status, "actualizando")
        self.assertFalse(card.button_enabled)

    def test_reverted_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        card = update_card(self.status, self.available, last_update_rc=1)

        self.assertEqual(card.pill, "Revertida")
        self.assertEqual(card.rail_status, "revertida")

    def test_missing_manager_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        status = parse_engine_status(CliResult(None, failure="missing"))
        card = update_card(status, None)

        self.assertEqual(card.pill, "No se pudo consultar")
        self.assertEqual(card.rail_status, "sin_consulta")
        self.assertFalse(card.button_enabled)

    def test_failed_query_state(self):
        from pc_ai_monitor.blocks.home_logica import update_card

        status = parse_engine_status(CliResult(3, stderr="boom"))
        card = update_card(status, None)

        self.assertEqual(card.pill, "No se pudo consultar")

    def test_labels_never_show_tags_or_revisions(self):
        """S5: tag y revisión sólo en tooltips; en pantalla, versión humana."""
        from pc_ai_monitor.blocks.home_logica import update_card

        card = update_card(self.status, self.available)
        visible = f"{card.pill} {card.from_label} {card.to_label} {card.button_label}"

        self.assertNotIn(GUFO_TAG, visible)
        self.assertNotIn(GUFO_TAG_NUEVA, visible)
        self.assertNotIn("r2", card.from_label)
        self.assertIn(GUFO_TAG, card.rail_tooltip)
        self.assertIn(GUFO_TAG_NUEVA, card.pill_tooltip)

    def test_parse_available_version_reads_the_human_label(self):
        from pc_ai_monitor.blocks.home_logica import parse_available_version

        self.assertEqual(parse_available_version(STATUS_UPDATABLE), "0.28.1")
        self.assertEqual(parse_available_version(STATUS_AL_DIA), "0.28.1")
        self.assertIsNone(parse_available_version("no soy json"))
        # Sin etiqueta humana disponible no se inventa: el tag no sale en pantalla.
        payload = json.dumps({"available": {"tag": GUFO_TAG_NUEVA}})
        self.assertEqual(parse_available_version(payload), "")


class HeadroomTests(unittest.TestCase):
    """Margen RAM: disponible y conservadora, con sus mínimos observados."""

    def test_both_rows_with_guard_minima(self):
        from pc_ai_monitor.blocks.home_logica import headroom_rows

        guard = Guard(
            mem_available_gib=51.4,
            conservative_gib=40.2,
            min_mem_available_gib=15.0,
            min_conservative_gib=12.0,
        )
        disponible, conservadora = headroom_rows(home_snapshot().memory(), guard)

        self.assertEqual(disponible.label, "disponible")
        self.assertEqual(disponible.value_gib, 51.4)
        self.assertEqual(disponible.min_gib, 15.0)
        self.assertEqual(conservadora.value_gib, 40.2)
        self.assertEqual(conservadora.min_gib, 12.0)
        self.assertEqual(disponible.total_gib, 122.0)

    def test_low_when_below_the_minimum(self):
        from pc_ai_monitor.blocks.home_logica import headroom_rows

        memory = dict(home_snapshot().memory(), available_gib=11.0)
        guard = Guard(min_mem_available_gib=15.0)
        disponible, _ = headroom_rows(memory, guard)

        self.assertTrue(disponible.low)

    def test_conservative_without_guard_has_no_data(self):
        from pc_ai_monitor.blocks.home_logica import headroom_rows

        _disponible, conservadora = headroom_rows(home_snapshot().memory(), None)

        self.assertIsNone(conservadora.value_gib)

    def test_mem_head_is_short(self):
        from pc_ai_monitor.blocks.home_logica import mem_head

        self.assertEqual(mem_head(home_snapshot().memory()), "51.4 GiB disponibles")


class InventoryRowTests(unittest.TestCase):
    """Filas compactas del inventario: lamp, ctx, puerto y acción de energía."""

    def test_healthy_row(self):
        from pc_ai_monitor.blocks.home_logica import inventory_row

        row = inventory_row(
            {"model": "abito", "port": 11002, "ctx": 65536, "healthy": True}
        )

        self.assertEqual(row["state"], "encendido")
        self.assertEqual(row["lamp"], "ok")
        self.assertEqual(row["action"], "stop")
        self.assertEqual(row["fraction"], 0.5)

    def test_stopped_row(self):
        from pc_ai_monitor.blocks.home_logica import inventory_row

        row = inventory_row(
            {"model": "gufo", "port": 11070, "ctx": 131072, "healthy": False}
        )

        self.assertEqual(row["state"], "detenido")
        self.assertEqual(row["action"], "ensure")
        self.assertEqual(row["lamp"], "off")

    def test_problem_row_is_blocked(self):
        from pc_ai_monitor.blocks.home_logica import inventory_row

        row = inventory_row(
            {
                "model": "gufo",
                "port": 11070,
                "ctx": 131072,
                "healthy": False,
                "problem": "missing_port",
            }
        )

        self.assertEqual(row["state"], "no utilizable")
        self.assertTrue(row["blocked"])

    def test_manager_down_disables_the_row(self):
        from pc_ai_monitor.blocks.home_logica import inventory_row

        row = inventory_row(
            {"model": "abito", "port": 11002, "ctx": 131072, "healthy": True},
            manager_ok=False,
        )

        self.assertEqual(row["state"], "estado no disponible")
        self.assertTrue(row["blocked"])


class SpeedsTests(unittest.TestCase):
    """S11: deltas de contadores; una sola lectura nunca es 0."""

    def test_gpu_lamp_states(self):
        from pc_ai_monitor.blocks.home_logica import gpu_lamp_state

        self.assertEqual(gpu_lamp_state(None), "off")
        self.assertEqual(gpu_lamp_state(0.0), "ok")
        self.assertEqual(gpu_lamp_state(2.0), "down")

    def test_mtp_ratio_from_spec_counters(self):
        from pc_ai_monitor.blocks.home_logica import mtp_ratio

        metrics = parse_metrics_fixture()
        self.assertEqual(mtp_ratio(metrics), 840 / 1200)
        self.assertIsNone(mtp_ratio(Metrics()))

    def test_one_reading_is_sin_datos_never_zero(self):
        tracker = SpeedTracker()
        self.assertIsNone(tracker.update(2456.0, time.monotonic()))
        self.assertIsNone(tracker.last)
        self.assertEqual(speed_text(tracker.last), "sin datos recientes")


def parse_metrics_fixture():
    from pc_ai_monitor.blocks.rendimiento_logica import parse_metrics

    return parse_metrics(
        'gufo_spec_drafts_proposed_total{slot="0"} 1200\n'
        'gufo_spec_drafts_accepted_total{slot="0"} 840\n'
        'llamacpp:kv_cache_usage_ratio{slot="0"} 0.25\n'
        "llamacpp:prompt_tokens_cached_total 38900\n"
        "llamacpp:prompt_tokens_total 43512\n"
    )


# ---------------------------------------------------------------------------
# Widgets (xvfb): construcción, atajos y render de la página
# ---------------------------------------------------------------------------


class FakePoller:
    """Doble del poller compartido: estado fijo, sin subprocesos ni HTTP."""

    def __init__(self):
        from pc_ai_monitor.blocks.historia_motor import MotorHistory
        from pc_ai_monitor.blocks.home_logica import HomeState

        self.state = HomeState()
        self.history = MotorHistory()
        self.subscribers = []
        self.switch_begun = []
        self.update_begun = False

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def refresh(self, _snapshot, force=False):
        del force

    def dispatch(self):
        for callback in tuple(self.subscribers):
            callback()

    def begin_switch(self, target):
        self.switch_begun.append(target)
        self.state.switching = target

    def end_switch(self, ok, message, info=None):
        del ok, message
        self.state.switching = None
        if info is not None:
            self.state.resident_info = info

    def begin_update(self):
        self.update_begun = True
        self.state.updating = True

    def end_update(self, returncode, message, status=None):
        self.state.updating = False
        self.state.last_update_rc = returncode
        self.state.update_message = message
        if status is not None:
            self.state.status = status


class FakeAdwDialog:
    """Doble de Adw.MessageDialog que registra su texto y respuestas."""

    def __init__(self, *args, **kwargs):
        self.heading = kwargs.get("heading", "")
        self.body = kwargs.get("body", "")
        self.responses = []
        self._handler = None

    def add_response(self, name, _label):
        self.responses.append(name)

    def set_response_appearance(self, _name, _appearance):
        pass

    def set_default_response(self, _name):
        pass

    def set_close_response(self, _name):
        pass

    def connect(self, _signal, handler):
        self._handler = handler

    def present(self):
        pass

    def respond(self, name):
        if self._handler is not None:
            self._handler(self, name)


class FakeGtkDialog:
    """Doble de Gtk.MessageDialog (confirmación de actualización)."""

    def __init__(self, *args, **kwargs):
        self.text = kwargs.get("text", "")
        # GTK 4: propiedad «secondary-text»; sin format_secondary_text (GTK 3).
        self.secondary = kwargs.get("secondary_text", "")
        self._handler = None

    def add_button(self, _label, _response):
        pass

    def connect(self, _signal, handler):
        self._handler = handler

    def present(self):
        pass

    def close(self):
        pass

    def respond(self, response):
        if self._handler is not None:
            self._handler(self, response)


class WidgetHomeTests(unittest.TestCase):
    """La página construye sus tres filas y renderiza estados del poller."""

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

    def _page(self, poller=None):
        import pc_ai_monitor.blocks.home as home_blocks
        from pc_ai_monitor.pages.home import HomePage

        poller = poller or FakePoller()
        with mock.patch.object(home_blocks, "POLLER", poller):
            page = HomePage()
        return page, poller

    def test_home_builds_the_three_rows(self):
        from pc_ai_monitor.blocks.home import (
            InventarioRowBlock,
            MotorRowBlock,
            RendimientoRowBlock,
            UpdateCardBlock,
        )
        from pc_ai_monitor.widgets.instrumentos import (
            Lanes,
            MotorBand,
            RamBlocks,
            Ring,
            VersionRail,
        )

        page, _poller = self._page()

        self.assertEqual(
            [type(block) for block in page._blocks],
            [MotorRowBlock, RendimientoRowBlock, InventarioRowBlock, UpdateCardBlock],
        )
        motor, rendimiento, inventario, update = page._blocks
        self.assertIsInstance(motor._ram, RamBlocks)
        self.assertIsInstance(motor._band, MotorBand)
        self.assertIsInstance(rendimiento._lanes, Lanes)
        self.assertIsInstance(rendimiento._cache_ring, Ring)
        self.assertIsInstance(rendimiento._kv_ring, Ring)
        self.assertIsInstance(rendimiento._mtp_dial, logica and object)
        self.assertTrue(hasattr(inventario, "_headroom_box"))
        self.assertIsInstance(update._rail, VersionRail)

    def test_motor_row_renders_measured_ram_and_resident(self):
        page, poller = self._page()
        poller.state.resident_info = RESIDENT_GUFO
        page._blocks[0]._render()

        # Residente sin medición del snapshot todavía: la figura del
        # residente (la reserva, 85 GiB) es el recurso honesto disponible.
        gufo = page._blocks[0]._ram._blocks[0]
        self.assertEqual(gufo.used_gib, 85.0)
        self.assertEqual(gufo.kind, "active")

    def test_local_models_card_replaces_the_compact_pill(self):
        """L30 (B) sustituye la píldora S4 de 07: la columna derecha de la fila
        1 es ahora la tarjeta «Modelos locales» con sus tres interruptores."""
        page, poller = self._page()
        poller.state.resident_info = RESIDENT_GUFO
        motor = page._blocks[0]
        motor._render()

        card = motor._models
        self.assertIsInstance(card._master, Gtk.Switch)
        self.assertEqual(sorted(card._rows), ["abito", "gufo"])
        self.assertTrue(card._rows["gufo"]["switch"].get_active())
        self.assertFalse(card._rows["abito"]["switch"].get_active())

    def test_update_card_renders_each_state(self):
        page, poller = self._page()
        card = page._blocks[3]

        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        poller.state.available_label = "0.28.1"
        card._render()
        self.assertEqual(card._pill.get_text(), "1 actualización")
        self.assertEqual(card._button.get_label(), "Actualizar a 0.28.1")
        self.assertTrue(card._button.get_sensitive())

        poller.state.updating = True
        card._render()
        self.assertIn("Actualizando", card._pill.get_text())
        self.assertFalse(card._button.get_sensitive())

        poller.state.updating = False
        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_AL_DIA))
        card._render()
        self.assertEqual(card._pill.get_text(), "Todo al día")
        self.assertFalse(card._button.get_sensitive())

        poller.state.status = parse_engine_status(CliResult(None, failure="missing"))
        card._render()
        self.assertEqual(card._pill.get_text(), "No se pudo consultar")

        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        poller.state.last_update_rc = 1
        card._render()
        self.assertEqual(card._pill.get_text(), "Revertida")

    def test_rendimiento_shows_sin_datos_with_one_reading(self):
        """S11: una sola lectura del motor nunca se pinta como 0."""
        page, poller = self._page()
        block = page._blocks[1]

        poller.state.decode = None
        poller.state.prefill = None
        block._render()
        self.assertEqual(block._lanes.current_text(0), "sin datos")
        self.assertEqual(block._lanes.current_text(1), "sin datos")

    def test_lanes_follow_the_history(self):
        page, poller = self._page()
        block = page._blocks[1]
        base = time.monotonic()

        poller.history.record(sample(base - 20.0, "gufo", decode=12.0, prefill=80.0))
        poller.history.record(sample(base - 10.0, "gufo", decode=14.0, prefill=90.0))
        poller.history.record(sample(base, "gufo", decode=11.0, prefill=70.0))
        poller.state.decode = 11.0
        poller.state.prefill = 70.0
        block._render()

        decode_points = block._lanes._lanes[0].points
        self.assertEqual(len(decode_points), 3)
        self.assertEqual(decode_points[-1][1], 11.0)
        # El valor actual vive al final del lane como número grande, no en
        # una frase monospace sobre el gráfico (defecto 6).
        self.assertEqual(block._lanes.current_text(0), "11.0")
        self.assertEqual(block._lanes._lanes[0].unit, "tok/s")

    def test_lanes_axis_uses_wall_clock_labels(self):
        """Defecto 5: las marcas del eje son HH:MM de pared, no «104:25»."""
        page, poller = self._page()
        block = page._blocks[1]
        base = time.time()

        poller.history.record(sample(time.monotonic() - 120.0, "gufo", decode=12.0))
        poller.history.record(sample(time.monotonic(), "gufo", decode=13.0))
        block._render()

        lanes = block._lanes
        self.assertIsNotNone(lanes._clock)
        stamp = time.localtime(base)
        expected = f"{stamp.tm_hour:02d}:{stamp.tm_min:02d}"
        self.assertEqual(lanes._clock(time.monotonic()), expected)

    def test_inventory_rows_render_compact(self):
        page, poller = self._page()
        block = page._blocks[2]

        poller.state.inventory = [
            {"model": "abito", "port": 11002, "ctx": 65536, "healthy": True},
            {"model": "gufo", "port": 11070, "ctx": 131072, "healthy": False},
        ]
        block._render()

        self.assertEqual(len(block._rows), 2)
        # El nombre del motor es visible además del estado (defecto 3).
        self.assertEqual(block._rows[0]["name_label"].get_text(), "abito")
        self.assertEqual(block._rows[0]["port"].get_text(), ":11002")
        self.assertIn("encendido", block._rows[0]["state"].get_text())

    def test_row_three_is_a_two_column_grid(self):
        """Defecto 2 (pase visual 2): inventario a la izquierda (~70 %) y la
        tarjeta de actualización en una columna derecha de 276 px, la misma
        rejilla «1fr 276px» de las filas 1 y 2 del artboard."""
        page, _poller = self._page()
        inventario, update = page._blocks[2], page._blocks[3]

        self.assertIs(inventario.get_parent(), update.get_parent())
        row = inventario.get_parent()
        self.assertIsInstance(row, Gtk.Box)
        self.assertEqual(row.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertTrue(inventario.get_hexpand())
        self.assertFalse(update.get_hexpand())
        self.assertEqual(update.get_size_request()[0], 276)

    def test_update_card_is_compact_with_a_natural_width_button(self):
        """Defecto 2: píldora arriba, rail, botón de ancho natural debajo
        (nunca una barra de ancho completo)."""
        page, _poller = self._page()
        update = page._blocks[3]

        self.assertEqual(update._button.get_halign(), Gtk.Align.START)
        # Sin vexpand en el rail: el espacio sobrante de la página no debe
        # inflar la tarjeta (defecto 4 del pase visual 2).
        self.assertFalse(update._rail_box.get_vexpand())

    def test_inventory_ctx_bar_is_series_blue_and_compact(self):
        """Defecto 2: la barra de contexto usa el azul serie del artboard y
        ocupa ~40 % de la fila, no todo el ancho restante."""
        page, poller = self._page()
        block = page._blocks[2]

        poller.state.inventory = [
            {"model": "gufo", "port": 11070, "ctx": 131072, "healthy": True},
        ]
        block._render()

        bar = block._rows[0]["ctx"]
        self.assertIn("inv-ctx", bar.get_css_classes())
        self.assertGreaterEqual(bar.get_size_request()[0], 300)

    def test_band_and_markers_follow_the_history(self):
        from pc_ai_monitor.widgets import instrumentos_logica as logica

        page, poller = self._page()
        motor = page._blocks[0]
        base = time.monotonic()

        poller.history.record(sample(base - 60.0, "gufo"))
        poller.history.record_event("switch", "cambio a Abito", t=base - 30.0)
        poller.history.record(sample(base, "abito"))
        motor._render()

        self.assertEqual([s.kind for s in motor._band._segments], ["gufo", "abito"])
        self.assertEqual([m.kind for m in motor._band._markers], ["switch"])
        del logica


class ShortcutTests(unittest.TestCase):
    """Ctrl E y Ctrl U disparan las mismas confirmaciones del panel Motores."""

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

    def _page(self, poller=None):
        import pc_ai_monitor.blocks.home as home_blocks
        from pc_ai_monitor.pages.home import HomePage

        poller = poller or FakePoller()
        with mock.patch.object(home_blocks, "POLLER", poller):
            page = HomePage()
        return page, poller

    def test_ctrl_e_shows_the_real_dialog_with_confirm_text(self):
        from pc_ai_monitor.blocks.residente_logica import confirm_text

        page, poller = self._page()
        poller.state.resident_info = RESIDENT_GUFO

        created = []
        original = FakeAdwDialog

        class Recording(original):  # type: ignore[valid-type,misc]
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        import pc_ai_monitor.blocks.home as home_blocks

        with mock.patch.object(home_blocks.Adw, "MessageDialog", Recording):
            page.request_switch()

        title, body = confirm_text("abito")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].heading, title)
        self.assertEqual(created[0].body, body)

    def test_ctrl_e_respects_the_manager_reason(self):
        page, poller = self._page()
        poller.state.resident_info = {
            "resident": None,
            "others_active": ["gufo.service"],
            "abito": {"healthy": False},
            "gufo": {"context": 131072},
        }

        created = []

        class Recording(FakeAdwDialog):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        import pc_ai_monitor.blocks.home as home_blocks

        with mock.patch.object(home_blocks.Adw, "MessageDialog", Recording):
            page.request_switch()

        self.assertEqual(created, [])  # otro motor cargado: ni diálogo

    def test_ctrl_u_opens_the_same_update_confirmation_and_command(self):
        from pc_ai_monitor.blocks.motores import CommandResult

        page, poller = self._page()
        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_UPDATABLE))
        poller.state.available_label = "0.28.1"

        created = []

        class Recording(FakeGtkDialog):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        calls = []
        done = threading.Event()

        def fake_run_manager(args, timeout=None):
            del timeout
            calls.append(tuple(args))
            done.set()
            if args[:2] == ("engine", "update"):
                return CommandResult(0, stdout='{"from": "a", "to": "b"}')
            return CommandResult(0, stdout=STATUS_UPDATABLE)

        import pc_ai_monitor.blocks.home as home_blocks

        with (
            mock.patch.object(home_blocks.Gtk, "MessageDialog", Recording),
            mock.patch.object(home_blocks, "run_manager", fake_run_manager),
        ):
            page.request_update()
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].text, "¿Actualizar el motor?")
            self.assertEqual(created[0].secondary, CONFIRMATION_TEXT)

            created[0].respond(Gtk.ResponseType.ACCEPT)
            self.assertTrue(done.wait(5.0), "el update no llegó a correr")
            self.assertIn(("engine", "update", "--yes", "--json"), calls)
            self.assertTrue(poller.update_begun)


if __name__ == "__main__":
    unittest.main()


class HistoryConcurrencyTests(unittest.TestCase):
    """El poller escribe desde su hilo y la UI lee desde el de GTK."""

    def test_concurrent_record_and_read_keep_the_buffer_consistent(self):
        import threading

        from pc_ai_monitor.blocks.historia_motor import MotorHistory, Sample

        history = MotorHistory(window_s=50.0)
        errors: list[BaseException] = []
        stop = threading.Event()

        def writer():
            t = 0.0
            while not stop.is_set():
                t += 0.01
                history.record(Sample(t, "gufo", 47.0, 1000.0, 30.0, 90.0))
                history.record_event("update", "prueba", t=t)

        def reader():
            try:
                for _ in range(3000):
                    samples = history.samples()
                    times = [sample.t for sample in samples]
                    self.assertEqual(times, sorted(times))
                    if samples:
                        # La ventana acota por instante de la última muestra.
                        self.assertLessEqual(samples[-1].t - samples[0].t, 50.0 + 1e-6)
                    history.markers()
            except BaseException as error:  # noqa: BLE001 - surfaced below
                errors.append(error)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            reader()
        finally:
            stop.set()
            thread.join()
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# S15 / T12: interruptores de modelos locales (lógica pura)
# ---------------------------------------------------------------------------

RESIDENT_NONE = {
    "resident": "none",
    "abito": {"unit": "inactive", "healthy": False, "processing": False},
    "gufo": {"unit": "inactive", "ready": False, "configured_context": 131072},
    "others_active": [],
    "switch_in_progress": False,
}
RESIDENT_ABITO = dict(RESIDENT_NONE, resident="abito", abito={"healthy": True, "processing": False})
RESIDENT_GUFO_FULL = dict(RESIDENT_NONE, resident="gufo")
MEMORY_LIVE = {"total_gib": 122.7, "used_gib": 21.8, "available_gib": 100.9}


class TogglePlanTests(unittest.TestCase):
    """L28/L31: qué hace cada interruptor según lo que el backend confirma."""

    def test_abito_on_with_nothing_resident_is_ensure(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.toggle_plan(RESIDENT_NONE, "abito", True), hl.Plan("ensure", "abito", False))

    def test_gufo_on_with_nothing_resident_is_a_switch_without_dialog(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.toggle_plan(RESIDENT_NONE, "gufo", True), hl.Plan("switch", "gufo", False))

    def test_one_on_while_the_other_runs_asks_for_the_switch(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.toggle_plan(RESIDENT_ABITO, "gufo", True), hl.Plan("switch", "gufo", True))
        self.assertEqual(hl.toggle_plan(RESIDENT_GUFO_FULL, "abito", True), hl.Plan("switch", "abito", True))

    def test_off_asks_and_stops_the_running_engine(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.toggle_plan(RESIDENT_ABITO, "abito", False), hl.Plan("stop", "abito", True))
        self.assertEqual(hl.toggle_plan(RESIDENT_GUFO_FULL, "gufo", False), hl.Plan("stop_gufo", "gufo", True))

    def test_no_change_or_no_data_does_nothing(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.toggle_plan(RESIDENT_ABITO, "abito", True).kind, "none")
        self.assertEqual(hl.toggle_plan(RESIDENT_NONE, "gufo", False).kind, "none")
        self.assertEqual(hl.toggle_plan(None, "abito", True).kind, "none")

    def test_master_on_is_abito_and_off_stops_whatever_runs(self):
        from pc_ai_monitor.blocks import home_logica as hl

        self.assertEqual(hl.master_plan(RESIDENT_NONE, True), hl.Plan("ensure", "abito", False))
        self.assertEqual(hl.master_plan(RESIDENT_ABITO, False), hl.Plan("stop", "abito", True))
        self.assertEqual(hl.master_plan(RESIDENT_GUFO_FULL, False), hl.Plan("stop_gufo", "gufo", True))
        self.assertEqual(hl.master_plan(RESIDENT_NONE, False).kind, "none")


class LocalModelsViewTests(unittest.TestCase):
    def _view(self, info, memory=MEMORY_LIVE, models=None, locks=None, running=None):
        from pc_ai_monitor.blocks import home_logica as hl

        return hl.local_models(info, memory, models or [], locks or {}, running)

    def test_idle_projection_matches_08(self):
        """08 tab 2: 100.9 disponibles → ≈ 80.9 con Abito, ≈ 15.9 con Gufo."""
        view = self._view(RESIDENT_NONE)
        abito, gufo = view.engines
        self.assertFalse(view.master_active)
        self.assertEqual(view.master_status, "Ninguno cargado · Pi y el router van a la nube")
        self.assertEqual(abito.projection_gib, 80.9)
        self.assertEqual(gufo.projection_gib, 15.9)
        self.assertFalse(abito.projection_low)
        self.assertTrue(gufo.projection_low)
        self.assertIn("≈ 20 GiB al encender", abito.status)
        self.assertTrue(abito.sensitive and gufo.sensitive)

    def test_switches_reflect_only_the_confirmed_resident(self):
        view = self._view(RESIDENT_ABITO)
        abito, gufo = view.engines
        self.assertTrue(view.master_active)
        self.assertTrue(abito.active)
        self.assertFalse(gufo.active)
        self.assertIsNone(abito.projection_gib)
        # Al cambiar se libera Abito: la proyección lo descuenta.
        self.assertIn("pide confirmación", gufo.reason)

    def test_loading_is_never_shown_as_on(self):
        view = self._view(dict(RESIDENT_NONE, resident="gufo-loading"))
        gufo = view.engines[1]
        self.assertFalse(gufo.active)
        self.assertFalse(gufo.sensitive)
        self.assertEqual(gufo.lamp, "stale")

    def test_running_action_locks_every_switch(self):
        view = self._view(RESIDENT_NONE, running="abito")
        abito, gufo = view.engines
        self.assertFalse(abito.sensitive)
        self.assertFalse(gufo.sensitive)
        self.assertFalse(view.master_sensitive)
        self.assertEqual(abito.reason, "")  # su progreso lo explica
        self.assertIn("otra acción de motor", gufo.reason)

    def test_update_lock_reason_is_inline(self):
        reason = "Bloqueado mientras se actualiza Abito."
        view = self._view(RESIDENT_NONE, locks={"abito": reason})
        self.assertEqual(view.engines[0].reason, reason)
        self.assertFalse(view.engines[0].sensitive)
        self.assertFalse(view.master_sensitive)  # encender el maestro = Abito
        self.assertEqual(view.master_reason, reason)

    def test_without_manager_data_nothing_is_on_or_clickable(self):
        view = self._view(None)
        self.assertFalse(view.master_active or view.master_sensitive)
        for engine in view.engines:
            self.assertFalse(engine.active or engine.sensitive)
            self.assertIn("No se pudo leer", engine.reason)


# ---------------------------------------------------------------------------
# S15 / T12: la tarjeta en Home (GTK real, diálogos reales, guard de aislamiento)
# ---------------------------------------------------------------------------


def Graphene_point():
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    return Graphene.Point().init(0, 0)


class LocalModelsCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")
        from gi.repository import Adw

        Adw.init()

    def setUp(self):
        from pc_ai_monitor.blocks import progreso

        progreso.HUB.reset()
        self.addCleanup(progreso.HUB.reset)

    def _motor(self, info):
        from pc_ai_monitor.blocks.home import MotorRowBlock

        poller = FakePoller()
        poller.state.resident_info = info
        block = MotorRowBlock(poller=poller)
        block.update_snapshot(home_snapshot(models=[], memory=dict(MEMORY_LIVE)))
        block._render()
        return block, poller

    def _open(self, action):
        try:
            import _dialogos as dialogos
        except ModuleNotFoundError:
            from tests import _dialogos as dialogos
        dialogs = dialogos.presented_dialogs(action)
        for dialog in dialogs:
            self.addCleanup(dialogos.close, dialog)
        return dialogs, dialogos

    def test_fila_1_keeps_ram_and_band_and_drops_the_mini_cards_and_pill(self):
        """L30 (B): la tarjeta de interruptores reemplaza sólo las mini
        tarjetas y la píldora «Cambiar a … Ctrl E»."""
        from pc_ai_monitor.blocks.home import LocalModelsCard
        from pc_ai_monitor.widgets.instrumentos import MotorBand, RamBlocks

        block, _poller = self._motor(RESIDENT_NONE)
        self.assertIsInstance(block._ram, RamBlocks)
        self.assertIsInstance(block._band, MotorBand)
        self.assertIsInstance(block._models, LocalModelsCard)
        self.assertFalse(hasattr(block, "_mini_cards"))
        self.assertFalse(hasattr(block, "_switch_button"))

    def test_switches_show_the_confirmed_state(self):
        block, poller = self._motor(RESIDENT_ABITO)
        card = block._models
        self.assertTrue(card._master.get_active())
        self.assertTrue(card._rows["abito"]["switch"].get_active())
        self.assertFalse(card._rows["gufo"]["switch"].get_active())
        poller.state.resident_info = RESIDENT_NONE
        block._render()
        self.assertFalse(card._master.get_active())
        self.assertFalse(card._rows["abito"]["switch"].get_active())

    def test_abito_on_runs_ensure_with_progress_and_never_fakes_the_state(self):
        block, _poller = self._motor(RESIDENT_NONE)
        card = block._models
        start = len(_aislamiento.ATTEMPTS)
        dialogs, dialogos = self._open(lambda: card._rows["abito"]["switch"].set_active(True))
        self.assertEqual(dialogs, [])  # sin confirmación: encender Abito no corta nada
        self.assertTrue(dialogos.pump_until(
            lambda: ("ensure", "abito") in dialogos.commands_since(start)
        ))
        # Mientras corre (y tras el fallo del guard) el interruptor sigue en
        # lo que el backend confirmó: apagado.
        self.assertFalse(card._rows["abito"]["switch"].get_active())
        from pc_ai_monitor.blocks import progreso

        self.assertTrue(dialogos.pump_until(
            lambda: (a := progreso.HUB.get("engine:abito")) is not None and not a.running
        ))
        dialogos.pump(0.1)
        self.assertFalse(card._rows["abito"]["switch"].get_active())
        self.assertTrue(card._rows["abito"]["progress"].get_visible())

    def test_gufo_on_while_abito_runs_opens_the_switch_confirmation(self):
        from gi.repository import Adw

        from pc_ai_monitor.blocks.residente_logica import confirm_text

        block, poller = self._motor(RESIDENT_ABITO)
        dialogs, dialogos = self._open(lambda: block._models._rows["gufo"]["switch"].set_active(True))
        self.assertEqual(len(dialogs), 1)
        self.assertIsInstance(dialogs[0], Adw.MessageDialog)
        self.assertEqual(dialogs[0].get_heading(), confirm_text("gufo")[0])
        start = dialogos.mark()
        dialogs[0].response("switch")
        self.assertTrue(dialogos.wait_for_command(start, ("switch", "gufo")))
        self.assertEqual(poller.switch_begun, ["gufo"])
        dialogos.pump_until(lambda: poller.state.switching is None)

    def test_gufo_on_with_nothing_resident_switches_without_a_dialog(self):
        block, _poller = self._motor(RESIDENT_NONE)
        start = len(_aislamiento.ATTEMPTS)
        dialogs, dialogos = self._open(lambda: block._models._rows["gufo"]["switch"].set_active(True))
        self.assertEqual(dialogs, [])
        self.assertTrue(dialogos.wait_for_command(start, ("switch", "gufo")))
        dialogos.pump_until(lambda: _poller.state.switching is None)

    def test_abito_off_asks_the_existing_stop_confirmation(self):
        block, _poller = self._motor(RESIDENT_ABITO)
        dialogs, dialogos = self._open(lambda: block._models._rows["abito"]["switch"].set_active(False))
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        self.assertEqual(dialog.get_property("text"), "¿Apagar abito?")
        start = dialogos.mark()
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assertTrue(dialogos.wait_for_command(start, ("stop", "abito")))
        self.assertTrue(block._models._rows["abito"]["switch"].get_active())  # sin confirmar

    def test_gufo_off_asks_then_stops_without_restoring_abito(self):
        """L31: Gufo off = gufo-flash stop --no-restore, tras confirmar."""
        block, _poller = self._motor(RESIDENT_GUFO_FULL)
        dialogs, dialogos = self._open(lambda: block._models._rows["gufo"]["switch"].set_active(False))
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        self.assertEqual(dialog.get_property("text"), "¿Apagar Gufo?")
        self.assertIn("85 GiB", dialog.get_property("secondary-text"))
        start = len(_aislamiento.ATTEMPTS)
        dialog.response(Gtk.ResponseType.ACCEPT)
        self.assertTrue(dialogos.pump_until(
            lambda: ("gufo-flash", ("stop", "--no-restore")) in _aislamiento.ATTEMPTS[start:]
        ))

    def test_cancel_changes_nothing(self):
        block, _poller = self._motor(RESIDENT_GUFO_FULL)
        dialogs, dialogos = self._open(lambda: block._models._master.set_active(False))
        self.assertEqual(len(dialogs), 1)
        start = len(_aislamiento.ATTEMPTS)
        dialogs[0].response(Gtk.ResponseType.CANCEL)
        dialogos.pump(0.3)
        self.assertEqual(_aislamiento.ATTEMPTS[start:], [])
        self.assertTrue(block._models._master.get_active())

    def test_master_on_starts_abito(self):
        block, _poller = self._motor(RESIDENT_NONE)
        start = len(_aislamiento.ATTEMPTS)
        dialogs, dialogos = self._open(lambda: block._models._master.set_active(True))
        self.assertEqual(dialogs, [])
        self.assertTrue(dialogos.pump_until(
            lambda: ("local-ai-models", ("ensure", "abito")) in _aislamiento.ATTEMPTS[start:]
        ))

    def test_a_long_error_never_pushes_the_card_off_the_window(self):
        """Render of an «ensure» error: in a horizontal Box the wrapping
        message asked for its width *for the row height* and the card was
        allocated 660 px past the window. Row 1 must keep inside 1156 px."""
        from pc_ai_monitor.blocks import progreso
        from pc_ai_monitor.blocks import progreso_logica as pl

        try:
            import _dialogos as dialogos
        except ModuleNotFoundError:
            from tests import _dialogos as dialogos

        import pc_ai_monitor.blocks.home as home_blocks
        from pc_ai_monitor.pages.home import HomePage

        poller = FakePoller()
        poller.state.resident_info = RESIDENT_NONE
        with mock.patch.object(home_blocks, "POLLER", poller):
            page = HomePage()
        page.update_snapshot(home_snapshot(models=[], memory=dict(MEMORY_LIVE)))
        block = page._blocks[0]
        window = Gtk.Window()
        window.set_default_size(1180, 900)
        window.set_child(page)
        window.present()
        self.addCleanup(window.destroy)
        activity = pl.engine_action("abito", "ensure", section="home", retry=lambda: None)
        progreso.HUB.begin(activity)
        activity.fail(
            "No se encendió Abito",
            "No se puede encender abito: no alcanza la memoria para conservar 20 GiB "
            "o el otro modelo grande está encendido.",
            log="x",
        )
        progreso.HUB.changed()
        dialogos.pump(0.3)
        card = block._models
        ok, origin = card.compute_point(window, Graphene_point())
        self.assertTrue(ok)
        self.assertLessEqual(origin.x + card.get_width(), window.get_width())

    def test_an_action_started_elsewhere_locks_the_switches_at_once(self):
        """Render: while «Encendiendo Abito» ran, the card still offered the
        master and showed idle lamps — it only re-rendered on poller ticks."""
        from pc_ai_monitor.blocks import progreso
        from pc_ai_monitor.blocks import progreso_logica as pl

        block, _poller = self._motor(RESIDENT_NONE)
        card = block._models
        self.assertTrue(card._master.get_sensitive())
        progreso.HUB.begin(pl.engine_action("abito", "ensure", section="motores"))
        progreso.HUB.changed()
        self.assertFalse(card._master.get_sensitive())
        self.assertFalse(card._rows["gufo"]["switch"].get_sensitive())
        self.assertEqual(card._rows["abito"]["lamp"]._state, "stale")
        self.assertFalse(card._master_reason.get_visible())

    def test_ctrl_e_still_opens_the_switch_confirmation(self):
        from gi.repository import Adw

        from pc_ai_monitor.blocks.residente_logica import confirm_text

        block, _poller = self._motor(RESIDENT_GUFO_FULL)
        dialogs, _dialogos = self._open(block.request_switch)
        self.assertEqual(len(dialogs), 1)
        self.assertIsInstance(dialogs[0], Adw.MessageDialog)
        self.assertEqual(dialogs[0].get_heading(), confirm_text("abito")[0])


class HomeDisabledReasonTests(unittest.TestCase):
    """S16 también en Home: un botón apagado dice por qué, visible."""

    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("sin display (usá xvfb-run)")

    def setUp(self):
        from pc_ai_monitor.blocks import progreso

        progreso.HUB.reset()
        self.addCleanup(progreso.HUB.reset)

    def test_every_disabled_button_in_home_says_why(self):
        import pc_ai_monitor.blocks.home as home_blocks
        from pc_ai_monitor.pages.home import HomePage

        try:
            from test_motores_vista import silent_disabled_buttons
        except ModuleNotFoundError:
            from tests.test_motores_vista import silent_disabled_buttons

        poller = FakePoller()
        poller.state.resident_info = RESIDENT_ABITO
        poller.state.status = parse_engine_status(CliResult(0, stdout=STATUS_AL_DIA))
        poller.state.available_label = "0.28.1"
        poller.state.inventory = [
            {"model": "abito", "port": 11002, "ctx": 131072, "healthy": True},
            {"model": "coder", "port": 11004, "ctx": 32768, "healthy": False,
             "problem": "missing_model_file"},
        ]
        with mock.patch.object(home_blocks, "POLLER", poller):
            page = HomePage()
        page.update_snapshot(home_snapshot())
        for block in page._blocks:
            block._render()

        self.assertEqual(silent_disabled_buttons(page), [])
        update = page._blocks[3]
        self.assertFalse(update._button.get_sensitive())
        self.assertEqual(update._why.get_text(), "Ya corre la más nueva.")
