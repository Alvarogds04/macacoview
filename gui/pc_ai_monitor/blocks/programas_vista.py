"""RAM per program: pure presentation rules (no GTK).

The collector (``scripts/macacoview_programs.py``) groups every observable
process into programs with an opaque stable ID and reads PSS only for the IDs
the user selected. This module turns those records into the text the settings
picker, the pill preview and Recursos show. Rules from the source spec
(``ram-program-selector``):

- PSS only with full coverage; otherwise the value is RSS and the label says
  why PSS is missing («PSS parcial», «PSS inaccesible», «PSS no muestreada»,
  «PSS: proceso cambiado»). The label describes PSS, not an incomplete RSS.
- Program values overlap the watch groups, the models and GPU/GTT: they are
  never added to them («no sumar»).
- Labels show the executable and a short ID, never the process arguments.
"""

import re
import unicodedata
from typing import Any

PROGRAM_ID = re.compile(r"program:v1:[0-9a-f]{64}")

# Why the value is RSS: the status of the PSS coverage of that program.
PSS_GAPS = {
    "partial": "PSS parcial",
    "not_sampled": "PSS no muestreada",
    "race": "PSS: proceso cambiado",
}
PSS_UNREADABLE = "PSS inaccesible"

NO_SUM = "no sumar"

# Recursos (L23): la lista vive aparte, bajo la leyenda de grupos.
RECURSOS_HEADING = f"PROGRAMAS SELECCIONADOS · {NO_SUM.upper()}"
RECURSOS_TIP = (
    "Se superponen con los grupos, los modelos y GPU/GTT: no se suman al RSS total."
)

# The switch gates the bar and Recursos together (L23).
SWITCH_TITLE = "Mostrar programas seleccionados"
SWITCH_SUBTITLE = (
    "En la barra y en Recursos · no sumar · apagarlo conserva la selección"
)


def is_program_id(value: object) -> bool:
    return isinstance(value, str) and PROGRAM_ID.fullmatch(value) is not None


def short_id(key: str) -> str:
    """First 8 hex characters: tells apart two interpreters with one name."""
    return key[-64:-56]


def metric_label(program: dict[str, Any]) -> str:
    """«PSS», or «RSS (<why PSS is missing>)»."""
    if program.get("memory_metric") == "pss":
        return "PSS"
    status = (program.get("coverage") or {}).get("status")
    return f"RSS ({PSS_GAPS.get(str(status), PSS_UNREADABLE)})"


def program_gib(program: dict[str, Any]) -> float:
    try:
        return float(program.get("memory_kb") or 0) / 1024**2
    except (TypeError, ValueError):
        return 0.0


def display_name(program: dict[str, Any]) -> str:
    """Printable name without the pill separator; never the arguments."""
    name = str(program.get("name") or "?")
    return "".join(c for c in name if c.isprintable() and c != "|") or "?"


def program_title(program: dict[str, Any], key: str) -> str:
    return f"{display_name(program)} · {short_id(key)}"


def program_subtitle(program: dict[str, Any]) -> str:
    """«/usr/bin/x · 1.20 GiB PSS · PSS 2/2 procesos» — public fields only."""
    coverage = program.get("coverage") or {}
    executable = program.get("executable") or "Ejecutable inaccesible"
    return (
        f"{executable} · {program_gib(program):.2f} GiB {metric_label(program)} · "
        f"PSS {coverage.get('measured', 0)}/{coverage.get('total', 0)} procesos"
    )


ABSENT_SUBTITLE = "No activo o no observable · selección conservada; puede desmarcarlo"


