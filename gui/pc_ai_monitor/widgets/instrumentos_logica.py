"""Pure geometry for the MacacoView instruments.

Everything here is display-free: the GTK widgets in ``instrumentos`` call these
functions from their draw callbacks and hit tests, and the tests exercise the
contract without opening a window. Colours are named by palette key (``accent``,
``warning``, …) or given as ``#rrggbb``; only the widget resolves them through
``pc_ai_monitor.theme``.
"""

import math
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# RAM blocks
# ---------------------------------------------------------------------------

RAM_KINDS = ("active", "idle", "other", "free")


@dataclass(frozen=True)
class RamBlock:
    """One segment of the RAM bar: used GiB plus the manager reservation.

    ``reserved_gib >= used_gib``; the share beyond ``used`` is drawn hatched
    (S9: Gufo «49.2 GiB en uso» / «≈ 85 GiB reservados»).
    """

    label: str
    sublabel: str
    used_gib: float
    reserved_gib: float
    kind: str


@dataclass(frozen=True)
class BlockRect:
    block: RamBlock
    x: float
    width: float
    # Hatched reservation share of this block (0 when reserved <= used).
    reserved_width: float


def _gib(block: RamBlock) -> float:
    reserved = max(0.0, block.reserved_gib)
    return max(reserved, max(0.0, block.used_gib))


def ram_layout(
    blocks: tuple[RamBlock, ...] | list[RamBlock], bar_width: float, gap: float = 3.0
) -> list[BlockRect]:
    """Width-proportional partition of the bar.

    The usable width (bar minus the gaps between blocks) is split by GiB, so
    the block widths plus the gaps always add up to ``bar_width``.
    """
    total = sum(_gib(block) for block in blocks)
    if total <= 0 or not blocks:
        return []
    usable = max(0.0, bar_width - gap * (len(blocks) - 1))
    rects: list[BlockRect] = []
    x = 0.0
    for block in blocks:
        width = _gib(block) / total * usable
        reserved = max(0.0, block.reserved_gib)
        overlay = max(0.0, reserved - max(0.0, block.used_gib)) / total * usable
        rects.append(BlockRect(block, x, width, min(overlay, width)))
        x += width + gap
    return rects


def ram_tooltip(block: RamBlock) -> str:
    """«Gufo · en uso · 49.2 GiB en uso · ≈ 85 GiB reservados»."""
    used = block.used_gib
    text = f"{block.label} · {used:.1f} GiB en uso"
    if block.sublabel:
        text = f"{block.label} · {block.sublabel} · {used:.1f} GiB en uso"
    if block.reserved_gib > block.used_gib:
        text += f" · ≈ {round(block.reserved_gib)} GiB reservados"
    return text


# ---------------------------------------------------------------------------
# Motor band
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BandSegment:
    """Engine state from ``start_s`` to ``end_s`` (seconds on the band clock)."""

    start_s: float
    end_s: float
    kind: str  # gufo / abito / gap / restart / down


@dataclass(frozen=True)
class BandMarker:
    t: float
    kind: str  # switch / crash / update / rollback
    tooltip: str


def band_window(now_s: float, window_s: float = 900.0) -> tuple[float, float]:
    """The visible time range: ``window_s`` ending at «ahora»."""
    return (now_s - window_s, now_s)


def band_x(t: float, tmin: float, tmax: float, plot_width: float) -> float:
    if tmax <= tmin:
        return 0.0
    return (t - tmin) / (tmax - tmin) * plot_width


def clip_segment(
    segment: BandSegment, tmin: float, tmax: float
) -> tuple[float, float] | None:
    """Intersect with the window; ``None`` when nothing is visible."""
    start = max(segment.start_s, tmin)
    end = min(segment.end_s, tmax)
    if end <= start:
        return None
    return (start, end)


def marker_hit(
    markers: tuple[BandMarker, ...] | list[BandMarker],
    x: float,
    tmin: float,
    tmax: float,
    plot_width: float,
    tolerance: float = 8.0,
) -> BandMarker | None:
    """Nearest marker whose drawn position sits within ``tolerance`` px of x."""
    best: tuple[float, BandMarker] | None = None
    for marker in markers:
        distance = abs(band_x(marker.t, tmin, tmax, plot_width) - x)
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, marker)
    return best[1] if best else None


def band_ticks(tmin: float, tmax: float, step_s: float = 180.0) -> list[float]:
    """Grid times strictly inside the window, aligned to ``step_s``."""
    if tmax <= tmin or step_s <= 0:
        return []
    first = (math.floor(tmin / step_s) + 1) * step_s
    return [t for t in _frange(first, tmax, step_s) if tmin < t <= tmax]


