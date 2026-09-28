"""Configuration for the MacacoView GNOME app.

Everything machine-specific lives here: which collectors to run, where they are,
how often, and what the panel pill shows. The defaults are derived from the home
directory, so the app starts on any machine that has the collectors installed and
only then degrades per source.

This module owns the file: the settings dialog edits a :class:`Config` and calls
:func:`save`, so there is a single writer.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import shutil
import tomllib

CONFIG_DIR = Path.home() / ".config" / "macacoview"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Directorio donde vivía la config antes del renombre. Solo lo lee la
# migración: nunca se escribe acá ni se borra nada de acá.
LEGACY_CONFIG_DIR = Path.home() / ".config" / "pc-ai-monitor"
LEGACY_CONFIG_PATH = LEGACY_CONFIG_DIR / "config.toml"

TEMPLATE = """\
# MacacoView — configuración
#
# Las rutas aceptan ~. Si un colector no existe, esa sección de la app queda en
# "sin datos" y el resto sigue funcionando.

[paths]
# Colector de recursos (JSON en stdout).
stats = "~/.local/bin/macacoview-stats"
# Colector de consumo de tokens (JSON en stdout).
tokens = "~/.local/bin/macacoview-tokens"

[ports]
# Helper privilegiado que imprime la salida de `ss`. Se invoca con `sudo -n`, es
# decir sin pedir contraseña: si falta, la pestaña Puertos queda vacía en vez de
# pedir credenciales en cada refresco.
helper = "/usr/local/bin/pc-ai-ports-read"
use_sudo = true

[refresh]
stats_s = 1.0
ports_s = 2.0
tokens_s = 10.0
# Muestras de historial que guarda la app (300 = 5 minutos a 1 Hz).
history = 300

[ui]
# Tema visual: gentle, cute, sexy o ghostly.
theme = "gentle"
# Icono de la app: cualquier nombre del tema de iconos instalado.
icon = "utilities-system-monitor"

[bar]
# Qué muestra el pill del panel. Los valores en cero se ocultan por defecto.
hide_zero = true
# Mostrar estos aunque estén en cero: pi, hermes, firefox, o el alias de un modelo.
always_show = []
# Si no está vacío, mostrar SOLO estas claves (por ejemplo ["pi", "abito-gpt"]).
only = []
# Unidades de las métricas: "gb" o "percent" (porcentaje de la memoria total).
units = "gb"
# Con varios modelos: "separate" (un chip por modelo) o "combined" (una métrica sumada).
models = "separate"
# Bloque de estado del router (A/F, JEV, sesiones, RAM libre, cola) en la barra.
# Viene OCULTO: activalo acá o desde el menú de configuración cuando lo quieras.
router_indicators = false

# Grupos de procesos que arma el colector. Cada patrón de `match` es una
# expresión regular que se busca sobre el nombre del proceso (comm) y sobre su
# línea completa. Sin ninguna sección [[watch]] valen los de siempre:
# pi, hermes, firefox, system y other.
#
# [[watch]]
# name = "chrome"
# match = ['^chrome$', '/chrome/']
# icon = "🌐"
# visible = true
"""


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def migrate_legacy_config(new_path: Path, legacy_path: Path) -> bool:
    """Copy the legacy config to the new location when it is needed.

    A config that already exists at the new path is never overwritten, and
    with no legacy file there is nothing to do (no directory is invented).
    When the copy happens it is a **copy, never a move or a delete**: the
    original stays exactly where it was, and the user can remove it by hand
    once they trust the migration. Only the config file itself migrates;
    other files that live next to it (backups and such) are not ours to
    touch. Returns True when a copy was made.
    """
    if new_path.exists() or not legacy_path.is_file():
        return False
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy_path, new_path)
    except OSError:
        return False
    return True


def _migrate_default(path: Path) -> None:
    """Runs the legacy migration once, for the app's own config path.

    Callers that pass an explicit path (the tests) opt out: only the default
    location migrates.
    """
    if path == CONFIG_PATH:
        migrate_legacy_config(CONFIG_PATH, LEGACY_CONFIG_PATH)


def _as_float(raw: Any, fallback: float) -> float:
    """Config values are user input: a wrong type must not break startup."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _as_int(raw: Any, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _as_bool(raw: Any, fallback: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    return fallback


