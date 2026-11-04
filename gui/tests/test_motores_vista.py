# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests de la página Motores rediseñada (diseño 07, T4).

Todo lo de datos se testea puro o con GTK real + pollers/subprocesos
mockeados: nunca se llama al administrador ni al motor de verdad.
"""

import json
import time
import unittest
from unittest import mock

from gi.repository import GLib

from pc_ai_monitor.datos import Snapshot

MODELS = [
    {
        "alias": "gufo", "pid": 101, "port": 11070, "model": "gufo.gguf",
        "rss_gib": 12.1, "cpu": 3.0, "gtt_gib": 49.2, "vram_gib": 0.0,
    },
    {
        "alias": "abito", "pid": 102, "port": 11002, "model": "abito.gguf",
        "rss_gib": 19.8, "cpu": 1.0, "gtt_gib": 0.4, "vram_gib": 0.0,
    },
]

RESIDENT_GUFO = {
    "resident": "gufo",
    "abito": {"healthy": False},
    "gufo": {"context": 131072},
}


def motores_snapshot(models=None):
    return Snapshot(stats={
        "memory": {
            "total_gib": 122.0, "used_gib": 71.3, "available_gib": 51.4,
            "swap_total_gib": 0.0, "swap_used_gib": 0.0,
        },
        "models": MODELS if models is None else models,
    })


# ---------------------------------------------------------------------------
# Lógica pura: figuras S9 y control del motor retirado
# ---------------------------------------------------------------------------


class ResidentFiguresTests(unittest.TestCase):
    """S9 «los 2»: reserva y «en uso» medido en cada tarjeta."""

    def test_gufo_shows_both_measured_figures(self):
        from pc_ai_monitor.blocks.motores_vista import figures_text

        text = figures_text("gufo", MODELS, "gufo")
        self.assertIn("≈ 85 GiB reservados", text)
        self.assertIn("49.2 GiB en uso", text)

    def test_abito_shows_both_measured_figures(self):
        from pc_ai_monitor.blocks.motores_vista import figures_text

        text = figures_text("abito", MODELS, "abito")
        self.assertIn("≈ 20 GiB reservados", text)
        self.assertIn("19.8 GiB en uso", text)

    def test_stopped_engine_has_no_use_figure(self):

        reserved, used = __import__(
            "pc_ai_monitor.blocks.motores_vista", fromlist=["resident_figures"]
        ).resident_figures("abito", MODELS, "gufo")
        self.assertIn("20 GiB", reserved)
        self.assertEqual(used, "—")

    def test_resident_without_measurement_uses_the_reservation(self):
        from pc_ai_monitor.blocks.motores_vista import figures_text

        text = figures_text("gufo", [], "gufo")
        self.assertIn("≈ 85 GiB en uso", text)
        self.assertIn("sin medir", text)


# ---------------------------------------------------------------------------
# Bloques GTK (xvfb): todo mockeado
# ---------------------------------------------------------------------------


class FakeAdwDialog:
    def __init__(self, *args, **kwargs):
        self.heading = kwargs.get("heading", "")
        self.body = kwargs.get("body", "")
        self._handler = None

    def add_response(self, *_args):
        pass

    def set_response_appearance(self, *_args):
        pass

    def set_default_response(self, *_args):
        pass

    def set_close_response(self, *_args):
        pass

    def connect(self, _signal, handler):
        self._handler = handler

    def present(self):
        pass

    def respond(self, response):
        if self._handler is not None:
            self._handler(self, response)


class MotoresVistaWidgetTests(unittest.TestCase):
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

    def _pump_until(self, condition, timeout: float = 5.0) -> None:
        """Bombea el contexto GLib hasta que la condición sea verdadera."""
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)

    def _resident_block(self, resident_stdout='{"resident": null}'):
        import pc_ai_monitor.blocks.motores_vista as vista

        patcher = mock.patch.object(
            vista, "_run_manager",
            lambda *args, **kwargs: _Result(0, stdout=resident_stdout),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        block = vista.ResidenteVistaBlock()
        self._pump_until(lambda: not block._refreshing)
        block.update_snapshot(motores_snapshot())
        return block

    def _rendimiento_block(self):
        import pc_ai_monitor.blocks.motores_vista as vista

        for name, double in (
            ("_http_text", lambda path, timeout=3.0: None),
            ("_run_gufo_flash_status", lambda: ("", None, "missing")),
        ):
            patcher = mock.patch.object(vista, name, double)
            patcher.start()
            self.addCleanup(patcher.stop)
        block = vista.RendimientoVistaBlock()
        self._pump_until(lambda: not block._refreshing)
        block.update_snapshot(motores_snapshot())
        return block

    # -- página ------------------------------------------------------------

    def test_page_builds_with_its_four_blocks(self):
        import pc_ai_monitor.blocks.motores_vista as vista
        from pc_ai_monitor.blocks import actualizaciones, motores
        from pc_ai_monitor.pages.motores import MotoresPage

        with (
            mock.patch.object(vista, "_run_manager", lambda *a, **k: _Result(0, stdout='{"resident": null}')),
            mock.patch.object(motores, "run_manager", lambda *a, **k: _Result(0, stdout="[]")),
            mock.patch.object(actualizaciones, "run_manager", lambda *a, **k: _Result(0, stdout='{"components": []}')),
            mock.patch.object(vista, "_http_text", lambda *a, **k: None),
            mock.patch.object(vista, "_run_gufo_flash_status", lambda: ("", None, "missing")),
        ):
            page = MotoresPage()
        page.update_snapshot(motores_snapshot())

        self.assertEqual(len(page._blocks), 4)
        # Dos columnas: residente + actualizaciones a la izquierda,
        # rendimiento + inventario a la derecha.
        residente = page._blocks[0]
        self.assertIs(page._blocks[1].get_parent(), residente.get_parent())
        rendimiento = page._blocks[2]
        self.assertIs(page._blocks[3].get_parent(), rendimiento.get_parent())

    # -- motor residente ---------------------------------------------------

    def test_resident_cards_show_state_model_ctx_and_figures(self):
        block = self._resident_block(json.dumps(RESIDENT_GUFO))

        self.assertEqual(len(block._cards), 2)  # siempre Abito y Gufo
        self.assertEqual(
            block._headline.get_text(), "Residente: Gufo"
        )
        gufo = block._cards["gufo"]
        self.assertEqual(gufo["status"].get_text(), "activo")
        self.assertIn("ctx 131.072", gufo["meta"].get_text())
        self.assertIn("≈ 85 GiB reservados", gufo["figures"].get_text())
        self.assertIn("49.2 GiB en uso", gufo["figures"].get_text())
        self.assertFalse(gufo["button"].get_visible())

        abito = block._cards["abito"]
        self.assertEqual(abito["status"].get_text(), "parado")
        self.assertIn("≈ 20 GiB reservados", abito["figures"].get_text())
        self.assertEqual(abito["button"].get_label(), "Cambiar a Abito")

    def test_load_hint_is_visible_on_each_card(self):
        block = self._resident_block(json.dumps(RESIDENT_GUFO))

        self.assertIn("~90 s", block._cards["gufo"]["hint"].get_text())
        self.assertIn("~30 s", block._cards["abito"]["hint"].get_text())

    def test_retired_flash_engine_never_appears(self):
        """L9: el motor retirado «flash» (11008) jamás llega a las tarjetas."""
        block = self._resident_block(json.dumps(RESIDENT_GUFO))
        block.update_snapshot(motores_snapshot(models=[
            *MODELS,
            # Un motor retirado con 77 GiB medidos no puede filtrarse a
            # las cifras de las tarjetas (77 GiB fueron borrados hoy).
            {"alias": "flash", "pid": 103, "port": 11008, "model": "flash.gguf",
             "rss_gib": 77.0, "cpu": 0.0, "gtt_gib": 0.0, "vram_gib": 0.0},
        ]))

        self.assertEqual(set(block._cards), {"abito", "gufo"})
        self.assertIn(
            "49.2 GiB en uso", block._cards["gufo"]["figures"].get_text()
        )
        # Abito no es residente: «—», nunca un 0 ni la medición vieja.
        self.assertIn("—", block._cards["abito"]["figures"].get_text())
        self.assertNotIn("77", block._cards["abito"]["figures"].get_text())

    def test_switch_button_asks_with_the_real_confirmation(self):
        import pc_ai_monitor.blocks.motores_vista as vista
        from pc_ai_monitor.blocks.residente_logica import confirm_text

        block = self._resident_block(json.dumps(RESIDENT_GUFO))

        created = []

        class Recording(FakeAdwDialog):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        title, body = confirm_text("abito")
        with mock.patch.object(vista.Adw, "MessageDialog", Recording):
            block._cards["abito"]["button"].emit("clicked")

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].heading, title)
        self.assertEqual(created[0].body, body)

    # -- rendimiento como instrumentos (S7) --------------------------------

    def test_instruments_show_sin_datos_with_one_reading(self):
        """S11: una sola lectura nunca se pinta como 0."""
        block = self._rendimiento_block()

        self.assertIn("sin datos", block._decode_value.get_text())
        self.assertIn("sin datos", block._prefill_value.get_text())
        self.assertNotIn("0 tok/s", block._decode_value.get_text())
        self.assertNotIn("0 tok/s", block._prefill_value.get_text())
        self.assertEqual(block._decode_spark._values, [])
        self.assertEqual(block._prefill_spark._values, [])

    def test_manual_refresh_button_and_freshness_footer(self):
        import pc_ai_monitor.blocks.motores_vista as vista

        reads = []

        def fake_http(path, timeout=3.0):
            reads.append(path)

        for name, double in (
            ("_http_text", fake_http),
            ("_run_gufo_flash_status", lambda: ("", None, "missing")),
        ):
            patcher = mock.patch.object(vista, name, double)
            patcher.start()
            self.addCleanup(patcher.stop)

        block = vista.RendimientoVistaBlock()
        self._pump_until(lambda: not block._refreshing)
        block._refresh_button.emit("clicked")
        self._pump_until(lambda: not block._refreshing)

        self.assertIn("/metrics", reads)
        self.assertIn("sin lecturas", block._footer.get_text())

    def test_memory_bars_show_the_guard_minima(self):
        from pc_ai_monitor.blocks.rendimiento_logica import Guard

        block = self._rendimiento_block()
        block._on_read_done(
            metrics=_metrics(),
            served=131072,
            slots=(),
            guard=Guard(
                mem_available_gib=51.4,
                conservative_gib=46.6,
                min_mem_available_gib=20.0,
                min_conservative_gib=24.0,
                engine_ready=True,
            ),
        )

        self.assertEqual(len(block._headroom_bars), 2)
        available_bar, conservative_bar = block._headroom_bars
        available_row = available_bar._row
        conservative_row = conservative_bar._row
        assert available_row is not None and conservative_row is not None
        self.assertEqual(available_row.min_gib, 20.0)
        self.assertEqual(conservative_row.min_gib, 24.0)
        self.assertIn("mín 20 GiB", _bar_value_text(available_bar))
        self.assertIn("mín 24 GiB", _bar_value_text(conservative_bar))

    def test_warning_banner_covers_the_guard_floors(self):
        from pc_ai_monitor.blocks.rendimiento_logica import Guard

        block = self._rendimiento_block()
        block._on_read_done(
            metrics=_metrics(),
            served=131072,
            slots=(),
            guard=Guard(mem_available_gib=10.0, engine_ready=True),
        )

        self.assertTrue(block._warn_banner.get_revealed())
        self.assertIn("12 GiB", block._warn_banner.get_title())


def _metrics():
    from pc_ai_monitor.blocks.rendimiento_logica import parse_metrics

    return parse_metrics(
        "llamacpp:prompt_tokens_total 43512\n"
        "llamacpp:prompt_tokens_cached_total 38900\n"
        "llamacpp:kv_cache_usage_ratio 0.25\n"
        "llamacpp:requests_processing 1\n"
        "gufo_device_lost_total 0\n"
    )


def _bar_value_text(bar):
    """El valor de la barra vive en la etiqueta hermana del bloque."""
    return bar._value.get_text()


class _Result:
    """Doble mínimo de CommandResult para los tests."""

    def __init__(self, returncode, stdout="", stderr="", failure=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.failure = failure


if __name__ == "__main__":
    unittest.main()


class DisabledActionLookTests(unittest.TestCase):
    """Un «Actualizar» deshabilitado no puede verse como uno activo."""

    def test_base_css_neutralises_disabled_accent_buttons(self):
        from pc_ai_monitor import theme

        css = theme.BASE_CSS
        self.assertIn("button.suggested-action:disabled", css)
        rule = css.split("button.suggested-action:disabled", 1)[1].split("}", 1)[0]
        self.assertIn("@theme_subtle", rule)
        self.assertIn("@theme_muted", rule)


class RingTextFitTests(unittest.TestCase):
    """El valor del anillo cabe en el hueco interior, también en aros chicos."""

    def test_small_ring_font_fits_inside_the_hole(self):
        from pc_ai_monitor.widgets import instrumentos

        size, thickness = 54, 9
        radius = (size - thickness) / 2 - 1
        inner = 2 * radius - thickness
        font = instrumentos.ring_font(size, size, thickness)
        # «91 %» en monospace ocupa ~2.6 veces el tamaño de fuente.
        self.assertLessEqual(font * 2.6, inner)
