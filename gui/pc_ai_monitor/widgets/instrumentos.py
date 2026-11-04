"""Cairo instruments for the MacacoView cockpit (design 07).

Every widget here is a thin ``Chart`` subclass: the geometry lives in
``instrumentos_logica`` (display-free, tested), the drawing reads colours only
through ``pc_ai_monitor.theme`` and calls ``queue_draw`` on data changes.
Tooltips hit-test the last drawn layout via ``query-tooltip``.
"""

# pyright: reportAttributeAccessIssue=false

import math

from gi.repository import GObject, Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.formato import truncate
from pc_ai_monitor.widgets import instrumentos_logica as logica
from pc_ai_monitor.widgets.base import Chart, draw_text


def _color(key: str) -> str:
    """Palette key ('accent') or literal '#rrggbb' → a colour string."""
    if key.startswith("#"):
        return key
    palette = theme.active()
    return getattr(palette, key, None) or palette.accent


def hatch_rect(
    cr,
    x: float,
    y: float,
    w: float,
    h: float,
    color: str,
    spacing: float = 6.0,
    line_width: float = 1.2,
    alpha: float = 1.0,
) -> None:
    """Diagonal hatch clipped to the rectangle (the reservation overlay)."""
    if w <= 0 or h <= 0:
        return
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.set_source_rgba(*theme.rgba(color, alpha))
    cr.set_line_width(line_width)
    offset = -h
    while offset < w:
        cr.move_to(x + offset, y + h)
        cr.line_to(x + offset + h, y)
        offset += spacing
    cr.stroke()
    cr.restore()


def round_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    if w <= 0 or h <= 0:
        return
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


# ---------------------------------------------------------------------------
# RAM blocks
# ---------------------------------------------------------------------------

# kind → (fill, border, text, dashed)
_RAM_STYLES = {
    "active": ("accent", "accent", "background", False),
    "idle": (None, "blue", "blue", True),
    "other": (None, "muted", "muted", False),
    "free": (None, "border", "muted", True),
}


class RamBlocks(Chart):
    """RAM of the machine as ordered blocks with a hatched reservation (S9)."""

    def __init__(self, width: int = 560, height: int = 66, gap: float = 3.0) -> None:
        super().__init__(width, height)
        self._blocks: tuple[logica.RamBlock, ...] = ()
        self._gap = gap
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)

    def set_data(self, blocks) -> None:
        self._blocks = tuple(blocks)
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        rects = logica.ram_layout(self._blocks, float(width), self._gap)
        bar_h = float(height) - 4
        for rect in rects:
            fill, border, text, dashed = _RAM_STYLES.get(rect.block.kind, _RAM_STYLES["free"])
            used_w = (
                rect.width
                * max(0.0, rect.block.used_gib)
                / max(1e-9, max(rect.block.reserved_gib, rect.block.used_gib))
                if rect.block.reserved_gib > rect.block.used_gib
                else rect.width
            )
            if fill:
                cr.set_source_rgba(*theme.rgba(_color(fill), 0.16))
                round_rect(cr, rect.x, 2, rect.width, bar_h, 6)
                cr.fill()
            cr.set_line_width(1.5 if rect.block.kind == "active" else 1.0)
            cr.set_source_rgba(*theme.rgba(_color(border)))
            if dashed:
                cr.set_dash([4.0, 3.0])
            round_rect(cr, rect.x + 0.5, 2.5, max(1.0, rect.width - 1), bar_h - 1, 6)
            cr.stroke()
            cr.set_dash([])

            if used_w < rect.width - 0.5:  # reservation overlay (S9)
                hatch_rect(
                    cr,
                    rect.x + used_w,
                    3.0,
                    rect.width - used_w,
                    bar_h - 2,
                    palette.accent,
                    alpha=0.55,
                )
            if rect.width > 46:
                draw_text(
                    area,
                    cr,
                    truncate(rect.block.label, max(3, round(rect.width / 9))),
                    rect.x + 8,
                    18,
                    _color(text),
                    12,
                    True,
                )
            if rect.width > 90 and rect.block.sublabel:
                draw_text(
                    area, cr, rect.block.sublabel, rect.x + 8, 34, palette.muted, 9
                )
            if rect.width > 90:
                draw_text(
                    area,
                    cr,
                    f"{rect.block.used_gib:.1f} GiB",
                    rect.x + 8,
                    height - 16,
                    _color(text),
                    11,
                )

    def _tooltip(self, _area, x: float, y: float, _keyboard, tooltip) -> bool:
        del y
        if not self._blocks:
            return False
        rects = logica.ram_layout(self._blocks, float(self.get_width()), self._gap)
        for rect in rects:
            if rect.x <= x <= rect.x + rect.width:
                tooltip.set_text(logica.ram_tooltip(rect.block))
                return True
        return False


