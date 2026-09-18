"""Shared number formatting.

The same rules are used by every page, so a GiB looks the same in the memory
bars, a model card and a table cell.
"""

import math

LOCALE = "es-AR"


def safe(value: object) -> float:
    """Coerce anything into a finite float; None and NaN become 0."""
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def whole(value: object, fallback: int = 0) -> int:
    """Same idea for PIDs and counts coming from the collectors."""
    if isinstance(value, bool) or value is None:
        return fallback
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback


def gib_label(value: object, decimals: int = 1) -> str:
    return f"{gib(value, decimals)}G"


def gib(value: object, decimals: int = 1) -> str:
    return f"{safe(value):.{decimals}f}"


def tokens(value: object) -> str:
    """1234567 -> '1.2M'. Token totals are read at a glance."""
    number = safe(value)
    if number >= 1e9:
        return f"{number / 1e9:.2f}B"
    if number >= 1e6:
        return f"{number / 1e6:.1f}M"
    if number >= 1e3:
        return f"{number / 1e3:.1f}k"
    return str(round(number))


def money(value: object) -> str:
    return f"${safe(value):.2f}"


def count(value: object) -> str:
    return f"{round(safe(value)):,}".replace(",", ".")


def percent(value: object, total: object, decimals: int = 0) -> str:
    """Share of value over total as a percentage string."""
    denominator = safe(total)
    if denominator <= 0:
        return f"{0:.{decimals}f}%"
    return f"{safe(value) / denominator * 100:.{decimals}f}%"


def share(value: object, maximum: object) -> float:
    """0..1 ratio, clamped, for geometry (bar widths, arc angles)."""
    peak = safe(maximum)
    if peak <= 0:
        return 0.0
    return min(1.0, max(0.0, safe(value) / peak))


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: max(1, limit - 1)]}…"
