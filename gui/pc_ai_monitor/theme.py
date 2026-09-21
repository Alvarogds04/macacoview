"""Visual identity: gentle-ai themes with a Ghostty-inspired terminal look.
Two sources feed this module:

* the three gentle-ai themes (``Gentle``, ``Gentleman-Cute``, ``Gentleman-Sexy``)
  for the palette and the semantic colours;
* the user's Ghostty theme (``Liquid Carbon Transparent``) for the terminal
  variant: near-black background, desaturated cyan foreground, ANSI accents.

The palettes are embedded on purpose instead of read from the gentle-pi package
at runtime: the app must keep working on a machine where that package is not
installed. ``load_external`` refreshes them from the package when it is present.

GTK is imported inside :func:`install` so the palettes, the CSS text and
``rgba`` stay importable — and testable — without a display.
"""

# PyGObject has no type stubs of its own; gui/typings/gi provides permissive ones
# and this directive keeps the checker from flagging every gi symbol.
# pyright: reportAttributeAccessIssue=false

import json
from dataclasses import dataclass, replace
from pathlib import Path

GENTLE_PI_THEMES = Path.home() / ".pi/agent/npm/node_modules/gentle-pi/themes"


@dataclass(frozen=True)
class Palette:
    key: str
    label: str
    background: str
    element: str
    subtle: str
    border: str
    text: str
    muted: str
    accent: str
    blue: str
    green: str
    warning: str
    error: str
    selection: str
    series: tuple[str, ...]

    def chart_series(self, index: int) -> str:
        return self.series[index % len(self.series)]


THEMES: dict[str, Palette] = {
    "gentle": Palette(
        key="gentle",
        label="Gentle",
        background="#06080f",
        element="#0b0d15",
        subtle="#0d0f14",
        border="#313342",
        text="#F3F6F9",
        muted="#5C6170",
        accent="#E0C15A",
        blue="#7FB4CA",
        green="#B7CC85",
        warning="#DEBA87",
        error="#CB7C94",
        selection="#232A40",
        series=(
            "#8FB8DD",
            "#B99BF2",
            "#E0C15A",
            "#A4DAA7",
            "#C99AD6",
            "#7FB4CA",
            "#A3B5D6",
            "#8394A3",
        ),
    ),
    "cute": Palette(
        key="cute",
        label="Gentleman Cute",
        background="#060407",
        element="#100A0F",
        subtle="#0d070c",
        border="#563040",
        text="#F6EFF3",
        muted="#A78E9B",
        accent="#F095C8",
        blue="#A9C7EE",
        green="#A4DAA7",
        warning="#F2B86D",
        error="#FF718F",
        selection="#28121E",
        series=(
            "#F095C8",
            "#D2CBD0",
            "#E0C27A",
            "#D7A0B8",
            "#FFB1DD",
            "#C96AA2",
            "#A9C7EE",
            "#76616B",
        ),
    ),
    "sexy": Palette(
        key="sexy",
        label="Gentleman Sexy",
        background="#060407",
        element="#100A0F",
        subtle="#0d070c",
        border="#563040",
        text="#F6EFF3",
        muted="#A78E9B",
        accent="#F43888",
        blue="#A9C7EE",
        green="#A4DAA7",
        warning="#F2B86D",
        error="#FF718F",
        selection="#28121E",
        series=(
            "#F43888",
            "#C49BFF",
            "#E0C27A",
            "#A9C7EE",
            "#FF4F9A",
            "#D2CBD0",
            "#D7A0B8",
            "#76616B",
        ),
    ),
    # Ghostty — Liquid Carbon Transparent, the terminal the user actually runs.
    # Real window transparency is not offered because GTK4 has no backdrop blur:
    # a translucent window would show the desktop through it unblurred.
    "ghostly": Palette(
        key="ghostly",
        label="Ghostly (Ghostty)",
        background="#000000",
        element="#070707",
        subtle="#0b0b0b",
        border="#242424",
        text="#afc2c2",
        muted="#5f7373",
        accent="#7ac4cc",
        blue="#0099cc",
        green="#559a70",
        warning="#ccac00",
        error="#ff3030",
        selection="#0f2f38",
        series=(
            "#7ac4cc",
            "#cc69c8",
            "#ccac00",
            "#559a70",
            "#0099cc",
            "#bccccc",
            "#ff3030",
            "#404040",
        ),
    ),
}

DEFAULT_THEME = "gentle"

_active = THEMES[DEFAULT_THEME]


def active() -> Palette:
    """Palette currently applied. Widgets read their colours from here."""
    return _active


def get(key: str) -> Palette:
    return THEMES.get(key, THEMES[DEFAULT_THEME])


def names() -> list[str]:
    return list(THEMES)


# ---------------------------------------------------------------------------
# gentle-ai theme files (optional refresh)
# ---------------------------------------------------------------------------