# ---------------------------------------------------------------------------
# Motor band
# ---------------------------------------------------------------------------

_SEGMENT_STYLES = {
    "gufo": ("accent", False),
    "abito": ("blue", False),
    "down": ("error", False),
    "restart": ("accent", True),
    "gap": ("error", True),
}

_MARKER_COLORS = {
    "switch": "accent",
    "crash": "error",
    "update": "green",
    "rollback": "warning",
}


class MotorBand(Chart):
    """15-minute engine state band with hit-testable event markers."""

    def __init__(self, width: int = 560, height: int = 74, window_s: float = 900.0) -> None:
        super().__init__(width, height)
        self._segments: tuple[logica.BandSegment, ...] = ()
        self._markers: tuple[logica.BandMarker, ...] = ()
        self._now = 0.0
        self._window = window_s
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)

    def set_data(self, segments, markers, now_s: float) -> None:
        self._segments = tuple(segments)
        self._markers = tuple(markers)
        self._now = now_s
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        tmin, tmax = logica.band_window(self._now, self._window)
        plot_w = float(width)

        cr.set_source_rgba(*theme.rgba(palette.subtle))
        round_rect(cr, 0, 0, width, height, 6)
        cr.fill()

        for t in logica.band_ticks(tmin, tmax):
            x = logica.band_x(t, tmin, tmax, plot_w)
            cr.set_source_rgba(*theme.rgba(palette.border, 0.5))
            cr.set_line_width(1.0)
            cr.move_to(x, 0)
            cr.line_to(x, height)
            cr.stroke()

        band_y, band_h = 28.0, 34.0
        for segment in self._segments:
            clipped = logica.clip_segment(segment, tmin, tmax)
            if clipped is None:
                continue
            start, end = clipped
            x = logica.band_x(start, tmin, tmax, plot_w)
            w = logica.band_x(end, tmin, tmax, plot_w) - x
            color, hatched = _SEGMENT_STYLES.get(segment.kind, _SEGMENT_STYLES["gap"])
            if hatched:
                hatch_rect(cr, x, band_y, w, band_h, color, spacing=7.0)
                cr.set_source_rgba(*theme.rgba(_color(color)))
                round_rect(cr, x, band_y, w, band_h, 4)
                cr.set_line_width(1.0)
                cr.stroke()
            else:
                cr.set_source_rgba(*theme.rgba(_color(color), 0.85))
                round_rect(cr, x, band_y, w, band_h, 4)
                cr.fill()

        for marker in self._markers:
            x = logica.band_x(marker.t, tmin, tmax, plot_w)
            color = _color(_MARKER_COLORS.get(marker.kind, "muted"))
            cr.set_source_rgba(*theme.rgba(color, 0.8))
            cr.set_line_width(1.5)
            cr.move_to(x, 16)
            cr.line_to(x, band_y)
            cr.stroke()
            cr.set_source_rgba(*theme.rgba(color))
            cr.arc(x, 10.0, 5.0, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(*theme.rgba(palette.background))
            cr.arc(x, 10.0, 2.0, 0, 2 * math.pi)
            cr.fill()

        # «ahora» at the right edge
        cr.set_source_rgba(*theme.rgba(palette.accent, 0.9))
        cr.set_line_width(1.5)
        cr.move_to(plot_w - 1, 0)
        cr.line_to(plot_w - 1, height)
        cr.stroke()

    def _tooltip(self, _area, x: float, y: float, _keyboard, tooltip) -> bool:
        del y
        tmin, tmax = logica.band_window(self._now, self._window)
        marker = logica.marker_hit(
            self._markers, x, tmin, tmax, float(self.get_width()), tolerance=10.0
        )
        if marker is None:
            return False
        tooltip.set_text(marker.tooltip)
        return True


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------


class Lanes(Chart):
    """1–N area lanes on one shared time axis with a shared cursor.

    ``set_cursor(t | None)`` freezes the cursor; the motion controller sets it
    to the hovered instant and the keyboard moves it (Left/Right/Home/End,
    Escape clears). Every change emits ``cursor-changed`` (value, active) so a
    sibling widget can follow.
    """

    __gsignals__ = {  # noqa: RUF012 - PyGObject reads this class mapping
        "cursor-changed": (
            GObject.SignalFlags.RUN_FIRST,
            GObject.TYPE_NONE,
            (GObject.TYPE_DOUBLE, GObject.TYPE_BOOLEAN),
        )
    }

    STEP_S = 10.0
    BIG_STEP_S = 60.0

    def __init__(
        self, width: int = 560, lane_height: int = 64, axis_height: int = 26
    ) -> None:
        super().__init__(width, lane_height + axis_height)
        self._lanes: tuple[logica.Lane, ...] = ()
        self._lane_height = lane_height
        self._axis_height = axis_height
        self._cursor: float | None = None

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        self.add_controller(motion)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._key)
        self.add_controller(key)
        self.set_focusable(True)

    def set_data(self, lanes) -> None:
        self._lanes = tuple(lanes)
        self.set_content_height(len(self._lanes) * self._lane_height + self._axis_height)
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def get_cursor(self) -> float | None:
        return self._cursor

    def set_cursor(self, t: float | None) -> None:
        tmin = self._tmin()
        tmax = self._tmax()
        value = logica.clamp_cursor(t, tmin, tmax)
        if value == self._cursor or (value is not None and self._cursor is not None
                                     and math.isclose(value, self._cursor)):
            return
        self._cursor = value
        self.queue_draw()
        self.emit("cursor-changed", value if value is not None else 0.0, value is not None)

    def _tmin(self) -> float:
        points = [p[0] for lane in self._lanes for p in lane.points]
        return min(points) if points else 0.0

    def _tmax(self) -> float:
        points = [p[0] for lane in self._lanes for p in lane.points]
        return max(points) if points else 900.0

    # -- controllers ------------------------------------------------------

    def _motion(self, _controller, x: float, _y: float) -> None:
        if self._lanes:
            self.set_cursor(self._tmin() + x / max(1.0, self.get_width())
                            * (self._tmax() - self._tmin()))

    def _leave(self, _controller) -> None:
        self.set_cursor(None)

    def _key(self, _controller, keyval, _keycode, state) -> bool:
        from gi.repository import Gdk

        key = Gdk.keyval_name(keyval)
        if key in ("Left", "Right"):
            step = (
                self.BIG_STEP_S
                if state & Gdk.ModifierType.SHIFT_MASK
                else self.STEP_S
            )
            self.set_cursor(
                logica.cursor_step(self._cursor, key, self._tmin(), self._tmax(), step)
            )
            return True
        if key in ("Home", "End", "Escape"):
            self.set_cursor(logica.cursor_step(self._cursor, key, self._tmin(), self._tmax()))
            return True
        return False

    # -- drawing ----------------------------------------------------------

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        tmin, tmax = self._tmin(), self._tmax()
        lane_h = float(self._lane_height)

        for index, lane in enumerate(self._lanes):
            top = index * lane_h + 18.0
            bottom = (index + 1) * lane_h - 6.0
            color = _color(lane.color)

            for run in logica.lane_runs(
                lane.points, tmin, tmax, 0.0, float(width), top, bottom, lane.low, lane.high
            ):
                if len(run) < 2:
                    continue
                cr.move_to(run[0][0], bottom)
                for x, y in run:
                    cr.line_to(x, y)
                cr.line_to(run[-1][0], bottom)
                cr.close_path()
                cr.set_source_rgba(*theme.rgba(color, 0.16))
                cr.fill()

                cr.move_to(*run[0])
                for x, y in run[1:]:
                    cr.line_to(x, y)
                cr.set_source_rgba(*theme.rgba(color))
                cr.set_line_width(1.6)
                cr.stroke()

            if lane.stale_tail:
                tail = logica.lane_stale_tail(
                    lane.points, tmin, tmax, 0.0, float(width), top, bottom,
                    lane.low, lane.high,
                )
                if tail is not None:
                    cr.move_to(tail[0], tail[1])
                    cr.line_to(tail[2], tail[3])
                    cr.set_source_rgba(*theme.rgba(palette.warning))
                    cr.set_line_width(1.4)
                    cr.set_dash([4.0, 4.0])
                    cr.stroke()
                    cr.set_dash([])

            draw_text(
                area, cr, lane.label.upper(), 8, index * lane_h + 10, palette.muted, 9, True
            )

        axis_y = len(self._lanes) * lane_h
        cr.set_source_rgba(*theme.rgba(palette.border))
        cr.set_line_width(1.0)
        cr.move_to(0, axis_y)
        cr.line_to(width, axis_y)
        cr.stroke()
        for t in logica.band_ticks(tmin, tmax, step_s=max(1.0, (tmax - tmin) / 6)):
            x = logica.lane_x(t, tmin, tmax, 0.0, float(width))
            draw_text(area, cr, _hm(t), x, axis_y + 14, palette.muted, 8, centered=True)
        draw_text(area, cr, "ahora", width - 4, axis_y + 14, palette.accent, 8, True, True)

        if self._cursor is not None:
            x = logica.lane_x(self._cursor, tmin, tmax, 0.0, float(width))
            cr.set_source_rgba(*theme.rgba(palette.text, 0.7))
            cr.set_line_width(1.0)
            cr.set_dash([3.0, 3.0])
            cr.move_to(x, 0)
            cr.line_to(x, axis_y)
            cr.stroke()
            cr.set_dash([])
            for index, lane in enumerate(self._lanes):
                value = _value_at(lane, self._cursor)
                if value is None:
                    continue
                y = logica.lane_y(
                    value, lane.low, lane.high, index * lane_h + 18.0,
                    (index + 1) * lane_h - 6.0,
                )
                cr.set_source_rgba(*theme.rgba(_color(lane.color)))
                cr.arc(x, y, 3.5, 0, 2 * math.pi)
                cr.fill()


