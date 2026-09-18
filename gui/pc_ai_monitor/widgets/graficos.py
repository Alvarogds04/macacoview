"""Charts drawn with Cairo.

GTK has no native donut, column chart or treemap, so these are drawing areas.
Each one is also a control: ``on_select`` receives the clicked slice, which is
what turns a chart into a filter instead of a picture.
"""

# pyright: reportAttributeAccessIssue=false

import math
from collections.abc import Callable, Sequence

from gi.repository import Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.formato import safe, truncate
from pc_ai_monitor.formato import tokens as fmt_tokens
from pc_ai_monitor.widgets.base import Chart, Slice, draw_text


class Dona(Chart):
    """Donut with the total in the middle. Segments are stroked arcs."""

    def __init__(
        self,
        size: int = 190,
        thickness: int = 24,
        on_select: Callable[[Slice], None] | None = None,
    ) -> None:
        super().__init__(size, size)
        self._slices: list[Slice] = []
        self._center_value = ""
        self._center_label = ""
        self._thickness = thickness
        self._on_select = on_select

        if on_select is not None:
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._pressed)
            self.add_controller(gesture)

    def set_data(
        self, slices: Sequence[Slice], center_value: str, center_label: str
    ) -> None:
        self._slices = list(slices)
        self._center_value = center_value
        self._center_label = center_label
        self.queue_draw()

    # -- geometry ---------------------------------------------------------

    def _arcs(self, width: int, height: int) -> list[tuple[Slice, float, float]]:
        total = sum(max(0.0, s.value) for s in self._slices)
        if total <= 0:
            return []
        arcs: list[tuple[Slice, float, float]] = []
        start = -math.pi / 2
        for item in self._slices:
            span = max(0.0, item.value) / total * 2 * math.pi
            if span > 0:
                arcs.append((item, start, span))
            start += span
        return arcs

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        radius = (min(width, height) - self._thickness) / 2 - 2
        cx, cy = width / 2, height / 2

        cr.set_line_width(self._thickness)
        cr.set_line_cap(1)  # CAIRO_LINE_CAP_ROUND
        cr.set_source_rgba(*theme.rgba(palette.subtle))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        for item, start, span in self._arcs(width, height):
            cr.set_source_rgba(*theme.rgba(item.color))
            cr.arc(cx, cy, radius, start, start + span)
            cr.stroke()

        draw_text(
            area, cr, self._center_value, cx, cy - 8, palette.text, 17, True, True
        )
        draw_text(
            area,
            cr,
            truncate(self._center_label, 18),
            cx,
            cy + 12,
            palette.muted,
            9,
            False,
            True,
        )

    def _pressed(self, _gesture, _count: int, x: float, y: float) -> None:
        if self._on_select is None:
            return
        palette = theme.active()
        del palette
        width, height = self.get_width(), self.get_height()
        radius = (min(width, height) - self._thickness) / 2 - 2
        cx, cy = width / 2, height / 2
        dx, dy = x - cx, y - cy
        distance = math.hypot(dx, dy)
        if not (
            radius - self._thickness / 2 <= distance <= radius + self._thickness / 2
        ):
            return
        angle = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
        for item, start, span in self._arcs(width, height):
            offset = (start + math.pi / 2) % (2 * math.pi)
            if offset <= angle <= offset + span:
                self._on_select(item)
                return


class Columnas(Chart):
    """Vertical columns scaled against the largest value in the set."""

    def __init__(
        self, height: int = 150, on_select: Callable[[Slice], None] | None = None
    ) -> None:
        super().__init__(560, height)
        self._slices: list[Slice] = []
        self._on_select = on_select
        self._rects: list[tuple[Slice, float, float, float, float]] = []
        self.set_hexpand(True)

        if on_select is not None:
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._pressed)
            self.add_controller(gesture)

    def set_data(self, slices: Sequence[Slice]) -> None:
        self._slices = list(slices)
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        self._rects = []
        if not self._slices:
            return

        peak = max(max(0.0, s.value) for s in self._slices)
        count = len(self._slices)
        gap = 8
        bar_width = max(6.0, min(34.0, (width - gap * (count + 1)) / max(1, count)))
        baseline = height - 30

        for index, item in enumerate(self._slices):
            x = gap + index * (bar_width + gap)
            ratio = max(0.0, item.value) / peak if peak > 0 else 0.0
            bar_height = max(2.0, ratio * (baseline - 22))
            y = baseline - bar_height

            cr.set_source_rgba(*theme.rgba(item.color))
            cr.rectangle(x, y, bar_width, bar_height)
            cr.fill()

            draw_text(
                area,
                cr,
                fmt_tokens(item.value),
                x + bar_width / 2,
                y - 10,
                palette.text,
                9,
                True,
                True,
            )
            draw_text(
                area,
                cr,
                truncate(item.label, 10),
                x + bar_width / 2,
                baseline + 12,
                palette.muted,
                8,
                False,
                True,
            )
            self._rects.append((item, x, y, bar_width, bar_height))

    def _pressed(self, _gesture, _count: int, x: float, y: float) -> None:
        if self._on_select is None:
            return
        for item, rx, ry, rw, rh in self._rects:
            if rx - 4 <= x <= rx + rw + 4 and ry - 14 <= y <= ry + rh + 16:
                self._on_select(item)
                return


