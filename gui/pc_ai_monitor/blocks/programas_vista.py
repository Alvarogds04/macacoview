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


PICKER_DESCRIPTION = (
    "PSS reparte páginas compartidas; RSS puede contarlas varias veces. "
    "PSS se lee solo para la selección guardada; sin selección no se muestrea. "
    "RSS es el respaldo si PSS es parcial, inaccesible o no muestreada. No sumar con "
    "grupos, modelos ni GPU/GTT: se superponen. RAM usada = total − "
    "disponible; caché y kernel no se atribuyen por completo a programas."
)