def _value_at(lane: logica.Lane, t: float) -> float | None:
    """Nearest sample value at or before ``t``; ``None`` in a gap or off data."""
    best: float | None = None
    best_t: float | None = None
    for point_t, value in lane.points:
        if point_t <= t and (best_t is None or point_t >= best_t):
            best_t = point_t
            best = value
    return best


def _hm(t: float) -> str:
    seconds = int(t)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Ring, dial, lamp, version rail
# ---------------------------------------------------------------------------


class Ring(Chart):
    """Single-value ring 0–100 % with track, centre text and «sin datos»."""

    def __init__(self, size: int = 64, thickness: int = 10) -> None:
        super().__init__(size, size)
        self._pct: float | None = None
        self._text = ""
        self._color = "accent"
        self._thickness = thickness

    def set_data(self, pct: float | None, text: str = "", color: str = "accent") -> None:
        self._pct = pct
        self._text = text
        self._color = color
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        cx, cy = width / 2, height / 2
        radius = (min(width, height) - self._thickness) / 2 - 1
        sweep, text, no_data = logica.ring_state(self._pct, self._text)

        cr.set_line_width(self._thickness)
        cr.set_line_cap(1)
        cr.set_source_rgba(*theme.rgba(palette.subtle if not no_data else palette.border))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        if not no_data and sweep > 0:
            cr.set_source_rgba(*theme.rgba(_color(self._color)))
            cr.arc(cx, cy, radius, -math.pi / 2, -math.pi / 2 + math.radians(sweep))
            cr.stroke()

        draw_text(
            area, cr, text, cx, cy, palette.text if not no_data else palette.muted,
            21 if not no_data else 10, not no_data, True,
        )


