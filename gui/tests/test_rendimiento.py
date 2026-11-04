"""Tests de la lógica de la tarjeta "Rendimiento del motor".

Todo vive en ``pc_ai_monitor.blocks.rendimiento_logica``: parseo tolerante
del texto Prometheus de ``/metrics`` (una serie ausente es desconocida,
nunca cero), las mates de caché, ventana y especulación, los pisos de
memoria del guard (15/12 GiB) y el comportamiento de últimos valores
conocidos cuando una lectura falla. Sin GTK y sin subprocesos.
"""

import unittest

from pc_ai_monitor.blocks.rendimiento_logica import (
    Guard,
    Metrics,
    RendimientoView,
    Slot,
    SpeedTracker,
    build_view,
    cached_percentage,
    device_lost_text,
    footer_text,
    health_text,
    kv_percentage,
    memory_warning,
    parse_guard,
    parse_metrics,
    parse_served_context,
    parse_slots,
    slot_text,
    spec_ratio,
    speed_text,
    stale_view,
)

# Texto Prometheus realista: HELP duplicado, una serie con etiquetas, otra
# con timestamp y faltan llamacpp:requests_processing y gufo_device_lost.
METRICS_TEXT = """
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
llamacpp:prompt_tokens_total 43512
# TYPE llamacpp:prompt_tokens_processed_total counter
llamacpp:prompt_tokens_processed_total 41000
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 2456
# TYPE llamacpp:prompt_tokens_cached_total counter
llamacpp:prompt_tokens_cached_total 38900
# TYPE llamacpp:kv_cache_usage_ratio gauge
llamacpp:kv_cache_usage_ratio{slot="0"} 0.1234
gufo_spec_drafts_proposed_total{slot="0"} 1200
gufo_spec_drafts_accepted_total{slot="0"} 840
llamacpp:prompt_tokens_total 10 1690000000000
"""