def live_programs(snapshot_programs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Programs of the snapshot by ID, dropping records with a malformed ID."""
    return {
        program["id"]: program
        for program in snapshot_programs
        if isinstance(program, dict) and is_program_id(program.get("id"))
    }


def selected_programs(
    snapshot_programs: list[dict[str, Any]], selected: tuple[str, ...]
) -> list[tuple[str, dict[str, Any] | None]]:
    """The selection in config order: (id, record) or (id, None) when absent."""
    live = live_programs(snapshot_programs)
    return [(key, live.get(key)) for key in selected if is_program_id(key)]


def program_chip(program: dict[str, Any], key: str, value: str) -> str:
    """One entry of the bar block: «editor·aaaaaaaa PSS 1.2G»."""
    return f"{display_name(program)}·{short_id(key)} {metric_label(program)} {value}"


def coverage_summary(stats: dict[str, Any] | None) -> tuple[str, str]:
    """Title and subtitle of the global coverage row of the picker."""
    stats = stats or {}
    coverage = stats.get("program_coverage") or {}
    enumeration = stats.get("program_enumeration") or {}
    title = "Procesos observables" if "programs" in stats else "Sin datos de programas"
    subtitle = (
        f"PSS legible: {coverage.get('measured', 0)}/{coverage.get('total', 0)} · "
        f"no muestreada: {coverage.get('not_sampled', 0)} · "
        f"inaccesible: {coverage.get('unreadable', 0)} · "
        f"cambiado: {coverage.get('raced', 0)} · "
        + (
            "Enumeración del snapshot completa, no de toda la RAM"
            if enumeration.get("complete")
            else "Enumeración incompleta o no disponible"
        )
    )
    return title, subtitle


# -- short Configuración + «Agregar programa…» (S19, proposal 10 B/C) --------

GROUP_TITLE = "RAM por programa"
GROUP_DESCRIPTION = (
    "Fijá programas para ver su RAM real (PSS) en la barra y en Recursos. "
    "No se suman a los grupos."
)
ADD_LABEL = "Agregar programa…"
REMOVE_LABEL = "Quitar"
PIN_LABEL = "Fijar"
PINNED_LABEL = "Fijado ✓"
ABSENT_VALUE = "ausente"
ABSENT_KIND = "No está corriendo · sigue fijado"
EMPTY_TITLE = "Todavía no fijaste ningún programa."
EMPTY_SUBTITLE = "Buscalo acá: su RAM aparece en la barra y en Recursos."
INCLUDE_ALL_TITLE = "Incluir procesos del sistema y los chicos"
INCLUDE_ALL_SUBTITLE = (
    "Ocultos ahora: los de ejecutable inaccesible (root u otros usuarios) "
    "y los que usan menos de 50 MiB"
)

TOP_DEFAULT = 15
SEARCH_CAP = 50
MIN_RSS_KB = 50 * 1024

# Rule 2 · browsers by executable path (first match wins).
BROWSER_PATHS = (
    ("/snap/firefox/", "Firefox", "navegador"),
    ("/snap/chromium/", "Chromium", "navegador (snap)"),
    ("/.cache/ms-playwright/", "Chromium (Playwright)", "navegador de pruebas"),
    ("/opt/google/chrome/", "Chrome", "navegador"),
    ("google-chrome", "Chrome", "navegador"),
)

# Rule 3 · known apps by name or executable basename. Short on purpose.
KNOWN_APPS = {
    "rustdesk": ("RustDesk", "escritorio remoto"),
    "gnome-shell": ("GNOME Shell", "escritorio de GNOME"),
    "update-manager": ("Gestor de actualizaciones", "actualizaciones de Ubuntu"),
    "xwayland": ("Xwayland", "apps X11 dentro de Wayland"),
    "gnome-software": ("Software de GNOME", "tienda de aplicaciones"),
    "nautilus": ("Archivos (Nautilus)", "gestor de archivos"),
    "code": ("VS Code", "editor de código"),
    "ghostty": ("Ghostty", "terminal"),
}

UNREADABLE_KIND = "Ejecutable inaccesible · sistema u otro usuario"
UNKNOWN_KIND = "sin nombre conocido"


def _executable(program: dict[str, Any]) -> str:
    return str(program.get("executable") or "")


def _key(program: dict[str, Any]) -> str:
    return str(program.get("id") or "")


def _known(program: dict[str, Any]) -> tuple[str, str] | None:
    """(human name, kind) from rules 2 and 3, or None."""
    executable = _executable(program)
    if executable:
        for fragment, name, kind in BROWSER_PATHS:
            if fragment in executable:
                return name, kind
    for candidate in (executable.rsplit("/", 1)[-1], display_name(program)):
        found = KNOWN_APPS.get(candidate.lower())
        if found:
            return found
    return None


def is_unreadable(program: dict[str, Any]) -> bool:
    """Rule 5: no readable executable (root or another user's process)."""
    return program.get("identity_source") == "comm" or not _executable(program)


def human_name(program: dict[str, Any]) -> str:
    """Rules 2, 3, 6: browser by path, known app, else «name · short id»."""
    known = _known(program)
    if known:
        return known[0]
    return f"{display_name(program)} · {short_id(_key(program))}"


def program_kind(program: dict[str, Any]) -> str:
    """Short description under the name."""
    known = _known(program)
    if is_unreadable(program):
        return f"{known[1]} · {UNREADABLE_KIND}" if known else UNREADABLE_KIND
    return known[1] if known else UNKNOWN_KIND


def program_tooltip(program: dict[str, Any] | None, key: str = "") -> str:
    """The full id (and the executable): only here, never in the title."""
    key = key or _key(program or {})
    executable = _executable(program or {}) or "Ejecutable inaccesible"
    lines = [f"id {key}", executable]
    if program:
        lines.append(metric_label(program))
    return "\n".join(lines)


def _size(kb: float) -> str:
    if kb >= 1024**2:
        return f"{kb / 1024**2:.2f} GiB"
    return f"{kb / 1024:.0f} MiB"


def memory_text(program: dict[str, Any] | None) -> str | None:
    """«3.42 GiB PSS» when measured, else «… RSS»; None when absent."""
    if program is None:
        return None
    try:
        kb = float(program.get("memory_kb") or 0)
    except (TypeError, ValueError):
        kb = 0.0
    metric = "PSS" if program.get("memory_metric") == "pss" else "RSS"
    return f"{_size(kb)} {metric}"


def rss_kb(program: dict[str, Any]) -> float:
    try:
        return float(program.get("rss_kb") or program.get("memory_kb") or 0)
    except (TypeError, ValueError):
        return 0.0


def rss_text(program: dict[str, Any]) -> str:
    """RAM column of the dialog: RSS, the figure every program has."""
    return _size(rss_kb(program))


def is_minor(program: dict[str, Any]) -> bool:
    """Hidden by default in the dialog: unreadable executable or < 50 MiB."""
    return is_unreadable(program) or rss_kb(program) < MIN_RSS_KB


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(program: dict[str, Any], query: str) -> bool:
    """Case- and accent-insensitive on human name, executable name and path."""
    needle = _fold(query.strip())
    if not needle:
        return True
    haystack = (human_name(program), display_name(program), _executable(program))
    return any(needle in _fold(text) for text in haystack)


def candidates(
    snapshot_programs: list[dict[str, Any]], query: str = "", include_all: bool = False
) -> tuple[list[dict[str, Any]], int]:
    """Dialog list sorted by RSS: (shown, total matches).

    Without a query, the 15 largest; with one, every match up to 50.
    """
    pool = [
        program
        for program in live_programs(snapshot_programs).values()
        if (include_all or not is_minor(program)) and matches(program, query)
    ]
    pool.sort(key=lambda program: (-rss_kb(program), _key(program)))
    limit = SEARCH_CAP if query.strip() else TOP_DEFAULT
    return pool[:limit], len(pool)


PICKER_DESCRIPTION = (
    "PSS reparte páginas compartidas; RSS puede contarlas varias veces. "
    "PSS se lee solo para la selección guardada; sin selección no se muestrea. "
    "RSS es el respaldo si PSS es parcial, inaccesible o no muestreada. No sumar con "
    "grupos, modelos ni GPU/GTT: se superponen. RAM usada = total − "
    "disponible; caché y kernel no se atribuyen por completo a programas."
)
