"""Lógica pura de la página Home: el cockpit de MacacoView (diseño 07, T3).

Todo lo que decide qué mostrar vive acá, sin GTK y sin subprocesos. El widget
(``pc_ai_monitor.blocks.home``) sólo lee fuentes y pinta estas decisiones,
reutilizando las reglas ya testeadas de ``residente_logica``,
``rendimiento_logica`` y ``engine_logica``.

Reglas de datos (S11/L9): sólo Abito (:11002) y Gufo (:11070) — la entrada
«flash» del router es el motor retirado y jamás aparece; prefill/decode salen
de los deltas de ``SpeedTracker`` («sin datos recientes», nunca 0); las
reservas de RAM son las de ``residente_logica.FOOTPRINT`` (Gufo ≈ 85 GiB,
Abito ≈ 20 GiB) y la figura «en uso» se mide del snapshot (el mayor de RSS y
GTT del proceso, porque Gufo vive en GTT y Abito en RSS).
"""

import json
from dataclasses import dataclass, field

from pc_ai_monitor.blocks.engine_logica import EngineStatus
from pc_ai_monitor.blocks.rendimiento_logica import (
    Guard,
    Metrics,
    cached_percentage,
    kv_percentage,
    spec_ratio,
    speed_text,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.widgets import instrumentos_logica as logica

GUFO_PORT = 11070
ABITO_PORT = 11002
ENGINES = ("abito", "gufo")

# Reservas del motor residente (residente_logica.FOOTPRINT, decisión «los 2»).
RESERVATION_GIB = {"gufo": 85.0, "abito": 20.0}
# Contexto que sirven HOY (L9): 131072 en los dos; el 65536 del payload del
# router está desactualizado y no se usa.
ENGINE_CTX = {"gufo": 131072, "abito": 131072}
DEFAULT_TOTAL_GIB = 122.0

UNKNOWN = "desconocido"


@dataclass
class HomeState:
    """Todo lo que el cockpit sabe en un instante; lo publica el poller."""

    resident_info: dict | None = None
    manager_ok: bool = True
    switching: str | None = None
    metrics: Metrics | None = None
    served_context: int | None = None
    guard: Guard | None = None
    inventory: list[dict] | None = None
    inventory_error: str | None = None
    status: EngineStatus | None = None
    available_label: str | None = None
    updating: bool = False
    last_update_rc: int | None = None
    update_message: str = ""
    decode: float | None = None
    prefill: float | None = None
    down_engines: tuple[str, ...] = ()
    last_read: str = ""


def measured_gib(model: dict) -> float | None:
    """El mayor de RSS y GTT del proceso: Gufo vive en GTT, Abito en RSS."""
    values = [
        value
        for value in (model.get("gtt_gib"), model.get("rss_gib"))
        if isinstance(value, (int, float))
    ]
    return max(values) if values else None


def engine_model(models: list[dict] | None, port: int) -> dict | None:
    """El proceso del snapshot que escucha en ``port`` (11070/11002)."""
    if not models:
        return None
    for model in models:
        if isinstance(model, dict) and model.get("port") == port:
            return model
    return None


# -- fila 1: bloques de RAM -------------------------------------------------


def ram_blocks(snapshot: Snapshot, resident: str | None) -> tuple[logica.RamBlock, ...]:
    """Los cuatro bloques del diseño: Gufo, Otros, Abito, Libre.

    La figura «en uso» se mide del snapshot (S9); si el motor es residente y
    el snapshot todavía no lo ve, se usa la figura del residente (la reserva).
    L21.3: la reserva (rayado) se pinta SÓLO para el motor residente o
    cargando — un motor parado no reserva nada y no toma ancho en la barra,
    que muestra el uso medido + el libre. Con el residente la reserva sigue
    pintándose (S9 «los 2»: 49.2 en uso / ≈ 85 reservados).
    """
    memory = snapshot.memory()
    models = snapshot.models()
    total = _number(memory.get("total_gib"), DEFAULT_TOTAL_GIB)
    used_total = _number(memory.get("used_gib"), 0.0)

    blocks: list[logica.RamBlock] = []
    engines_gib = 0.0
    for engine, port in (("gufo", GUFO_PORT), ("abito", ABITO_PORT)):
        model = engine_model(models, port)
        is_resident = resident in (engine, "gufo-loading" if engine == "gufo" else None)
        if model is not None:
            used = measured_gib(model) or 0.0
        elif is_resident:
            used = RESERVATION_GIB[engine]
        else:
            used = 0.0
        engines_gib += used
        blocks.append(
            logica.RamBlock(
                label=engine.capitalize(),
                sublabel="en uso" if (is_resident or model is not None) else "",
                used_gib=used,
                reserved_gib=RESERVATION_GIB[engine] if is_resident else 0.0,
                kind="active" if is_resident else "idle",
            )
        )

    # La barra muestra uso medido + libre; los motores parados quedan fuera
    # (ancho 0 = sin bloque dibujado; los rects suman ≤ el ancho de la barra).
    others = max(0.0, used_total - engines_gib)
    free = max(0.0, total - used_total)
    blocks.insert(1, logica.RamBlock("Otros", "procesos", others, others, "other"))
    blocks.append(logica.RamBlock("Libre", "", free, free, "free"))
    return tuple(blocks)


def _number(value: object, fallback: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


# -- fila 1: mini tarjetas y cambio compacto --------------------------------


def mini_cards(
    info: dict | None, down_engines: tuple[str, ...] = ()
) -> tuple[dict, ...]:
    """Las dos mini tarjetas de motor (comentario ljlp6zst), Abito y Gufo.

    Reusa ``residente_logica`` (nombres, resumen, huella, hint de carga) y
    añade el contexto vivo de L9. ``down_engines`` marca caídas de la historia.
    """
    from pc_ai_monitor.blocks.residente_logica import (
        FOOTPRINT,
        LOAD_HINT,
        NAMES,
        SUMMARY,
        engine_view,
    )

    cards: list[dict] = []
    for engine in ENGINES:
        view = engine_view(info, engine, None)
        down = engine in down_engines
        if down:
            lamp, status = "down", "caído"
        elif view["active"] and info and info.get("resident") == "gufo-loading":
            lamp, status = "stale", "cargando…"
        elif view["active"]:
            lamp, status = "ok", view["status"].lstrip("● ").strip()
        else:
            lamp, status = "off", "parado"
        cards.append(
            {
                "engine": engine,
                "name": NAMES[engine],
                "model": SUMMARY[engine].split(" · ")[0],
                "status": status,
                "active": view["active"],
                "lamp": lamp,
                "ctx": ENGINE_CTX[engine],
                "footprint": FOOTPRINT[engine],
                "load_hint": LOAD_HINT[engine],
                "detail": SUMMARY[engine],
                "enabled": view["enabled"],
                "reason": view["reason"],
                "button": view["button"],
            }
        )
    return tuple(cards)


def switch_target(resident: str | None) -> str:
    """El motor destino del cambio compacto: el que no es residente."""
    return "abito" if resident == "gufo" else "gufo"


# -- fila 2: rendimiento ----------------------------------------------------


def gpu_lamp_state(device_lost_total: float | None) -> str:
    """Lámpara de GPU: sana con 0 pérdidas; down si hubo alguna; off sin dato."""
    if device_lost_total is None:
        return "off"
    return "ok" if device_lost_total == 0 else "down"


def mtp_ratio(metrics: Metrics | None) -> float | None:
    """Ratio de borradores aceptados del MTP; ``None`` sin contadores."""
    if metrics is None:
        return None
    ratio = spec_ratio(metrics.spec)
    return ratio[0] if ratio else None


def cache_pct(metrics: Metrics | None) -> float | None:
    if metrics is None:
        return None
    return cached_percentage(
        metrics.prompt_tokens_cached_total, metrics.prompt_tokens_total
    )


def kv_pct(metrics: Metrics | None) -> float | None:
    if metrics is None:
        return None
    return kv_percentage(metrics.kv_cache_usage_ratio)


def speed_label(rate: float | None) -> str:
    """«sin datos recientes» o «12.3 tok/s»: nunca un 0 inventado."""
    return speed_text(rate)


# -- fila 3: inventario y margen RAM ----------------------------------------


def inventory_row(
    model: dict, manager_ok: bool = True, action_running: bool = False
) -> dict:
    """Una fila compacta del inventario de ``local-ai-models status --json``.

    Reusa el vocabulario del panel Motores (encendido/detenido/no utilizable)
    y añade la fracción de la barra de contexto (escala 131072, L9).
    """
    from pc_ai_monitor.blocks.motores import PROBLEM_MESSAGES

    healthy = bool(model.get("healthy", False))
    problem = model.get("problem")
    reason = ""
    if problem:
        state, lamp = "no utilizable", "down"
        reason = PROBLEM_MESSAGES.get(str(problem), f"problema: {problem}")
    elif action_running:
        state, lamp = "cargando…", "stale"
    elif healthy:
        state, lamp = "encendido", "ok"
    elif not manager_ok:
        state, lamp = "estado no disponible", "off"
    else:
        state, lamp = "detenido", "off"

    ctx = _optional(model.get("ctx"))
    fraction = min(1.0, max(0.0, ctx / ENGINE_CTX["gufo"])) if ctx is not None else 0.0

    blocked = (not manager_ok) or bool(problem) or (not healthy and bool(reason))
    return {
        "name": str(model.get("model", "")),
        "port": model.get("port"),
        "ctx": ctx,
        "fraction": fraction,
        "healthy": healthy,
        "lamp": "off" if not manager_ok else lamp,
        "state": state if manager_ok else "estado no disponible",
        "reason": reason,
        "action": "stop" if healthy else "ensure",
        "blocked": blocked,
    }


@dataclass(frozen=True)
class HeadroomRow:
    """Una barra de margen: valor actual, mínimo observado y escala total."""

    label: str
    value_gib: float | None
    min_gib: float | None
    total_gib: float

    @property
    def low(self) -> bool:
        return (
            self.min_gib is not None
            and self.value_gib is not None
            and self.value_gib < self.min_gib
        )


def headroom_rows(memory: dict, guard: Guard | None) -> tuple[HeadroomRow, HeadroomRow]:
    """«disponible» y «conservadora» con sus mínimos del guard (S11)."""
    total = _number(memory.get("total_gib"), DEFAULT_TOTAL_GIB)
    available = memory.get("available_gib")
    disponible = HeadroomRow(
        label="disponible",
        value_gib=_optional(available),
        min_gib=None if guard is None else guard.min_mem_available_gib,
        total_gib=total,
    )
    conservative = None if guard is None else guard.conservative_gib
    conservadora = HeadroomRow(
        label="conservadora",
        value_gib=conservative,
        min_gib=None if guard is None else guard.min_conservative_gib,
        total_gib=total,
    )
    return (disponible, conservadora)


def mem_head(memory: dict) -> str:
    """Cabecera corta del margen: «51.4 GiB disponibles»."""
    value = _optional(memory.get("available_gib"))
    return f"{value:.1f} GiB disponibles" if value is not None else UNKNOWN


def _optional(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# -- tarjeta de actualización (S5/S6): un estado, un botón ------------------


def parse_available_version(stdout: str) -> str | None:
    """Etiqueta humana de la versión disponible, del propio ``engine status``.

    ``None`` = la salida no es JSON; ``""`` = no hay etiqueta humana (el tag
    existe pero no sale en pantalla: sólo tooltip).
    """
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    available = payload.get("available")
    if not isinstance(available, dict):
        return ""
    for key in ("version_label", "version", "latest"):
        value = available.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


@dataclass(frozen=True)
class UpdateCard:
    """Estado único de la tarjeta de actualización de Home (S6)."""

    pill: str
    pill_class: str  # ok / warn / acc / ""
    pill_tooltip: str
    rail_status: str  # al_dia / nueva / actualizando / revertida / sin_consulta
    from_label: str
    to_label: str
    rail_tooltip: str
    button_label: str
    button_enabled: bool
    button_tooltip: str
    # Datos para tooltips: los tags/revisions jamás salen en pantalla (S5).
    tags: tuple[str, ...] = field(default_factory=tuple)


def update_card(
    status: EngineStatus | None,
    available_label: str | None,
    updating: bool = False,
    last_update_rc: int | None = None,
) -> UpdateCard:
    """Mapea el estado del motor a los cinco estados de S6.

    Precedencia: sin consulta → actualizando → revertida (rc 1) → nueva →
    al día. Los tags/revisions van sólo a los tooltips.
    """
    if status is None or status.error:
        error = status.error if status is not None else "sin lectura del administrador"
        return UpdateCard(
            pill="No se pudo consultar",
            pill_class="",
            pill_tooltip=error,
            rail_status="sin_consulta",
            from_label="—",
            to_label="—",
            rail_tooltip=error,
            button_label="Actualizar motor",
            button_enabled=False,
            button_tooltip="No se pudo leer el estado del motor.",
        )

    version = status.version_label or "—"
    available = available_label or "—"
    tags = tuple(
        tag for tag in (status.tag, status.available_tag, status.revision_label) if tag
    )

    if updating:
        return UpdateCard(
            pill="Actualizando…",
            pill_class="acc",
            pill_tooltip="la actualización del motor está en curso",
            rail_status="actualizando",
            from_label=version,
            to_label=available,
            rail_tooltip=f"corre {version} → {available} · imágenes {status.tag} → {status.available_tag}",
            button_label="Actualizando…",
            button_enabled=False,
            button_tooltip="La actualización ya está en curso.",
            tags=tags,
        )

    if last_update_rc == 1:
        return UpdateCard(
            pill="Revertida",
            pill_class="warn",
            pill_tooltip=f"{available} falló la verificación y se volvió a {version}",
            rail_status="revertida",
            from_label=version,
            to_label=available,
            rail_tooltip=f"{available} rechazada · vuelve a {version} · imágenes {status.available_tag} → {status.tag}",
            button_label="Reintentar",
            button_enabled=True,
            button_tooltip="Pide confirmación: reinicia el motor (~85 s) y corta los pedidos en curso.",
            tags=tags,
        )

    if status.updatable:
        target = available_label or "la nueva versión"
        return UpdateCard(
            pill="1 actualización",
            pill_class="acc",
            pill_tooltip=f"corre {version} · disponible {available} · imágenes {status.tag} → {status.available_tag}",
            rail_status="nueva",
            from_label=version,
            to_label=available,
            rail_tooltip=f"corre {version} · disponible {available} · imágenes {status.tag} → {status.available_tag}",
            button_label=f"Actualizar a {target}",
            button_enabled=True,
            button_tooltip="Pide confirmación: reinicia el motor (~85 s), corta los pedidos en curso y bloquea el cambio de motor mientras dura.",
            tags=tags,
        )

    return UpdateCard(
        pill="Todo al día",
        pill_class="ok",
        pill_tooltip=f"corre {version} · sin versiones más nuevas · imagen {status.tag}",
        rail_status="al_dia",
        from_label=version,
        to_label=available,
        rail_tooltip=f"corre {version} · sin versión más nueva · imagen {status.tag}",
        button_label="Al día",
        button_enabled=False,
        button_tooltip=status.reason or "No hay actualizaciones disponibles.",
        tags=tags,
    )
