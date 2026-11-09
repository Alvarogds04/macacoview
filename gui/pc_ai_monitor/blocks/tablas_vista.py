"""Pure rules for the table pages (Puertos, Procesos) and the panel pill.

No GTK here: everything that decides a value — the RAM bar fraction, the style
kind of an access pill, the chips of the panel-pill preview — is a plain
function, so the design rules are testable without a display. The widgets in
``pc_ai_monitor.widgets.tabla`` and the blocks in ``puertos`` / ``procesos``
/ ``settings`` only paint what these decide.
"""

from pc_ai_monitor.blocks import programas_vista as programas
from pc_ai_monitor.config import Config
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import gib, safe
from pc_ai_monitor.puertos import ALL, IFACE, LAN, LOCAL, TAILSCALE, PortRow

# Tooltip de cada insignia de acceso (S12: la frase va en el tooltip).
ACCESS_TIPS: dict[str, str] = {
    LOCAL: "Solo 127.0.0.1 o ::1: nadie más la alcanza",
    LAN: "Dirección de la red local",
    TAILSCALE: "Dirección de la tailnet",
    ALL: "0.0.0.0 o ::: cualquiera que llegue a la máquina puede conectarse",
    IFACE: "Atada a una interfaz concreta",
}

# El pill muestra estos grupos por defecto (igual que la extensión); los
# demás (system, other, ...) sólo entran si el usuario los nombra.
PILL_GROUPS = ("pi", "hermes", "firefox")

# Umbral de «valor en cero» para hide_zero: un GiB despreciable no es dato.
ZERO_FLOOR_GIB = 0.05


def ram_fraction(rss_kb: object, max_rss_kb: object, floor: float = 0.015) -> float:
    """Fraction of the inline RAM bar, scaled to the largest row shown.

    A tiny row keeps a visible sliver (the design floor) instead of vanishing;
    without a usable maximum there is no bar at all.
    """
    peak = safe(max_rss_kb)
    if peak <= 0:
        return 0.0
    fraction = safe(rss_kb) / peak
    if 0 < fraction < floor:
        return floor
    return min(1.0, fraction)


def ram_text(rss_kb: object) -> str:
    """49200000 KiB -> «49.20G», two decimals like every other RAM value."""
    return f"{safe(rss_kb) / 1024 / 1024:.2f}G"


def access_pill_kind(access: str) -> str:
    """CSS kind of the access pill: exposed rows warn, the rest stay blue."""
    return "ex" if access == ALL else "lo"


def access_tip(access: str) -> str:
    return ACCESS_TIPS.get(access, access)


def port_tooltip(row: PortRow) -> str:
    """«22 · 0.0.0.0 o ::: … · 0.0.0.0:22» — the sentence lives in the tooltip."""
    return f"{row.port} · {access_tip(row.access)} · {row.local}"


def port_sort_key(key: str):
    """Sort key for the ports table: everything is numeric."""
    if key == "pid":
        return lambda row: row.pid
    return lambda row: row.port


def pill_chips(config: Config, snapshot: Snapshot | None) -> list[tuple[str, str]]:
    """Chips (text, tooltip) the panel pill would show right now.

    Mirrors what the GNOME extension renders, built only from the current
    config and the current snapshot: groups (pi/hermes/firefox by default,
    others only when named), models per alias or summed, and the router
    indicator block when it is on. No snapshot, no chips — never fake data.
    """
    if snapshot is None:
        return []

    only = set(config.bar_only)
    always = set(config.bar_always_show)
    total = safe(snapshot.memory().get("total_gib"))

    def unit(value: float) -> str:
        if config.bar_units == "percent":
            return f"{round(value / total * 100)}%" if total > 0 else "—%"
        return f"{gib(value)}G"

    def show(keys: tuple[str, ...], value: float) -> bool:
        if only and not only.intersection(keys):
            return False
        if config.bar_hide_zero and value < ZERO_FLOOR_GIB:
            return bool(always.intersection(keys))
        return True

    chips: list[tuple[str, str]] = []
    groups = snapshot.groups()
    hidden = set(snapshot.hidden_groups)
    for entry in config.watch:
        key = entry.name
        if key in hidden:
            continue
        if key not in PILL_GROUPS and key not in only and key not in always:
            continue
        value = safe((groups.get(key) or {}).get("rss_gib"))
        if show((key,), value):
            chips.append(
                (
                    f"{entry.icon} {unit(value)}".strip(),
                    f"{key} · {value:.2f} GiB",
                )
            )

    rows = [
        (str(model.get("alias") or "?"), safe(model.get("rss_gib")))
        for model in snapshot.models()
    ]
    visible_models = [(alias, rss) for alias, rss in rows if show((alias,), rss)]
    if config.bar_models == "combined":
        if visible_models:
            total_rss = sum(rss for _alias, rss in visible_models)
            chips.append(("🤖 " + unit(total_rss), "Modelos locales sumados"))
    else:
        chips.extend(
            (f"{_model_icon(alias)} {alias} {unit(rss)}", f"{alias} · {rss:.2f} GiB")
            for alias, rss in visible_models
        )

    if config.bar_router_indicators:
        chips.append(router_chip(snapshot))

    # Same block the collector appends to the bar: opt-in, only programs that
    # are present now, and marked «no sumar» because they overlap the rest.
    if config.bar_programs_enabled:
        present = [
            (key, program)
            for key, program in programas.selected_programs(
                snapshot.programs(), config.bar_program_ids
            )
            if program is not None
        ]
        if present:
            text = "  ".join(
                programas.program_chip(
                    program, key, unit(programas.program_gib(program))
                )
                for key, program in present
            )
            chips.append(
                (
                    f"RAM programas ({programas.NO_SUM}): {text}",
                    "Se superponen con grupos, modelos y GPU/GTT: no se suman.",
                )
            )
    return chips


def _model_icon(alias: str) -> str:
    """Same icon the Recursos page uses for a model alias."""
    from pc_ai_monitor.blocks.modelos import icon_for

    return icon_for(alias)


def router_chip(snapshot: Snapshot) -> tuple[str, str]:
    """«A● F○ · JEV● · 3 ses · 51G libre · q0» from the live /status payload."""
    router = snapshot.router()
    abito = (router.get("abito") or {}).get("healthy")
    flash = (router.get("flash") or {}).get("healthy")
    if abito is None and flash is None:
        return ("router —", "Sin datos del router ahora mismo")

    def dot(healthy: object) -> str:
        return "●" if healthy else "○"

    sessions = safe((router.get("router") or {}).get("active_sessions"))
    queue = safe((router.get("abito") or {}).get("queue"))
    free = safe(snapshot.memory().get("available_gib"))
    jev = "●" if (router.get("jev") or {}).get("key_present") else "○"
    text = (
        f"A{dot(abito)} F{dot(flash)} · JEV{jev} · "
        f"{sessions:.0f} ses · {free:.0f}G libre · q{queue:.0f}"
    )
    return (text, "Router: Abito/Flash vivos · JEV · sesiones · RAM libre · cola")