def _as_path(raw: Any, fallback: Path) -> Path:
    if isinstance(raw, str) and raw.strip():
        return _expand(raw.strip())
    return fallback


def _as_keys(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip().lower() for item in raw if str(item).strip())


@dataclass(frozen=True)
class WatchEntry:
    """An entry of ``[[watch]]``: a process group the collector builds.

    ``match`` holds regular expressions searched against the process name
    (comm) and its full command line. Entries without patterns are the
    collector's fixed rules: ``system`` catches root processes and the other
    empty-match entry catches everything no pattern claimed. ``visible``
    decides whether the group is built and shown at all.
    """

    name: str
    match: tuple[str, ...] = ()
    icon: str = ""
    visible: bool = True


# Los grupos de siempre, tal como estaban hardcodeados en el colector: los
# mismos nombres, los mismos patrones (anchrados para comm exacto) y los
# mismos íconos en la barra.
DEFAULT_WATCH: tuple[WatchEntry, ...] = (
    WatchEntry(
        name="pi", match=(r"^pi$", r"/\.pi-lens/", r"/\.pi/agent/"), icon="\U0001f967"
    ),
    WatchEntry(
        name="hermes",
        match=(r"^hermes$", r"/\.hermes/", "hermes_cli"),
        icon="\U0001fab6",
    ),
    WatchEntry(name="firefox", match=(r"^firefox$", r"/firefox/"), icon="\U0001f98a"),
    WatchEntry(name="system", icon="\u2699\ufe0f"),
    WatchEntry(name="other", icon="\U0001f4e6"),
)


