# PyGObject no trae stubs propios; ver gui/typings/gi y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests for the MacacoView Cairo instruments (widgets/instrumentos).

The pure geometry lives in ``instrumentos_logica`` (importable without a
display); the smoke tests at the end build the real GTK widgets, so they need
a display — even a virtual one:

    xvfb-run -a python3 -m unittest tests.test_instrumentos -v
"""

import unittest

from pc_ai_monitor.widgets import instrumentos_logica as logica


class RamBlockLayoutTests(unittest.TestCase):
    """The bar is a partition of the total GiB; the reservation is an overlay
    inside its own block and can never spill past the bar."""

    @staticmethod
    def _blocks():
        return (
            logica.RamBlock("Gufo", "en uso", 49.2, 85.0, "active"),
            logica.RamBlock("Otros", "procesos", 12.0, 12.0, "other"),
            logica.RamBlock("Abito", "reservado", 19.8, 20.0, "idle"),
            logica.RamBlock("Libre", "", 41.0, 41.0, "free"),
        )

    def test_widths_sum_to_bar_width(self):
        rects = logica.ram_layout(self._blocks(), 560.0, gap=3.0)
        usable = 560.0 - 3.0 * (len(rects) - 1)
        self.assertAlmostEqual(sum(r.width for r in rects), usable, places=6)
        self.assertAlmostEqual(
            sum(r.width for r in rects) + 3.0 * (len(rects) - 1), 560.0, places=6
        )

    def test_reserved_overlay_never_exceeds_block(self):
        rects = logica.ram_layout(self._blocks(), 560.0)
        for rect in rects:
            self.assertLessEqual(rect.reserved_width, rect.width + 1e-9)
            self.assertGreaterEqual(rect.reserved_width, 0.0)
        gufo = rects[0]
        self.assertGreater(gufo.reserved_width, 0.0)

    def test_no_overlay_when_reserved_le_used(self):
        blocks = (logica.RamBlock("A", "", 10.0, 10.0, "active"),)
        (rect,) = logica.ram_layout(blocks, 300.0)
        self.assertEqual(rect.reserved_width, 0.0)

    def test_tooltip_carries_both_figures(self):
        text = logica.ram_tooltip(logica.RamBlock("Gufo", "en uso", 49.2, 85.0, "active"))
        self.assertIn("Gufo", text)
        self.assertIn("49.2", text)
        self.assertIn("85", text)


class BandLayoutTests(unittest.TestCase):
    """Window edges map to the plot edges; segments clip at the window."""

    WINDOW = 900.0
    NOW = 900.0
    PLOT = 560.0

    def _x(self, t):
        return logica.band_x(t, 0.0, self.NOW, self.PLOT)

    def test_window_edges(self):
        self.assertAlmostEqual(self._x(self.NOW - self.WINDOW), 0.0, places=6)
        self.assertAlmostEqual(self._x(self.NOW), self.PLOT, places=6)

    def test_segment_clipping(self):
        seg = logica.BandSegment(start_s=-120.0, end_s=60.0, kind="gufo")
        clipped = logica.clip_segment(seg, 0.0, self.NOW)
        self.assertEqual(clipped, (0.0, 60.0))

    def test_segment_outside_window_is_dropped(self):
        seg = logica.BandSegment(start_s=-200.0, end_s=-50.0, kind="gufo")
        self.assertIsNone(logica.clip_segment(seg, 0.0, self.NOW))
        seg = logica.BandSegment(start_s=1000.0, end_s=1100.0, kind="gap")
        self.assertIsNone(logica.clip_segment(seg, 0.0, self.NOW))

    def test_marker_hit_test_tolerance(self):
        markers = (
            logica.BandMarker(t=450.0, kind="crash", tooltip="Gufo cayó"),
            logica.BandMarker(t=800.0, kind="update", tooltip="actualización"),
        )
        hit = logica.marker_hit(
            markers, self._x(450.0) + 2.0, 0.0, self.NOW, self.PLOT, tolerance=8.0
        )
        if hit is None:
            self.fail("marcador dentro de la tolerancia no encontrado")
        self.assertEqual(hit.tooltip, "Gufo cayó")
        self.assertIsNone(
            logica.marker_hit(
                markers, self._x(450.0) + 30.0, 0.0, self.NOW, self.PLOT, tolerance=8.0
            )
        )
        nearest = logica.marker_hit(
            markers, self._x(799.0), 0.0, self.NOW, self.PLOT, tolerance=8.0
        )
        if nearest is None:
            self.fail("marcador más cercano no encontrado")
        self.assertEqual(nearest.kind, "update")

    def test_ticks_every_three_minutes(self):
        ticks = logica.band_ticks(0.0, 900.0, step_s=180.0)
        self.assertEqual(ticks[0], 180.0)
        self.assertEqual(ticks[-1], 900.0)
        self.assertEqual(len(ticks), 5)


class LaneLayoutTests(unittest.TestCase):
    """value→y mapping, None gaps split the runs, cursor clamps and steps."""

    def test_value_to_y_mapping(self):
        self.assertAlmostEqual(logica.lane_y(0.0, 0.0, 100.0, 10.0, 50.0), 50.0)
        self.assertAlmostEqual(logica.lane_y(100.0, 0.0, 100.0, 10.0, 50.0), 10.0)
        self.assertAlmostEqual(logica.lane_y(50.0, 0.0, 100.0, 10.0, 50.0), 30.0)

    def test_none_gap_produces_separate_runs(self):
        points = ((0.0, 1.0), (1.0, 2.0), (2.0, None), (3.0, 4.0), (4.0, 5.0))
        runs = logica.lane_runs(points, 0.0, 900.0, 0.0, 560.0, 0.0, 50.0, 0.0, 6.0)
        self.assertEqual(len(runs), 2)
        self.assertEqual(len(runs[0]), 2)
        self.assertEqual(len(runs[1]), 2)

    def test_leading_none_is_no_run(self):
        points = ((0.0, None), (1.0, 2.0), (2.0, 3.0))
        runs = logica.lane_runs(points, 0.0, 900.0, 0.0, 560.0, 0.0, 50.0, 0.0, 6.0)
        self.assertEqual(len(runs), 1)

    def test_stale_tail_only_when_last_point_missing(self):
        tail = logica.lane_stale_tail(
            ((0.0, 1.0), (1.0, None)), 0.0, 900.0, 0.0, 560.0, 0.0, 50.0, 0.0, 6.0
        )
        if tail is None:
            self.fail("cola sin datos no dibujada")
        self.assertAlmostEqual(tail[1], tail[3])  # dashed, flat at last value
        self.assertAlmostEqual(tail[2], 560.0)  # reaches the window end
        self.assertIsNone(
            logica.lane_stale_tail(
                ((0.0, 1.0), (1.0, 2.0)), 0.0, 900.0, 0.0, 560.0, 0.0, 50.0, 0.0, 6.0
            )
        )

    def test_cursor_clamps_to_window(self):
        self.assertEqual(logica.clamp_cursor(950.0, 0.0, 900.0), 900.0)
        self.assertEqual(logica.clamp_cursor(-5.0, 0.0, 900.0), 0.0)
        self.assertIsNone(logica.clamp_cursor(None, 0.0, 900.0))

    def test_keyboard_steps(self):
        self.assertEqual(logica.cursor_step(None, "Left", 0.0, 900.0), 890.0)
        self.assertEqual(logica.cursor_step(None, "Right", 0.0, 900.0), 900.0)
        self.assertEqual(logica.cursor_step(450.0, "Left", 0.0, 900.0), 440.0)
        self.assertEqual(logica.cursor_step(450.0, "Right", 0.0, 900.0), 460.0)
        self.assertEqual(logica.cursor_step(450.0, "Home", 0.0, 900.0), 0.0)
        self.assertEqual(logica.cursor_step(450.0, "End", 0.0, 900.0), 900.0)
        self.assertIsNone(logica.cursor_step(450.0, "Escape", 0.0, 900.0))
        self.assertEqual(
            logica.cursor_step(450.0, "Left", 0.0, 900.0, step=10.0, big_step=60.0),
            440.0,
        )


class RingDialTests(unittest.TestCase):
    def test_ring_sweep(self):
        self.assertAlmostEqual(logica.ring_sweep(0.0), 0.0)
        self.assertAlmostEqual(logica.ring_sweep(50.0), 180.0)
        self.assertAlmostEqual(logica.ring_sweep(100.0), 360.0)

    def test_ring_clamps_out_of_range(self):
        self.assertAlmostEqual(logica.ring_sweep(150.0), 360.0)
        self.assertAlmostEqual(logica.ring_sweep(-5.0), 0.0)

    def test_ring_no_data(self):
        sweep, text, no_data = logica.ring_state(None, "42 %")
        self.assertEqual(sweep, 0.0)
        self.assertEqual(text, "sin datos")
        self.assertTrue(no_data)
        sweep, text, no_data = logica.ring_state(50.0, "50 %")
        self.assertAlmostEqual(sweep, 180.0)
        self.assertEqual(text, "50 %")
        self.assertFalse(no_data)

    def test_dial_angle_mapping(self):
        self.assertAlmostEqual(logica.dial_angle(0.0), 150.0)
        self.assertAlmostEqual(logica.dial_angle(0.5), 270.0)
        self.assertAlmostEqual(logica.dial_angle(1.0), 390.0)

    def test_dial_clamps_out_of_range(self):
        self.assertAlmostEqual(logica.dial_angle(2.0), 390.0)
        self.assertAlmostEqual(logica.dial_angle(-1.0), 150.0)

    def test_dial_needle(self):
        x, y = logica.dial_needle(50.0, 48.0, 26.0, 270.0)
        self.assertAlmostEqual(x, 50.0)
        self.assertAlmostEqual(y, 22.0)

    def test_dial_no_data(self):
        angle, no_data = logica.dial_state(None)
        self.assertEqual(angle, 150.0)
        self.assertTrue(no_data)


class LampTests(unittest.TestCase):
    def test_state_mapping(self):
        self.assertEqual(logica.lamp_state("ok"), ("circle", "green"))
        self.assertEqual(logica.lamp_state("stale"), ("diamond", "warning"))
        self.assertEqual(logica.lamp_state("down"), ("square", "error"))
        self.assertEqual(logica.lamp_state("off"), ("hollow", "muted"))

    def test_unknown_state_is_off(self):
        self.assertEqual(logica.lamp_state("naranja"), ("hollow", "muted"))


class RailTests(unittest.TestCase):
    def test_status_validation(self):
        for status in logica.RAIL_STATUS:
            view = logica.rail_geometry(status, 0.0)
            self.assertGreaterEqual(view.ratio, 0.0)
            self.assertLessEqual(view.ratio, 1.0)
        with self.assertRaises(ValueError):
            logica.rail_geometry("desactualizada", 0.0)

    def test_status_geometry(self):
        self.assertTrue(logica.rail_geometry("al_dia", 0.0).done)
        self.assertAlmostEqual(logica.rail_geometry("nueva", 0.0).ratio, 0.0)
        self.assertAlmostEqual(
            logica.rail_geometry("actualizando", 0.5).ratio, 0.5
        )
        self.assertTrue(logica.rail_geometry("revertida", 0.0).reverted)
        self.assertTrue(logica.rail_geometry("sin_consulta", 0.0).muted)
        self.assertAlmostEqual(
            logica.rail_geometry("actualizando", 7.0).ratio, 1.0
        )

    def test_stepper_state_validation(self):
        steps = logica.normalize_steps(
            (("pull", "done"), ("restart", "current"), ("verify", "todo"), ("done", "todo"))
        )
        self.assertEqual([s.state for s in steps], ["done", "current", "todo", "todo"])
        with self.assertRaises(ValueError):
            logica.normalize_steps((("pull", "casi"),))
        with self.assertRaises(ValueError):
            logica.normalize_steps((("saltar", "done"),))

    def test_connector_states(self):
        steps = logica.normalize_steps(
            (("pull", "done"), ("restart", "failed"), ("verify", "todo"))
        )
        self.assertEqual(logica.connector_state(steps, 0), "ok")
        self.assertEqual(logica.connector_state(steps, 1), "warning")
        self.assertEqual(logica.connector_state(steps, 2), "muted")
        self.assertEqual(logica.connector_state(steps, -1), "muted")


class WidgetSmokeTests(unittest.TestCase):
    """Every widget must build, accept data and redraw under a display."""

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

    def test_ram_blocks(self):
        from pc_ai_monitor.widgets.instrumentos import RamBlocks

        widget = RamBlocks()
        widget.set_data(
            (
                logica.RamBlock("Gufo", "en uso", 49.2, 85.0, "active"),
                logica.RamBlock("Otros", "", 12.0, 12.0, "other"),
                logica.RamBlock("Libre", "", 41.0, 41.0, "free"),
            )
        )
        widget.queue_draw()
        widget.redraw()

    def test_motor_band(self):
        from pc_ai_monitor.widgets.instrumentos import MotorBand

        widget = MotorBand()
        widget.set_data(
            (
                logica.BandSegment(0.0, 300.0, "abito"),
                logica.BandSegment(300.0, 380.0, "restart"),
                logica.BandSegment(380.0, 900.0, "gufo"),
            ),
            (logica.BandMarker(300.0, "switch", "cambio a Gufo"),),
            900.0,
        )
        widget.queue_draw()
        widget.redraw()

    def test_lanes(self):
        from pc_ai_monitor.widgets.instrumentos import Lanes

        widget = Lanes()
        widget.set_data(
            (
                logica.Lane(
                    label="Decode",
                    color="accent",
                    points=((0.0, 40.0), (300.0, 45.0), (310.0, None), (320.0, 44.0)),
                    low=0.0,
                    high=1200.0,
                    stale_tail=True,
                ),
            )
        )
        widget.queue_draw()
        widget.redraw()

    def test_lanes_cursor_signal(self):
        from pc_ai_monitor.widgets.instrumentos import Lanes

        widget = Lanes()
        widget.set_data(
            (
                logica.Lane(
                    label="d",
                    color="accent",
                    points=((0.0, 1.0), (900.0, 2.0)),
                    low=0.0,
                    high=3.0,
                ),
            )
        )
        seen: list[tuple[float, bool]] = []
        widget.connect(
            "cursor-changed", lambda _w, t, active: seen.append((t, active))
        )
        widget.set_cursor(450.0)
        self.assertEqual(seen, [(450.0, True)])
        widget.set_cursor(450.0)  # sin cambio: no emite
        self.assertEqual(len(seen), 1)
        widget.set_cursor(None)
        self.assertEqual(seen[-1], (0.0, False))
        self.assertIsNone(widget.get_cursor())

    def test_ring(self):
        from pc_ai_monitor.widgets.instrumentos import Ring

        widget = Ring()
        widget.set_data(62.0, "62 %", "green")
        widget.set_data(None, "")
        widget.queue_draw()
        widget.redraw()

    def test_dial(self):
        from pc_ai_monitor.widgets.instrumentos import Dial

        widget = Dial()
        widget.set_data(0.77)
        widget.set_data(None)
        widget.queue_draw()
        widget.redraw()

    def test_lamp(self):
        from pc_ai_monitor.widgets.instrumentos import Lamp

        widget = Lamp()
        for state in ("ok", "stale", "down", "off", "raro"):
            widget.set_data(state)
        widget.queue_draw()
        widget.redraw()

    def test_version_rail(self):
        from pc_ai_monitor.widgets.instrumentos import VersionRail

        widget = VersionRail()
        widget.set_data("0.27.4", "0.28.1", "nueva", 0.0)
        widget.set_data(
            "0.27.4",
            "0.28.1",
            "actualizando",
            0.5,
            (
                ("pull", "done"),
                ("restart", "current"),
                ("verify", "todo"),
                ("done", "todo"),
            ),
        )
        widget.queue_draw()
        widget.redraw()
