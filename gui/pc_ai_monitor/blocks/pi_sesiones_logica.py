"""Recursos → Pi sesiones: pure logic for the per-session table (S17, 08 tab 4).

Input is the collector's ``pi_sessions`` list (``scripts/macacoview_pi_sessions.py``):
one dict per pi process with its tree (the pi plus everything it launched,
subagents excluded) PSS/RSS. Output is display-ready rows: sorted by PSS,
subagents nested under the session that launched them, names unique.

PSS sums here never reach the treemap or «RSS total»: they overlap the group
RSS, so the header shows both side by side marked «no sumar».
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOOTNOTE = (
    "PSS y RSS suman el proceso pi y todo lo que lanzó; los subagentes van en "
    "su propia fila y no se suman dos veces."
)
EMPTY = "No hay sesiones de Pi ahora mismo."
TIP_PIDS = "Procesos del árbol: el pi y todo lo que lanzó (shells, MCP, LSP), sin sus subagentes."
TIP_PSS = (
    "PSS del árbol: reparte las páginas compartidas entre procesos, "
    "es la cifra que se puede sumar entre sesiones."
)
TIP_RSS = "RSS del árbol: cuenta entera cada página compartida, por eso es mayor que la PSS."
TIP_CHILDREN = "Hijos directos del proceso pi (shells, servidores MCP, subagentes); se cuentan, no se suman."
TIP_GROUP_RSS = "Lo mismo que muestra la ficha Pi del treemap."
TIP_SESSIONS_PSS = (
    "Suma de la PSS de todas las sesiones (el pi y todo lo que lanzó): reparte las "
    "páginas compartidas, es la suma honesta. Incluye shells y servidores MCP que "
    "el treemap cuenta en otros grupos, así que puede superar al RSS del grupo Pi."
)
TIP_NO_SUMAR = "Se superpone con el RSS del grupo: no se suma al RSS total."

_SOURCES = {
    "environ": "sesión leída del entorno de un proceso hijo (exacta)",
    "start_time": "sesión creada al arrancar el proceso (segura)",
    "recent_mtime": "única sesión de esa carpeta escrita desde que arrancó (probable)",
}


@dataclass(frozen=True)
class Row:
    pid: int
    depth: int
    name: str
    detail: str
    tags: tuple[tuple[str, str], ...]
    pid_count: int
    children: int
    pss_kb: int | None
    pss_text: str
    rss_kb: int
    rss_text: str
    bar: float
    tooltip: str


@dataclass(frozen=True)
class Header:
    title: str
    rss_text: str
    pss_text: str
    partial: bool


def mib(kb: Any) -> str:
    """kB → «1 608 MiB» (space as thousands separator, as in 08)."""
    if kb is None or isinstance(kb, bool):
        return "—"
    try:
        value = round(float(kb) / 1024)
    except (TypeError, ValueError):
        return "—"
    return f"{value:,} MiB".replace(",", " ")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pss(session: dict[str, Any]) -> int | None:
    value = session.get("pss_kb")
    return None if value is None else _int(value)


def _partial(session: dict[str, Any]) -> bool:
    return ((session.get("pss_coverage") or {}).get("status")) != "full"


def _short_cwd(cwd: Any, home: str) -> str:
    if not isinstance(cwd, str) or not cwd:
        return ""
    if cwd == home:
        return "~"
    base = Path(cwd).name or cwd
    if home and cwd.startswith(home.rstrip("/") + "/"):
        return f"~/…/{base}"
    return f"…/{base}"


def _unique_names(sessions: list[dict[str, Any]]) -> dict[int, str]:
    """Names as the collector made them, disambiguated when two collide."""
    names: dict[int, str] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        name = str(session.get("name") or f"pi · pid {session.get('pid')}")
        by_name.setdefault(name, []).append(session)
    for name, group in by_name.items():
        for session in group:
            pid = _int(session.get("pid"))
            if len(group) == 1:
                names[pid] = name
                continue
            session_id = session.get("session_id")
            suffix = session_id[-4:] if isinstance(session_id, str) and session_id else f"pid {pid}"
            names[pid] = f"{name} · {suffix}"
    # Same session id suffix twice (a resumed session in two processes): use PIDs.
    seen: dict[str, int] = {}
    for pid in sorted(names):
        if names[pid] in seen:
            names[pid] = f"{names[pid]} · pid {pid}"
        seen[names[pid]] = pid
    return names


def session_rows(sessions: list[dict[str, Any]] | None, home: str | None = None) -> list[Row]:
    """Rows in display order: sessions by PSS desc, each followed by its subagents."""
    sessions = [s for s in (sessions or []) if isinstance(s, dict) and "pid" in s]
    if home is None:
        home = str(Path.home())
    names = _unique_names(sessions)
    by_pid = {_int(s["pid"]): s for s in sessions}
    kids: dict[int | None, list[dict[str, Any]]] = {}
    for session in sessions:
        parent = session.get("parent_pid")
        parent = _int(parent) if parent in by_pid or _int(parent) in by_pid else None
        kids.setdefault(parent, []).append(session)
    biggest = max((_pss(s) or 0 for s in sessions), default=0)

    def order(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(group, key=lambda s: (-(_pss(s) or 0), _int(s["pid"])))

    rows: list[Row] = []
    visited: set[int] = set()

    def visit(session: dict[str, Any], depth: int) -> None:
        pid = _int(session["pid"])
        if pid in visited:
            return
        visited.add(pid)
        parent = by_pid.get(_int(session.get("parent_pid")))
        tags: list[tuple[str, str]] = []
        if parent is not None:
            tags.append((f"subagente de {names[_int(parent['pid'])]}", ""))
        if session.get("name_source") == "session_info":
            tags.append(("con nombre", "named"))
        if session.get("session_confidence") == "low":
            tags.append(("probable", "dim"))
        if _partial(session) and _pss(session) is not None:
            tags.append(("PSS parcial", "warn"))
        pss = _pss(session)
        # The PID-named fallback already shows the PID: do not repeat it.
        parts = [] if names[pid].endswith(f"pid {pid}") else [f"pid {pid}"]
        if parent is None and (where := _short_cwd(session.get("cwd"), home)):
            parts.append(where)
        detail = " · ".join(parts)
        rows.append(Row(
            pid=pid, depth=depth, name=names[pid], detail=detail, tags=tuple(tags),
            pid_count=_int(session.get("pid_count")),
            children=_int(session.get("children")),
            pss_kb=pss, pss_text=mib(pss) if pss is not None else "—",
            rss_kb=_int(session.get("rss_kb")), rss_text=mib(session.get("rss_kb")),
            bar=(pss or 0) / biggest if biggest > 0 else 0.0,
            tooltip=_tooltip(session),
        ))
        for child in order(kids.get(pid, [])):
            visit(child, depth + 1)

    for session in order(kids.get(None, [])):
        visit(session, 0)
    for session in order(sessions):  # cycles in a corrupt snapshot
        visit(session, 0)
    return rows


def _tooltip(session: dict[str, Any]) -> str:
    lines = []
    source = _SOURCES.get(str(session.get("session_source")))
    session_id = session.get("session_id")
    if session_id and source:
        lines.append(f"Sesión {session_id}: {source}.")
    else:
        lines.append("Sin sesión identificada: carpeta y PID del proceso pi.")
    lines.append(
        f"Proceso pi solo: PSS {mib(session.get('pi_pss_kb'))} · RSS {mib(session.get('pi_rss_kb'))}."
    )
    coverage = session.get("pss_coverage") or {}
    if coverage.get("status") not in (None, "full"):
        missing = _int(coverage.get("total")) - _int(coverage.get("measured"))
        lines.append(f"PSS sin leer en {missing} de {_int(coverage.get('total'))} procesos.")
    return "\n".join(lines)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def header(sessions: list[dict[str, Any]] | None, group_rss_gib: float) -> Header:
    sessions = [s for s in (sessions or []) if isinstance(s, dict)]
    pids = {_int(s.get("pid")) for s in sessions}
    subagents = sum(1 for s in sessions if _int(s.get("parent_pid")) in pids)
    title = f"Pi · {_plural(len(sessions) - subagents, 'sesión', 'sesiones')}"
    if subagents:
        title += f" · {_plural(subagents, 'subagente', 'subagentes')}"
    total = sum(_pss(s) or 0 for s in sessions)
    partial = any(_partial(s) for s in sessions)
    pss_text = f"{total / 1024 / 1024:.2f} GiB"
    try:
        rss = float(group_rss_gib)
    except (TypeError, ValueError):
        rss = 0.0
    return Header(
        title=title,
        rss_text=f"{rss:.2f} GiB",
        pss_text=f"≥ {pss_text}" if partial else pss_text,
        partial=partial,
    )