def _frange(start: float, stop: float, step: float) -> list[float]:
    values: list[float] = []
    t = start
    while t <= stop + 1e-9:
        values.append(t)
        t += step
    return values


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lane:
    """One area lane: samples on the shared clock, ``None`` marks a gap.

    ``stale_tail`` draws the end dashed from the last good sample to «ahora».
    """

    label: str
    color: str
    points: tuple[tuple[float, float | None], ...]
    low: float
    high: float
    stale_tail: bool = False
    unit: str = "tok/s"


def lane_y(value: float, low: float, high: float, top: float, bottom: float) -> float:
    """Value → y, with ``high`` at the top and ``low`` at the bottom."""
    span = high - low
    if span <= 0:
        return bottom
    return bottom - (value - low) / span * (bottom - top)


def lane_x(t: float, tmin: float, tmax: float, plot_x: float, plot_w: float) -> float:
    if tmax <= tmin:
        return plot_x
    return plot_x + (t - tmin) / (tmax - tmin) * plot_w


def lane_runs(
    points: tuple[tuple[float, float | None], ...],
    tmin: float,
    tmax: float,
    plot_x: float,
    plot_w: float,
    top: float,
    bottom: float,
    low: float,
    high: float,
) -> list[list[tuple[float, float]]]:
    """Contiguous non-None samples as separate polylines; gaps split runs."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for t, value in points:
        if value is None:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(
            (
                lane_x(t, tmin, tmax, plot_x, plot_w),
                lane_y(value, low, high, top, bottom),
            )
        )
    if current:
        runs.append(current)
    return runs


def lane_stale_tail(
    points: tuple[tuple[float, float | None], ...],
    tmin: float,
    tmax: float,
    plot_x: float,
    plot_w: float,
    top: float,
    bottom: float,
    low: float,
    high: float,
) -> tuple[float, float, float, float] | None:
    """Dashed end segment when the tail of the lane has no data.

    Returns ``(x0, y0, x1, y1)`` from the last good sample to the window end at
    the same height, or ``None`` when the lane ends on a real value.
    """
    last_good: tuple[float, float] | None = None
    for t, value in points:
        if value is not None:
            last_good = (
                lane_x(t, tmin, tmax, plot_x, plot_w),
                lane_y(value, low, high, top, bottom),
            )
    if last_good is None:
        return None
    if points and points[-1][1] is not None:
        return None
    return (last_good[0], last_good[1], plot_x + plot_w, last_good[1])


def lane_value_at(
    points: tuple[tuple[float, float | None], ...], t: float
) -> float | None:
    """Nearest sample value at or before ``t``; ``None`` in a gap or off data."""
    best: float | None = None
    best_t: float | None = None
    for point_t, value in points:
        if point_t <= t and value is not None and (best_t is None or point_t >= best_t):
            best_t = point_t
            best = value
    return best


def lane_current(
    points: tuple[tuple[float, float | None], ...], cursor: float | None = None
) -> float | None:
    """The value the lane shows at its right end.

    With an active cursor, the value at the cursor; without it, the last
    reading. ``None`` = «sin datos» (never a 0, S11).
    """
    if cursor is not None:
        return lane_value_at(points, cursor)
    last: float | None = None
    for _t, value in points:
        if value is not None:
            last = value
    return last


def lane_value_text(value: float | None) -> str:
    """Big number for the right end of a lane: «47.8», «1 046», «sin datos"."""
    if value is None:
        return "sin datos"
    if abs(value) >= 100:
        return f"{round(value):,}".replace(",", "\u202f")
    return f"{value:.1f}"


def wall_clock_hm(epoch_s: float) -> str:
    """Wall-clock «HH:MM» of a unix timestamp (lane axis labels)."""
    stamp = time.localtime(epoch_s)
    return f"{stamp.tm_hour:02d}:{stamp.tm_min:02d}"


def tick_label_visible(x: float, width: float, reserve_px: float = 56.0) -> bool:
    """A tick label fits left of the «ahora» corner."""
    return 0.0 <= x <= width - reserve_px


def clamp_cursor(t: float | None, tmin: float, tmax: float) -> float | None:
    if t is None:
        return None
    return min(tmax, max(tmin, t))


def cursor_step(
    current: float | None,
    key: str,
    tmin: float,
    tmax: float,
    step: float = 10.0,
    big_step: float = 60.0,
) -> float | None:
    """Keyboard move for the shared cursor. ``Escape`` clears it.

    With no cursor yet, Left/Right start from «ahora». The caller chooses the
    step size (pass a bigger one for Shift).
    """
    if key == "Left":
        return clamp_cursor(
            (current if current is not None else tmax) - step, tmin, tmax
        )
    if key == "Right":
        return clamp_cursor(
            (current if current is not None else tmax) + step, tmin, tmax
        )
    if key == "Home":
        return tmin
    if key == "End":
        return tmax
    if key == "Escape":
        return None
    return current