def _as_watch(raw: Any, fallback: tuple[WatchEntry, ...]) -> tuple[WatchEntry, ...]:
    """Parse ``[[watch]]`` entries; anything unusable falls back as a whole.

    A section that is absent, empty or full of broken entries means "the usual
    groups"; a section with at least one valid entry replaces them entirely —
    the user chose what is watched. An entry needs a name; a ``match`` that is
    present but not a list of strings discards the entry.
    """
    if not isinstance(raw, list):
        return fallback

    entries: list[WatchEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        match_raw = item.get("match", ())
        if match_raw is None:
            match_raw = ()
        if not isinstance(match_raw, list):
            continue
        icon = item.get("icon")
        visible = item.get("visible")
        entries.append(
            WatchEntry(
                name=name.strip().lower(),
                match=tuple(p for p in match_raw if isinstance(p, str) and p.strip()),
                icon=icon if isinstance(icon, str) else "",
                visible=visible if isinstance(visible, bool) else True,
            )
        )
    return tuple(entries) or fallback


@dataclass(frozen=True)
class Config:
    stats_bin: Path = field(
        default_factory=lambda: Path.home() / ".local/bin/macacoview-stats"
    )
    tokens_bin: Path = field(
        default_factory=lambda: Path.home() / ".local/bin/macacoview-tokens"
    )
    ports_helper: Path = Path("/usr/local/bin/pc-ai-ports-read")
    ports_use_sudo: bool = True
    stats_interval_s: float = 1.0
    ports_interval_s: float = 2.0
    tokens_interval_s: float = 10.0
    history_capacity: int = 300
    theme: str = "gentle"
    icon: str = "utilities-system-monitor"
    bar_hide_zero: bool = True
    bar_always_show: tuple[str, ...] = ()
    bar_only: tuple[str, ...] = ()
    bar_units: str = "gb"
    bar_models: str = "separate"
    # Opt-in: el bloque de estado del router no se muestra salvo que se active.
    bar_router_indicators: bool = False
    # Grupos de procesos vigilados ([[watch]]): los de siempre por defecto.
    watch: tuple[WatchEntry, ...] = DEFAULT_WATCH

    def with_theme(self, key: str) -> "Config":
        return replace(self, theme=key)

    def with_icon(self, name: str) -> "Config":
        return replace(self, icon=name)


def load(path: Path = CONFIG_PATH) -> Config:
    """Read the config file, falling back to the defaults for anything missing.

    A malformed file is not fatal: the app must still open and show which sources
    are unavailable.
    """
    _migrate_default(path)
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return Config()

    defaults = Config()
    paths = raw.get("paths", {})
    ports = raw.get("ports", {})
    refresh = raw.get("refresh", {})
    look = raw.get("ui", {})
    bar = raw.get("bar", {})

    if not all(
        isinstance(section, dict) for section in (paths, ports, refresh, look, bar)
    ):
        return defaults

    theme_name = look.get("theme")
    icon_name = look.get("icon")

    return Config(
        stats_bin=_as_path(paths.get("stats"), defaults.stats_bin),
        tokens_bin=_as_path(paths.get("tokens"), defaults.tokens_bin),
        ports_helper=_as_path(ports.get("helper"), defaults.ports_helper),
        ports_use_sudo=_as_bool(ports.get("use_sudo"), defaults.ports_use_sudo),
        stats_interval_s=max(
            0.2, _as_float(refresh.get("stats_s"), defaults.stats_interval_s)
        ),
        ports_interval_s=max(
            0.2, _as_float(refresh.get("ports_s"), defaults.ports_interval_s)
        ),
        tokens_interval_s=max(
            1.0, _as_float(refresh.get("tokens_s"), defaults.tokens_interval_s)
        ),
        history_capacity=max(
            10, _as_int(refresh.get("history"), defaults.history_capacity)
        ),
        theme=theme_name
        if isinstance(theme_name, str) and theme_name
        else defaults.theme,
        icon=icon_name if isinstance(icon_name, str) and icon_name else defaults.icon,
        bar_hide_zero=_as_bool(bar.get("hide_zero"), defaults.bar_hide_zero),
        bar_always_show=_as_keys(bar.get("always_show")),
        bar_only=_as_keys(bar.get("only")),
        bar_units=units
        if isinstance(units := bar.get("units"), str) and units in ("gb", "percent")
        else defaults.bar_units,
          bar_models=models
          if isinstance(models := bar.get("models"), str)
          and models in ("separate", "combined")
          else defaults.bar_models,
          bar_router_indicators=_as_bool(
              bar.get("router_indicators"), defaults.bar_router_indicators
          ),
          watch=_as_watch(raw.get("watch"), defaults.watch),
      )


def _toml_string(value: str) -> str:
    """Basic TOML string with the two escapes that matter for our values."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save(config: Config, path: Path = CONFIG_PATH) -> bool:
    """Write the whole file from a Config. Returns True when it was written."""
    listed = lambda keys: ", ".join(f'"{key}"' for key in keys)  # noqa: E731
    watch_blocks = "\n".join(
        "[[watch]]\n"
        f"name = {_toml_string(entry.name)}\n"
        f"match = [{', '.join(_toml_string(p) for p in entry.match)}]\n"
        f"icon = {_toml_string(entry.icon)}\n"
        f"visible = {str(entry.visible).lower()}\n"
        for entry in config.watch
    )
    body = f"""\
# PC-AI Monitor — configuración
#
# Este archivo lo escribe la app (menú de configuración). Editar a mano también
# funciona: se lee al arrancar.

[paths]
stats = "{config.stats_bin}"
tokens = "{config.tokens_bin}"

[ports]
helper = "{config.ports_helper}"
use_sudo = {str(config.ports_use_sudo).lower()}

[refresh]
stats_s = {config.stats_interval_s}
ports_s = {config.ports_interval_s}
tokens_s = {config.tokens_interval_s}
history = {config.history_capacity}

[ui]
theme = "{config.theme}"
icon = "{config.icon}"

[bar]
hide_zero = {str(config.bar_hide_zero).lower()}
always_show = [{listed(config.bar_always_show)}]
only = [{listed(config.bar_only)}]
units = "{config.bar_units}"
models = "{config.bar_models}"
router_indicators = {str(config.bar_router_indicators).lower()}

{watch_blocks}"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    except OSError:
        return False
    return True


def write_template(path: Path = CONFIG_PATH) -> bool:
    """Create a commented config when none exists yet. Never touches an existing one.

    The legacy migration runs first: if an old config exists it is copied
    here instead of the template, so the user's settings survive the rename.
    """
    _migrate_default(path)
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE)
    except OSError:
        return False
    return True