def _read_vars(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    variables = document.get("vars", {})
    return variables if isinstance(variables, dict) else {}


def _pick(variables: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        value = variables.get(candidate)
        if isinstance(value, str) and value.startswith("#"):
            return value
    return None


# gentle-ai's own variable names -> the fields this app uses. A theme file that
# stops naming one of them simply keeps the embedded value.
FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "background": ("bg",),
    "element": ("bgPanel", "bgElement"),
    "subtle": ("bgSubtle",),
    "border": ("border",),
    "text": ("text",),
    "muted": ("muted", "dim"),
    "accent": ("accent",),
    "blue": ("blue", "syntaxType"),
    "green": ("green",),
    "warning": ("warning",),
    "error": ("red", "error"),
    "selection": ("selection",),
}


def load_external(directory: Path = GENTLE_PI_THEMES) -> int:
    """Refresh the embedded palettes from the gentle-pi theme files.

    Returns how many themes were refreshed. Missing files are not an error: the
    embedded copies are the fallback that keeps the app self-contained.
    """
    mapping = {
        "Gentle.json": "gentle",
        "Gentleman-Cute.json": "cute",
        "Gentleman-Sexy.json": "sexy",
    }

    refreshed = 0
    for filename, key in mapping.items():
        variables = _read_vars(directory / filename)
        if not variables:
            continue

        updates = {
            field: value
            for field, candidates in FIELD_SOURCES.items()
            if (value := _pick(variables, *candidates)) is not None
        }
        if not updates:
            continue

        THEMES[key] = replace(THEMES[key], **updates)
        refreshed += 1

    return refreshed


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

# Terminal-inspired layout: compact cards, hairline borders, monospace numbers
# and small uppercase labels. Every colour comes from the @define-color block
# built below, so switching theme is a one-provider swap.
BASE_CSS = """
window, .background {
  background-color: @theme_bg;
  color: @theme_text;
}

headerbar {
  background-color: @theme_bg;
  border-bottom: 1px solid @theme_border;
  box-shadow: none;
}

.card {
  background-color: @theme_element;
  border: 1px solid @theme_border;
  border-radius: 8px;
  padding: 10px 12px;
}

.card-title {
  color: @theme_muted;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.mono, .value, .num {
  font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace;
}

.muted {
  color: @theme_muted;
  font-size: 11px;
}

.accent {
  color: @theme_accent;
}

.kpi {
  background-color: @theme_element;
  border: 1px solid @theme_border;
  border-radius: 8px;
  padding: 8px 12px;
}

.kpi-value {
  font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace;
  font-size: 22px;
  font-weight: 700;
  color: @theme_text;
}

.kpi-label, .kpi-title {
  color: @theme_muted;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.chip {
  background-color: @theme_subtle;
  border: 1px solid @theme_border;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
  color: @theme_text;
}

.chip-dim {
  color: @theme_muted;
}

.chip-warn {
  border-color: @theme_warning;
  color: @theme_warning;
}

.legend-row, .row-button {
  border-radius: 6px;
}

.legend-row:hover, .row-button:hover {
  background-color: @theme_selection;
}

.model-name {
  font-weight: 600;
  color: @theme_text;
}

.section-head {
  color: @theme_muted;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

separator {
  background-color: @theme_border;
}

scrollbar slider {
  background-color: @theme_border;
  border-radius: 6px;
}

listview, gridview {
  background-color: transparent;
}

/* Sidebar: filas densas, es un índice y no debe competir con el contenido. */
.navigation-sidebar > row {
  min-height: 22px;
  padding: 0;
}

.navigation-sidebar > row:selected {
  background-color: @theme_selection;
}

.sidebar-label {
  font-size: 12px;
}

/* Router: valores alineados y semaforo de estado. */
.mono {
  font-family: monospace;
  font-size: 12px;
}

.tier-ok {
  color: @theme_green;
}

.tier-warn {
  color: @theme_warning;
}

.tier-crit {
  color: @theme_error;
}
"""


def colors_css(palette: Palette) -> str:
    return f"""
@define-color theme_bg {palette.background};
@define-color theme_element {palette.element};
@define-color theme_subtle {palette.subtle};
@define-color theme_border {palette.border};
@define-color theme_text {palette.text};
@define-color theme_muted {palette.muted};
@define-color theme_accent {palette.accent};
@define-color theme_blue {palette.blue};
@define-color theme_green {palette.green};
@define-color theme_warning {palette.warning};
@define-color theme_error {palette.error};
@define-color theme_selection {palette.selection};
@define-color accent_color {palette.accent};
@define-color accent_bg_color {palette.accent};
@define-color accent_fg_color {palette.background};
"""


_rules_provider = None
_colors_provider = None


def install(display, palette: Palette) -> None:
    """Apply the static rules once and the palette colours on every switch."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    global _rules_provider, _colors_provider, _active

    if _rules_provider is None:
        rules = Gtk.CssProvider()
        rules.load_from_data(BASE_CSS, -1)
        Gtk.StyleContext.add_provider_for_display(
            display, rules, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        _rules_provider = rules

    if _colors_provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _colors_provider)

    colors = Gtk.CssProvider()
    colors.load_from_data(colors_css(palette), -1)
    Gtk.StyleContext.add_provider_for_display(
        display, colors, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
    _colors_provider = colors

    _active = palette


def rgba(color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """'#rrggbb' -> Cairo-ready tuple. Bad input falls back to mid grey."""
    text = color.strip().lstrip("#")
    if len(text) != 6:
        return (0.5, 0.5, 0.5, alpha)
    try:
        red = int(text[0:2], 16) / 255
        green = int(text[2:4], 16) / 255
        blue = int(text[4:6], 16) / 255
    except ValueError:
        return (0.5, 0.5, 0.5, alpha)
    return (red, green, blue, alpha)