class Treemap(Chart):
    """Area proportional to value, with the reading order kept."""

    def __init__(
        self, height: int = 190, on_select: Callable[[Slice], None] | None = None
    ) -> None:
        super().__init__(560, height)
        self._slices: list[Slice] = []
        self._on_select = on_select
        self._rects: list[tuple[Slice, float, float, float, float]] = []
        self.set_hexpand(True)

        if on_select is not None:
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._pressed)
            self.add_controller(gesture)

    def set_data(self, slices: Sequence[Slice]) -> None:
        self._slices = [s for s in slices if s.value > 0]
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        self._rects = []
        placed = _layout(self._slices, (0, 0, width, height), True)

        for item, (x, y, w, h) in placed:
            cr.set_source_rgba(*theme.rgba(item.color))
            cr.rectangle(x + 1, y + 1, max(1.0, w - 2), max(1.0, h - 2))
            cr.fill()

            if w > 54 and h > 26:
                draw_text(
                    area,
                    cr,
                    truncate(item.label, max(4, round(w / 8))),
                    x + 6,
                    y + 12,
                    "#000000",
                    9,
                    True,
                )
                draw_text(area, cr, fmt_tokens(item.value), x + 6, y + 26, "#000000", 8)
            self._rects.append((item, x, y, w, h))

        del palette

    def _pressed(self, _gesture, _count: int, x: float, y: float) -> None:
        if self._on_select is None:
            return
        for item, rx, ry, rw, rh in self._rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                self._on_select(item)
                return


class Sparkline(Chart):
    """Compact area chart for one series."""

    def __init__(self, width: int = 240, height: int = 44, color: str = "") -> None:
        super().__init__(width, height)
        self._values: list[float] = []
        self._color = color
        self.set_hexpand(True)

    def set_data(self, values: Sequence[float], color: str = "") -> None:
        self._values = [safe(value) for value in values]
        if color:
            self._color = color
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        del area
        palette = theme.active()
        color = self._color or palette.accent
        values = [v for v in self._values if math.isfinite(v)]
        if len(values) < 2:
            return

        low, high = min(values), max(values)
        span = max(0.01, high - low)
        points = [
            (
                index / (len(values) - 1) * width,
                height - (value - low) / span * (height - 4) - 2,
            )
            for index, value in enumerate(values)
        ]

        cr.move_to(points[0][0], height)
        for x, y in points:
            cr.line_to(x, y)
        cr.line_to(points[-1][0], height)
        cr.close_path()
        cr.set_source_rgba(*theme.rgba(color, 0.18))
        cr.fill()

        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.set_line_width(1.4)
        cr.set_source_rgba(*theme.rgba(color))
        cr.stroke()


# ---------------------------------------------------------------------------
# Treemap layout
# ---------------------------------------------------------------------------

Rect = tuple[float, float, float, float]


def _layout(
    slices: Sequence[Slice], rect: Rect, horizontal: bool
) -> list[tuple[Slice, Rect]]:
    """Alternating split: half the value goes to one side, the axis flips.

    Not a squarified treemap, but it keeps the order (largest first) and stays a
    handful of lines instead of a layout algorithm.
    """
    if not slices:
        return []
    if len(slices) == 1:
        return [(slices[0], rect)]

    total = sum(max(0.0, s.value) for s in slices)
    accumulated = 0.0
    split_at = 1
    for index, item in enumerate(slices[:-1]):
        accumulated += max(0.0, item.value)
        if accumulated >= total / 2:
            split_at = index + 1
            break

    head, tail = slices[:split_at], slices[split_at:]
    head_total = sum(max(0.0, s.value) for s in head)
    ratio = head_total / total if total > 0 else len(head) / len(slices)

    x, y, w, h = rect
    if horizontal:
        return _layout(head, (x, y, w * ratio, h), False) + _layout(
            tail, (x + w * ratio, y, w * (1 - ratio), h), False
        )
    return _layout(head, (x, y, w, h * ratio), True) + _layout(
        tail, (x, y + h * ratio, w, h * (1 - ratio)), True
    )
