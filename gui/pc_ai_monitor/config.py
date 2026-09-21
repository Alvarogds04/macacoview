"""Configuration for the PC-AI Monitor GNOME app.

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

import tomllib

CONFIG_DIR = Path.home() / ".config" / "pc-ai-monitor"
CONFIG_PATH = CONFIG_DIR / "config.toml"

TEMPLATE = """\
# PC-AI Monitor — configuración
#
# Las rutas aceptan ~. Si un colector no existe, esa sección de la app queda en
# "sin datos" y el resto sigue funcionando.

[paths]
# Colector de recursos (JSON en stdout).
stats = "~/.local/bin/pc-ai-stats"
# Colector de consumo de tokens (JSON en stdout).
tokens = "~/.local/bin/pc-ai-tokens"

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
"""


def _expand(value: str) -> Path:
    return Path(value).expanduser()


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
class Config:
    stats_bin: Path = field(
        default_factory=lambda: Path.home() / ".local/bin/pc-ai-stats"
    )
    tokens_bin: Path = field(
        default_factory=lambda: Path.home() / ".local/bin/pc-ai-tokens"
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

    def with_theme(self, key: str) -> "Config":
        return replace(self, theme=key)

    def with_icon(self, name: str) -> "Config":
        return replace(self, icon=name)


def load(path: Path = CONFIG_PATH) -> Config:
    """Read the config file, falling back to the defaults for anything missing.

    A malformed file is not fatal: the app must still open and show which sources
    are unavailable.
    """
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
      )


def save(config: Config, path: Path = CONFIG_PATH) -> bool:
    """Write the whole file from a Config. Returns True when it was written."""
    listed = lambda keys: ", ".join(f'"{key}"' for key in keys)  # noqa: E731
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
"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    except OSError:
        return False
    return True


def write_template(path: Path = CONFIG_PATH) -> bool:
    """Create a commented config when none exists yet. Never touches an existing one."""
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE)
    except OSError:
        return False
    return True