class Dial(Chart):
    """Small 240° arc gauge with a needle for a 0–1 value (MTP acceptance)."""

    def __init__(self, size: int = 66) -> None:
        super().__init__(size, size)
        self._value: float | None = None
        self._color = "blue"

    def set_data(self, value: float | None, color: str = "blue") -> None:
        self._value = value
        self._color = color
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        cx, cy, radius = width / 2, height / 2 - 2, min(width, height) / 2 - 9
        thickness = 8.0
        angle, no_data = logica.dial_state(self._value)

        cr.set_line_width(thickness)
        cr.set_line_cap(1)
        start = math.radians(logica.DIAL_START)
        end = math.radians(logica.DIAL_START + logica.DIAL_SWEEP)
        cr.set_source_rgba(*theme.rgba(palette.border if not no_data else palette.subtle))
        cr.arc(cx, cy, radius, start, end)
        cr.stroke()

        if not no_data:
            cr.set_source_rgba(*theme.rgba(_color(self._color)))
            cr.arc(cx, cy, radius, start, math.radians(angle))
            cr.stroke()
            tip = logica.dial_needle(cx, cy, radius - thickness - 3, angle)
            cr.set_source_rgba(*theme.rgba(palette.text))
            cr.set_line_width(2.5)
            cr.move_to(cx, cy)
            cr.line_to(*tip)
            cr.stroke()
            cr.set_source_rgba(*theme.rgba(palette.background))
            cr.arc(cx, cy, 4.5, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(*theme.rgba(palette.text))
            cr.set_line_width(1.5)
            cr.stroke()

        draw_text(
            area, cr, "sin datos" if no_data else f"{self._value:.2f}", cx, cy + radius + 8,
            palette.muted if no_data else palette.text, 10 if no_data else 12, not no_data, True,
        )


class Lamp(Chart):
    """Status lamp: ok = circle green, stale = diamond amber, down = square red,
    off = hollow circle muted."""

    def __init__(self, size: int = 12) -> None:
        super().__init__(size, size)
        self._state = "off"

    def set_data(self, state: str) -> None:
        self._state = state
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        del area
        shape, color_key = logica.lamp_state(self._state)
        cx, cy = width / 2, height / 2
        r = min(width, height) / 2 - 2
        hollow = shape == "hollow"
        color = _color(color_key)
        cr.set_line_width(1.5)
        if shape in ("circle", "hollow"):
            if not hollow:
                cr.set_source_rgba(*theme.rgba(color))
                cr.arc(cx, cy, r, 0, 2 * math.pi)
                cr.fill()
            else:
                cr.set_source_rgba(*theme.rgba(color))
                cr.arc(cx, cy, r - 0.5, 0, 2 * math.pi)
                cr.stroke()
        elif shape == "diamond":
            cr.set_source_rgba(*theme.rgba(color))
            cr.move_to(cx, cy - r)
            cr.line_to(cx + r, cy)
            cr.line_to(cx, cy + r)
            cr.line_to(cx - r, cy)
            cr.close_path()
            cr.fill()
        else:  # square
            cr.set_source_rgba(*theme.rgba(color))
            cr.rectangle(cx - r, cy - r, 2 * r, 2 * r)
            cr.fill()


class VersionRail(Chart):
    """«from_label → to_label» rail with a status and an optional stepper."""

    def __init__(self, width: int = 230, height: int = 92) -> None:
        super().__init__(width, height)
        self._from = ""
        self._to = ""
        self._status = "sin_consulta"
        self._progress = 0.0
        self._steps: list[logica.RailStep] = []

    def set_data(self, from_label: str, to_label: str, status: str,
                 progress: float = 0.0, steps=()) -> None:
        self._from = from_label
        self._to = to_label
        self._status = status
        self._progress = progress
        self._steps = logica.normalize_steps(tuple(steps)) if steps else []
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        view = logica.rail_geometry(self._status, self._progress)
        accent = palette.muted if view.muted else _color(
            "green" if view.done else "warning" if view.reverted else "accent"
        )
        xa, xb, y = 34.0, width - 34.0, 22.0

        cr.set_line_width(3.0)
        cr.set_line_cap(1)
        cr.set_source_rgba(*theme.rgba(palette.border))
        cr.move_to(xa + 12, y)
        cr.line_to(xb - 12, y)
        cr.stroke()

        if view.running:
            cr.set_source_rgba(*theme.rgba(accent))
            cr.move_to(xa + 12, y)
            cr.line_to(xa + 12 + (xb - xa - 24) * view.ratio, y)
            cr.stroke()

        # running dot (travels with progress while updating)
        rx = xa + (xb - xa) * view.ratio
        if not view.muted:
            if view.done:
                cr.set_source_rgba(*theme.rgba(palette.green, 0.18))
                cr.arc(rx, y, 12, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgba(*theme.rgba(accent))
            cr.arc(rx, y, 6.5, 0, 2 * math.pi)
            cr.fill()
        else:
            cr.set_source_rgba(*theme.rgba(palette.border))
            cr.arc(rx, y, 6.5, 0, 2 * math.pi)
            cr.stroke()

        # target: hollow ring (dashed + X when reverted)
        cr.set_line_width(2.0)
        cr.set_source_rgba(*theme.rgba(palette.border))
        cr.arc(xb, y, 9, 0, 2 * math.pi)
        cr.stroke()
        if view.reverted:
            cr.set_source_rgba(*theme.rgba(palette.warning))
            cr.set_line_width(2.2)
            cr.move_to(xb - 5, y - 5)
            cr.line_to(xb + 5, y + 5)
            cr.move_to(xb + 5, y - 5)
            cr.line_to(xb - 5, y + 5)
            cr.stroke()

        draw_text(area, cr, self._from, xa, height - 30, palette.text, 11, True, True)
        to_color = palette.text if view.muted else (
            palette.green if view.done else palette.warning if view.reverted else palette.text
        )
        draw_text(area, cr, self._to, xb, height - 30, to_color, 11, True, True)
        draw_text(area, cr, self._status, width / 2, y - 14, accent, 9, True, True)

        if self._steps:
            self._draw_steps(area, cr, width, height)

    def _draw_steps(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        y = height - 12.0
        count = len(self._steps)
        gap = (width - 24.0) / max(1, count - 1) if count > 1 else 0.0
        positions = [12.0 + index * gap for index in range(count)]

        for index in range(count - 1):
            key = logica.connector_state(self._steps, index)
            color = palette.green if key == "ok" else palette.warning if key == "warning" else palette.border
            cr.set_source_rgba(*theme.rgba(color))
            cr.set_line_width(2.0)
            cr.move_to(positions[index] + 7, y)
            cr.line_to(positions[index + 1] - 7, y)
            cr.stroke()

        for step, x in zip(self._steps, positions, strict=True):
            if step.state == "done":
                cr.set_source_rgba(*theme.rgba(palette.green))
                cr.arc(x, y, 6.5, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(*theme.rgba(palette.background))
                cr.set_line_width(2.0)
                cr.move_to(x - 3, y)
                cr.line_to(x - 1, y + 3)
                cr.line_to(x + 3, y - 3)
                cr.stroke()
            else:
                color = (
                    palette.accent if step.state == "current"
                    else palette.warning if step.state == "failed"
                    else palette.border
                )
                cr.set_source_rgba(*theme.rgba(color))
                cr.set_line_width(2.0)
                cr.arc(x, y, 6.5, 0, 2 * math.pi)
                cr.stroke()
                if step.state == "failed":
                    cr.move_to(x - 3, y - 3)
                    cr.line_to(x + 3, y + 3)
                    cr.move_to(x + 3, y - 3)
                    cr.line_to(x - 3, y + 3)
                    cr.stroke()
            draw_text(
                area, cr, step.name, x, y + 12, palette.muted, 8, False, True
            )