# ---------------------------------------------------------------------------
# Ring and dial
# ---------------------------------------------------------------------------

DIAL_START = 150.0
DIAL_SWEEP = 240.0


def ring_sweep(pct: float) -> float:
    """0–100 % → 0–360°, clamped."""
    value = min(100.0, max(0.0, pct))
    return value / 100.0 * 360.0


def ring_state(pct: float | None) -> tuple[float, str, bool]:
    """``(sweep_deg, centre_text, no_data)``.

    The centre carries the VALUE, never the label: «91 %» with data,
    «sin datos» without (the label lives under the ring).
    """
    if pct is None:
        return (0.0, "sin datos", True)
    return (ring_sweep(pct), f"{round(pct)} %", False)


def dial_angle(value: float) -> float:
    """0–1 → the 240° gauge angle, clamped (150° = empty, 390° = full)."""
    return DIAL_START + DIAL_SWEEP * min(1.0, max(0.0, value))


def dial_state(value: float | None) -> tuple[float, bool]:
    if value is None:
        return (DIAL_START, True)
    return (dial_angle(value), False)


def dial_needle(
    cx: float, cy: float, radius: float, angle_deg: float
) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    return (cx + radius * math.cos(radians), cy + radius * math.sin(radians))


# ---------------------------------------------------------------------------
# Lamp
# ---------------------------------------------------------------------------

# state → (shape, palette key): ok = circle green, stale = diamond amber,
# down = square red, off (or unknown) = hollow circle muted.
LAMP_STATES = {
    "ok": ("circle", "green"),
    "stale": ("diamond", "warning"),
    "down": ("square", "error"),
    "off": ("hollow", "muted"),
}


def lamp_state(state: str) -> tuple[str, str]:
    return LAMP_STATES.get(state, ("hollow", "muted"))


# ---------------------------------------------------------------------------
# Version rail + stepper
# ---------------------------------------------------------------------------

RAIL_STATUS = ("al_dia", "nueva", "actualizando", "revertida", "sin_consulta")
STEP_STATES = ("done", "current", "todo", "failed")
RAIL_STEPS = ("pull", "restart", "verify", "done")

# Status → (running dot ratio, done, reverted, muted, progress bar)
RAIL_VIEWS = {
    "al_dia": (1.0, True, False, False, False),
    "nueva": (0.0, False, False, False, False),
    "actualizando": (None, False, False, False, True),  # ratio = progress
    "revertida": (1.0, False, True, False, False),
    "sin_consulta": (0.0, False, False, True, False),
}


@dataclass(frozen=True)
class RailView:
    ratio: float
    done: bool
    reverted: bool
    muted: bool
    running: bool  # progress bar visible (actualizando)


@dataclass(frozen=True)
class RailStep:
    name: str
    state: str


# Token → palabra humana sobre el riel. Hoy ninguno (la píldora de la
# tarjeta ya dice el estado y el riel no repite texto, S12); si mañana un
# estado necesita palabra, se agrega acá.
RAIL_STATUS_WORDS: dict[str, str] = {}


def rail_status_text(status: str) -> str:
    """Palabra humana sobre el riel para ``status`` — nunca el token.

    L21.2: la píldora de la tarjeta ya dice el estado («Todo al día»,
    «1 actualización», …); el riel no repite texto (S12) y hoy devuelve la
    cadena vacía para todos los estados conocidos.
    """
    return RAIL_STATUS_WORDS.get(status, "")


def rail_geometry(status: str, progress: float) -> RailView:
    """Visual state of the rail for one update status."""
    if status not in RAIL_VIEWS:
        raise ValueError(f"estado de rail desconocido: {status}")
    ratio, done, reverted, muted, running = RAIL_VIEWS[status]
    if ratio is None:  # actualizando: the dot travels with the progress
        ratio = min(1.0, max(0.0, progress))
    return RailView(ratio, done, reverted, muted, running)


def normalize_steps(
    steps: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> list[RailStep]:
    """Validate step names and states; raises ``ValueError`` on either."""
    for name, state in steps:
        if name not in RAIL_STEPS:
            raise ValueError(f"paso desconocido: {name}")
        if state not in STEP_STATES:
            raise ValueError(f"estado de paso desconocido: {state}")
    return [RailStep(name, state) for name, state in steps]


def connector_state(steps: list[RailStep], index: int) -> str:
    """Colour key of the connector leaving step ``index``.

    Green after a done step, warning after a failed one, muted otherwise.
    Out-of-range indices are muted (no connector drawn).
    """
    if not 0 <= index < len(steps):
        return "muted"
    if steps[index].state == "done":
        return "ok"
    if steps[index].state == "failed":
        return "warning"
    return "muted"