class ParseMetricsTests(unittest.TestCase):
    def test_realistic_payload_parses_known_series(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertEqual(metrics.prompt_tokens_total, 43512.0 + 10.0)
        self.assertEqual(metrics.prompt_tokens_processed_total, 41000.0)
        self.assertEqual(metrics.tokens_predicted_total, 2456.0)
        self.assertEqual(metrics.prompt_tokens_cached_total, 38900.0)
        self.assertEqual(metrics.kv_cache_usage_ratio, 0.1234)

    def test_missing_series_is_unknown_never_zero(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertIsNone(metrics.requests_processing)
        self.assertIsNone(metrics.device_lost_total)

    def test_duplicate_help_lines_do_not_break_the_parse(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertIsNotNone(metrics.prompt_tokens_total)

    def test_labeled_sample_matches_by_bare_series_name(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertEqual(metrics.kv_cache_usage_ratio, 0.1234)

    def test_sample_with_timestamp_is_parsed(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertEqual(metrics.prompt_tokens_total, 43522.0)

    def test_garbage_text_leaves_every_series_unknown(self):
        metrics = parse_metrics("esto no es prometheus\n{malformada 3\n")

        self.assertIsNone(metrics.prompt_tokens_total)
        self.assertIsNone(metrics.kv_cache_usage_ratio)
        self.assertEqual(metrics.spec, ())

    def test_spec_counters_keep_the_full_series_name(self):
        metrics = parse_metrics(METRICS_TEXT)

        self.assertEqual(
            metrics.spec,
            (
                ("gufo_spec_drafts_accepted_total{slot=\"0\"}", 840.0),
                ("gufo_spec_drafts_proposed_total{slot=\"0\"}", 1200.0),
            ),
        )


class CachedPercentageTests(unittest.TestCase):
    def test_normal_ratio(self):
        self.assertEqual(cached_percentage(38900.0, 43512.0), 89.4)

    def test_zero_total_is_unknown(self):
        self.assertIsNone(cached_percentage(0.0, 0.0))

    def test_missing_total_is_unknown(self):
        self.assertIsNone(cached_percentage(10.0, None))

    def test_missing_cached_is_unknown(self):
        self.assertIsNone(cached_percentage(None, 43512.0))

    def test_ratio_is_clamped_to_the_interval(self):
        self.assertEqual(cached_percentage(50000.0, 43512.0), 100.0)


class KvPercentageTests(unittest.TestCase):
    def test_ratio_is_rounded_to_one_decimal(self):
        self.assertEqual(kv_percentage(0.1234), 12.3)

    def test_full_window_is_one_hundred(self):
        self.assertEqual(kv_percentage(1.0), 100.0)

    def test_missing_ratio_is_unknown(self):
        self.assertIsNone(kv_percentage(None))


class SpecRatioTests(unittest.TestCase):
    def test_ratio_with_both_counters(self):
        ratio = spec_ratio(
            (
                ("gufo_spec_drafts_proposed_total{slot=\"0\"}", 1200.0),
                ("gufo_spec_drafts_accepted_total{slot=\"0\"}", 840.0),
            )
        )

        self.assertEqual(ratio, (0.7, 840.0, 1200.0))

    def test_missing_counters_yield_no_ratio(self):
        self.assertIsNone(spec_ratio(()))

    def test_only_one_counter_yields_no_ratio(self):
        self.assertIsNone(spec_ratio((("gufo_spec_drafts_accepted_total", 5.0),)))

    def test_zero_proposed_yields_no_ratio(self):
        self.assertIsNone(
            spec_ratio(
                (
                    ("gufo_spec_drafts_proposed_total", 0.0),
                    ("gufo_spec_drafts_accepted_total", 0.0),
                )
            )
        )


class MemoryWarningTests(unittest.TestCase):
    def test_unknown_memory_never_warns(self):
        self.assertEqual(memory_warning(None), "")

    def test_exactly_fifteen_gib_does_not_warn(self):
        self.assertEqual(memory_warning(15.0), "")

    def test_just_below_fifteen_gib_warns_about_the_postload_floor(self):
        warning = memory_warning(14.9)

        self.assertIn("15 GiB", warning)
        self.assertIn("tras cargar", warning)

    def test_exactly_twelve_gib_still_warns_about_fifteen(self):
        warning = memory_warning(12.0)

        self.assertIn("15 GiB", warning)

    def test_just_below_twelve_gib_warns_about_the_work_floor(self):
        warning = memory_warning(11.9)

        self.assertIn("12 GiB", warning)
        self.assertIn("durante el trabajo", warning)


class ParseGuardTests(unittest.TestCase):
    STATUS_OK = """{
     "unit": "active",
     "engine_ready": true,
     "configured_context": 131072,
     "status": {
      "phase": "postload",
      "mem_available_gib": 23.5,
      "conservative_gib": 21.2,
      "min_mem_available_gib": 9.28,
      "min_conservative_gib": 8.4
     }
    }"""

    def test_realistic_status_parses_the_guard_numbers(self):
        guard = parse_guard(self.STATUS_OK, 0, None)

        self.assertEqual(guard.mem_available_gib, 23.5)
        self.assertEqual(guard.conservative_gib, 21.2)
        self.assertEqual(guard.min_mem_available_gib, 9.28)
        self.assertEqual(guard.min_conservative_gib, 8.4)
        self.assertEqual(guard.phase, "postload")
        self.assertTrue(guard.engine_ready)
        self.assertEqual(guard.error, "")

    def test_missing_status_block_keeps_engine_ready_without_numbers(self):
        guard = parse_guard('{"engine_ready": true}', 0, None)

        self.assertTrue(guard.engine_ready)
        self.assertIsNone(guard.mem_available_gib)
        self.assertEqual(guard.phase, "")

    def test_invalid_json_reports_an_error(self):
        guard = parse_guard("no soy json", 0, None)

        self.assertIn("JSON inválido", guard.error)

    def test_nonzero_exit_reports_an_error(self):
        guard = parse_guard("", 3, None)

        self.assertIn("código 3", guard.error)

    def test_missing_binary_reports_an_error(self):
        guard = parse_guard("", None, "missing")

        self.assertIn("gufo-flash", guard.error)

    def test_timeout_reports_an_error(self):
        guard = parse_guard("", None, "timeout")

        self.assertIn("tiempo de espera", guard.error)


class ParseSlotsTests(unittest.TestCase):
    SLOTS_JSON = """
    [{"id": 0, "n_ctx": 131072, "is_processing": true,
      "n_prompt_tokens": 43512, "n_prompt_tokens_processed": 41000}]
    """

    def test_list_payload_parses_the_first_slot(self):
        slots = parse_slots(self.SLOTS_JSON)

        self.assertEqual(
            slots,
            (
                Slot(
                    n_ctx=131072,
                    is_processing=True,
                    n_prompt_tokens=43512.0,
                    n_prompt_tokens_processed=41000.0,
                ),
            ),
        )

    def test_wrapped_payload_is_accepted(self):
        slots = parse_slots('{"slots": [{"n_ctx": 262144}]}')

        self.assertEqual(slots[0].n_ctx, 262144)
        self.assertIsNone(slots[0].is_processing)

    def test_garbage_yields_no_slots(self):
        self.assertEqual(parse_slots("no soy json"), ())


class ParseServedContextTests(unittest.TestCase):
    def test_realistic_models_payload(self):
        text = """
        {"object": "list", "data": [
          {"id": "qwen3.8-flash-next", "context_length": 131072}
        ]}
        """

        self.assertEqual(parse_served_context(text), 131072)

    def test_missing_context_length_is_unknown(self):
        text = '{"object": "list", "data": [{"id": "qwen3.8-flash-next"}]}'

        self.assertIsNone(parse_served_context(text))

    def test_garbage_is_unknown(self):
        self.assertIsNone(parse_served_context("no soy json"))


class SpeedTrackerTests(unittest.TestCase):
    def test_first_sample_has_no_rate(self):
        tracker = SpeedTracker()

        self.assertIsNone(tracker.update(100.0, 10.0))
        self.assertIsNone(tracker.last)

    def test_positive_delta_gives_tokens_per_second(self):
        tracker = SpeedTracker()
        tracker.update(100.0, 10.0)

        self.assertEqual(tracker.update(1100.0, 20.0), 100.0)
        self.assertEqual(tracker.last, 100.0)

    def test_no_activity_keeps_the_last_known_rate(self):
        tracker = SpeedTracker()
        tracker.update(100.0, 10.0)
        tracker.update(1100.0, 20.0)
        rate = tracker.update(1100.0, 25.0)

        self.assertIsNone(rate)
        self.assertEqual(tracker.last, 100.0)

    def test_counter_reset_yields_no_rate_and_keeps_the_last(self):
        tracker = SpeedTracker()
        tracker.update(100.0, 10.0)
        tracker.update(1100.0, 20.0)
        rate = tracker.update(5.0, 30.0)

        self.assertIsNone(rate)
        self.assertEqual(tracker.last, 100.0)

    def test_missing_counter_yields_no_rate(self):
        tracker = SpeedTracker()
        tracker.update(100.0, 10.0)

        self.assertIsNone(tracker.update(None, 20.0))


def _guard_with_memory(mem: float | None) -> Guard:
    return Guard(
        mem_available_gib=mem,
        conservative_gib=mem,
        min_mem_available_gib=mem,
        min_conservative_gib=mem,
        phase="postload",
        engine_ready=bool(mem),
    )


def _metrics(**overrides) -> Metrics:
    values = {
        "prompt_tokens_total": 43512.0,
        "prompt_tokens_cached_total": 38900.0,
        "requests_processing": 1.0,
        "kv_cache_usage_ratio": 0.25,
        "device_lost_total": 0.0,
        "spec": (
            ("gufo_spec_drafts_proposed_total", 1200.0),
            ("gufo_spec_drafts_accepted_total", 840.0),
        ),
    }
    values.update(overrides)
    return Metrics(**values)


class BuildViewTests(unittest.TestCase):
    def test_view_rows_with_a_full_reading(self):
        view = build_view(
            metrics=_metrics(),
            served_context=131072,
            slots=(Slot(n_ctx=131072, is_processing=False),),
            guard=_guard_with_memory(23.5),
            prefill_rate=931.0,
            decode_rate=40.7,
            last_read="12:00:00",
        )

        rows = dict(view.rows)
        self.assertEqual(rows["salud"], "listo · fase: postload · puerto 11070")
        self.assertEqual(rows["contexto servido"], "131072")
        self.assertEqual(rows["solicitudes en curso"], "1")
        self.assertEqual(rows["slot"], "en espera")
        self.assertEqual(rows["prefill"], "931.0 tok/s")
        self.assertEqual(rows["decode"], "40.7 tok/s")
        self.assertEqual(rows["caché de prompt"], "89.4 %")
        self.assertEqual(rows["ventana en uso"], "25.0 %")
        self.assertEqual(
            rows["especulación"], "70 % aceptados (840 de 1200 borradores)"
        )
        self.assertEqual(rows["memoria disponible"], "23.5 GiB")
        self.assertIn("disponible 23.5 GiB", rows["mínimos observados"])
        self.assertEqual(
            rows["pérdidas de contexto GPU"], "0 (sin pérdidas desde el arranque)"
        )
        self.assertEqual(view.warning, "")
        self.assertEqual(view.error, "")
        self.assertFalse(view.stale)
        self.assertEqual(view.last_read, "12:00:00")

    def test_unknown_values_become_desconocido_never_zero(self):
        view = build_view(
            metrics=_metrics(
                requests_processing=None,
                kv_cache_usage_ratio=None,
                device_lost_total=None,
                prompt_tokens_cached_total=None,
                spec=(),
            ),
            served_context=None,
            slots=(),
            guard=_guard_with_memory(None),
            prefill_rate=None,
            decode_rate=None,
            last_read="12:00:00",
        )

        rows = dict(view.rows)
        self.assertEqual(rows["contexto servido"], "desconocido")
        self.assertEqual(rows["solicitudes en curso"], "desconocido")
        self.assertEqual(rows["slot"], "desconocido")
        self.assertEqual(rows["prefill"], "sin datos recientes")
        self.assertEqual(rows["decode"], "sin datos recientes")
        self.assertEqual(rows["caché de prompt"], "desconocido")
        self.assertEqual(rows["ventana en uso"], "desconocido")
        self.assertEqual(rows["especulación"], "sin contadores de borradores")
        self.assertEqual(rows["memoria disponible"], "desconocido")
        self.assertEqual(
            rows["pérdidas de contexto GPU"], "desconocido"
        )

    def test_memory_warning_reaches_the_view(self):
        view = build_view(
            metrics=_metrics(),
            served_context=131072,
            slots=(),
            guard=_guard_with_memory(9.3),
            prefill_rate=None,
            decode_rate=None,
            last_read="12:00:00",
        )

        self.assertIn("12 GiB", view.warning)


class StaleViewTests(unittest.TestCase):
    def _previous(self) -> RendimientoView:
        return build_view(
            metrics=_metrics(),
            served_context=131072,
            slots=(),
            guard=_guard_with_memory(23.5),
            prefill_rate=931.0,
            decode_rate=40.7,
            last_read="12:00:00",
        )

    def test_failed_read_keeps_the_last_known_rows(self):
        previous = self._previous()
        view = stale_view(previous, "el motor no responde en :11070")

        self.assertEqual(view.rows, previous.rows)
        self.assertEqual(view.last_read, "12:00:00")
        self.assertTrue(view.stale)
        self.assertEqual(view.error, "el motor no responde en :11070")

    def test_failed_read_keeps_the_previous_warning(self):
        previous = build_view(
            metrics=_metrics(),
            served_context=131072,
            slots=(),
            guard=_guard_with_memory(9.3),
            prefill_rate=None,
            decode_rate=None,
            last_read="12:00:00",
        )
        view = stale_view(previous, "el motor no responde en :11070")

        self.assertEqual(view.warning, previous.warning)

    def test_footer_marks_the_values_as_stale(self):
        view = stale_view(self._previous(), "falló la lectura")

        self.assertIn("12:00:00", footer_text(view))
        self.assertIn("valores desactualizados", footer_text(view))

    def test_footer_without_readings_says_so(self):
        self.assertEqual(
            footer_text(RendimientoView()),
            "sin lecturas correctas todavía",
        )


class TextHelpersTests(unittest.TestCase):
    def test_speed_without_data_is_not_invented(self):
        self.assertEqual(speed_text(None), "sin datos recientes")
        self.assertEqual(speed_text(40.66), "40.7 tok/s")

    def test_health_reports_the_guard_phase_and_port(self):
        self.assertEqual(
            health_text(_guard_with_memory(20.0)),
            "listo · fase: postload · puerto 11070",
        )
        self.assertEqual(
            health_text(Guard()),
            "no listo · puerto 11070",
        )

    def test_health_with_a_guard_error_is_unknown(self):
        self.assertEqual(
            health_text(Guard(error="gufo-flash status agotó el tiempo de espera.")),
            "desconocido · puerto 11070",
        )

    def test_slot_text_reports_processing_and_processed_prompt(self):
        self.assertEqual(
            slot_text(
                (Slot(is_processing=True, n_prompt_tokens_processed=41000.0),)
            ),
            "procesando · prompt procesado: 41000",
        )
        self.assertEqual(slot_text(()), "desconocido")

    def test_device_lost_text_says_what_zero_means(self):
        self.assertEqual(
            device_lost_text(0.0), "0 (sin pérdidas desde el arranque)"
        )
        self.assertEqual(device_lost_text(2.0), "2 (hubo pérdida de contexto)")
        self.assertEqual(device_lost_text(None), "desconocido")


if __name__ == "__main__":
    unittest.main()
