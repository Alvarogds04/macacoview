"""Lógica pura de la página Router reconstruida (diseño 07, T5, S8/S11/S12).

Todo lo que decide qué mostrar vive acá, sin GTK y sin red, para poder
testearlo contra los fixtures medidos del router. El widget
(``pc_ai_monitor.blocks.router``) solo lee fuentes y pinta estas decisiones.

Datos: ``/status`` y ``/metrics`` de :11009 llegan en el Snapshot (``router()``
y ``telemetry()``); el historial acumulado llega en ``Snapshot.decisions``
(lo mantiene el colector, de-duplicado y acotado). Las tarjetas de motor son
exactamente dos — Abito (:11002, del ``/status`` del router) y Gufo (:11070,
de sondas vivas al propio motor con los parseos de ``rendimiento_logica``) —
porque «flash» en el registro del router es el motor IQ3_XXS retirado (L9):
nunca una tarjeta, a lo sumo la línea apagada «flash · motor retirado».
Nunca ``exclusive_ctx`` ni el ctx desactualizado del payload: el ctx vivo.

Reglas S11: un valor que no llega es «—», nunca un cero inventado.
"""

# pyright: reportAttributeAccessIssue=false

import time

from pc_ai_monitor.blocks.rendimiento_logica import kv_percentage, spec_ratio
from pc_ai_monitor.formato import count, safe

# Claves del dict de tarjeta de motor (pintado por router_vista.EngineCard).
# lamp: ok | down | off (estados de instrumentos_logica.LAMP_STATES).

NO_RESPONSE_TEXT = "router no responde en :11009"
FLASH_RETIRED_TEXT = "flash · motor retirado"
FLASH_LEGACY_TITLE = "CICLO DE VIDA DE FLASH (MOTOR RETIRADO)"
NOTHING_PENDING_TEXT = "nada pendiente de tu decisión"
EMPTY_DECISIONS_TEXT = "sin decisiones con estos filtros"
UNKNOWN = "—"

GUFO_PORT = 11070

# Ventanas de la barra de toggle, en minutos (S8).
WINDOW_MINUTES = (5, 15, 60)
DEFAULT_WINDOW_MIN = 15

# Etiquetas legibles de los decisores (los valores crudos son del router).
DEC_LABEL = {
    "affinity": "afinidad",
    "jev": "JEV",
    "human_confirmation": "confirmación humana",
    "deterministic_low_confidence": "confianza baja",
    "deterministic_privacy": "privacidad",
    "deterministic_jev_unavailable": "JEV no disponible",
    "deterministic": "determinista",
}


def decider_label(decider: object) -> str:
    text = str(decider or "")
    return DEC_LABEL.get(text, text or UNKNOWN)


# -- estado global de la página ---------------------------------------------


def router_down(snapshot) -> bool:
    """True sin ``/status``: la página se atenúa con el banner de corte."""
    return not snapshot.router()


# -- franja de KPIs ----------------------------------------------------------


def _pct(value: object) -> str:
    if value is None:
        return UNKNOWN
    return f"{safe(value)} %"


def _latency_text(value: object) -> str:
    if value is None:
        return UNKNOWN
    return f"{round(safe(value))} ms"


def _confidence_text(value: object) -> str:
    if value is None:
        return UNKNOWN
    return f"{safe(value):.2f}"


def kpi_values(router: dict, telemetry: dict) -> list[dict[str, str]]:
    """Seis KPIs del artboard: decisiones, % local/cloud/cola, latencia, confianza."""
    return [
        {
            "label": "DECISIONES",
            "value": count(telemetry.get("total_decisions")),
            "tip": "decisiones totales registradas por el router",
        },
        {
            "label": "% LOCAL",
            "value": _pct(telemetry.get("pct_local")),
            "tip": "decisiones que van a un motor local",
        },
        {
            "label": "% CLOUD",
            "value": _pct(telemetry.get("pct_cloud")),
            "tip": "decisiones desbordadas a cloud (NAN)",
        },
        {
            "label": "% COLA",
            "value": _pct(telemetry.get("pct_queue")),
            "tip": "decisiones que esperan en cola",
        },
        {
            "label": "LATENCIA MEDIA",
            "value": _latency_text(telemetry.get("avg_routing_latency_ms")),
            "tip": "latencia media de enrutado; incluye la puerta JEV",
        },
        {
            "label": "CONFIANZA MEDIA",
            "value": _confidence_text(telemetry.get("avg_confidence")),
            "tip": "confianza media del decisor",
        },
    ]


# -- decisiones: filtros, filas y cubetas por minuto -------------------------


def route_category(route: object) -> str:
    """Clasificación de una ruta: local (motor), cloud (nan-*/openai-*) o cola."""
    text = str(route or "")
    if text == "queue":
        return "cola"
    if text.startswith(("nan-", "openai-")):
        return "cloud"
    return "local"


def with_seen(entries: list[dict], now: float) -> list[dict]:
    """Marca decisiones sin ``_seen`` como recién vistas (edad del último sondeo).

    El colector estampa ``_seen`` al acumular; esto cubre las listas fijas
    (p. ej. ``recent_routes()`` antes de que el historial arranque).
    """
    return [{**entry, "_seen": now} for entry in entries]


def filter_decisions(
    entries: list[dict],
    route: str = "",
    decider: str = "",
    window_min: int = DEFAULT_WINDOW_MIN,
    search: str = "",
    now: float | None = None,
) -> list[dict]:
    """Filtros compartidos por la tabla y el gráfico (S8).

    Ventana por ``_seen`` (la marca del colector); una decisión sin marca se
    considera recién vista. Devuelve la más nueva primero.
    """
    if now is None:
        now = time.monotonic()
    window_s = window_min * 60
    needle = search.strip().lower()

    out: list[dict] = []
    for entry in entries:
        seen = entry.get("_seen")
        if seen is not None and now - seen >= window_s:
            continue
        if route and entry.get("chosen") != route:
            continue
        if decider and entry.get("decider") != decider:
            continue
        if needle:
            hay = " ".join(
                (
                    str(entry.get("session") or ""),
                    str(entry.get("chosen") or ""),
                    decider_label(entry.get("decider")),
                )
            ).lower()
            if needle not in hay:
                continue
        out.append(entry)

    return sorted(out, key=lambda entry: entry.get("_seen") or 0.0, reverse=True)


def duration_text(seconds: float | None) -> str:
    """Edad compacta: «30s», «2m», «1h 01m». Nunca negativa."""
    total = max(0, round(safe(seconds)))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    return f"{total // 3600}h {total % 3600 // 60:02d}m"


def decision_rows(entries: list[dict], now: float | None = None) -> list[dict]:
    """Filas de la tabla «Decisiones»: hace, sesión, ruta, decisor, confianza, latencia."""
    if now is None:
        now = time.monotonic()

    rows: list[dict] = []
    for entry in entries:
        seen = entry.get("_seen")
        confidence = entry.get("confidence")
        latency = entry.get("total_latency_ms")
        rows.append(
            {
                "hace": duration_text(now - seen) if seen is not None else UNKNOWN,
                "sesion": str(entry.get("session") or UNKNOWN),
                "ruta": str(entry.get("chosen") or UNKNOWN),
                "decisor": decider_label(entry.get("decider")),
                "confianza": _confidence_text(confidence)
                if isinstance(confidence, (int, float))
                else UNKNOWN,
                "latencia": _latency_text(latency)
                if isinstance(latency, (int, float))
                else UNKNOWN,
            }
        )
    return rows


def minute_buckets(
    entries: list[dict], window_min: int = DEFAULT_WINDOW_MIN, now: float | None = None
) -> list[dict]:
    """Cubetas por minuto para «Rutas en el tiempo» (la más vieja primero).

    Recibe las decisiones YA filtradas: la tabla y el gráfico comparten el
    mismo filtro (S8). Categorías apiladas: local / cloud / cola.
    """
    if now is None:
        now = time.monotonic()

    buckets = [
        {"min": m, "label": f"-{m}m", "local": 0, "cloud": 0, "cola": 0}
        for m in range(window_min - 1, -1, -1)
    ]
    for entry in entries:
        seen = entry.get("_seen")
        age = now - seen if seen is not None else 0.0
        index = window_min - 1 - int(age // 60)
        if index < 0:
            continue
        category = route_category(entry.get("chosen"))
        buckets[index][category] += 1
    return buckets


# -- tarjetas de motor (exactamente dos: Abito y Gufo) ------------------------


def _card(name: str, port: int | None, lamp: str, status: str, **extra) -> dict:
    """Dict de tarjeta: name, port, lamp, status + slots/queue/ctx/mtp."""
    card = {
        "name": name,
        "port": port,
        "lamp": lamp,
        "status": status,
        "slots": None,
        "queue": None,
        "ctx_total": None,
        "ctx_pct": None,
        "mtp": None,
    }
    card.update({key: value for key, value in extra.items() if value is not None})
    return card


def _mtp_text(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return f"{value:.3f}"


def abito_card(data: dict) -> dict:
    """Abito desde el ``/status`` del router: ctx vivo, nunca exclusive_ctx (L9)."""
    healthy = bool(data.get("healthy"))
    ctx = data.get("ctx")
    slots = f"{data.get('slot_busy', 0)}/{data.get('slots', 0)}" if data.get("slots") else None
    ctx_total = int(ctx) if isinstance(ctx, (int, float)) and ctx else None
    return _card(
        "Abito",
        data.get("port") or None,
        "ok" if healthy else "down",
        "vivo" if healthy else "parado",
        slots=slots,
        queue=data.get("queue"),
        ctx_total=ctx_total,
        # ctx_pct ausente: el router no informa la ventana en uso de Abito.
        mtp=_mtp_text(data.get("mtp_acceptance")),
    )


def gufo_card(metrics, served_ctx: int | None, slots) -> dict:
    """Gufo desde sondas vivas al propio motor (:11070, con los parseos reales).

    ``metrics`` es un ``rendimiento_logica.Metrics`` (None = sin lectura viva),
    ``served_ctx`` el contexto de ``/v1/models`` y ``slots`` lo parseado de
    ``/slots``. Sin datos reales la tarjeta dice «sin datos», nunca cero.
    """
    if metrics is None:
        return _card("Gufo", GUFO_PORT, "off", "sin datos")

    processing = metrics.requests_processing
    kv = metrics.kv_cache_usage_ratio
    ctx_total = served_ctx
    if ctx_total is None and slots:
        ctx_total = slots[0].n_ctx
    busy = sum(1 for slot in slots if slot.is_processing)

    mtp = spec_ratio(metrics.spec)
    return _card(
        "Gufo",
        GUFO_PORT,
        "ok",
        "vivo",
        slots=f"{busy}/{len(slots)}" if slots else None,
        queue=int(processing) if processing is not None else None,
        ctx_total=ctx_total,
        ctx_pct=kv_percentage(kv),
        mtp=f"{mtp[0]:.3f}" if mtp else None,
    )


def engine_cards(status: dict, gufo_state: dict | None) -> list[dict]:
    """Las dos tarjetas de la página, en orden; flash jamás (L9).

    ``gufo_state`` es el dict de ``gufo_card`` de la última sonda viva ({} si
    no la hay). El «flash» del ``/status`` se ignora acá: es el motor retirado.
    """
    cards = [abito_card(status.get("abito") or {})]
    cards.append(gufo_state if gufo_state else gufo_card(None, None, ()))
    return cards


def flash_line(status: dict) -> str | None:
    """La línea apagada del motor retirado, solo si el /status aún lo lista."""
    return FLASH_RETIRED_TEXT if "flash" in status else None


def flash_lifecycle_line(lc: dict) -> str | None:
    """Ciclo de vida de flash en una línea compacta, marcado como legado."""
    if not lc:
        return None
    parts = [
        str(lc.get("state") or UNKNOWN),
        f"descarga tras {safe(lc.get('idle_unload_minutes')):g} min ocioso",
        f"cargas {lc.get('loads', 0)} / descargas {lc.get('unloads', 0)}",
        "autocarga " + ("sí" if lc.get("auto_load") else "no"),
        f"último evento: {lc.get('last_event') or UNKNOWN}",
    ]
    return " · ".join(parts)


# -- confirmaciones con cuenta regresiva -------------------------------------


def countdown_text(expires_in_s: object) -> str:
    if expires_in_s is None:
        return "caduca en —"
    return countdown_left(safe(expires_in_s))


def countdown_left(seconds_left: float) -> str:
    return f"caduca en {max(0, round(safe(seconds_left)))}s"


def pending_confirmations(status: dict) -> list[dict]:
    return list(status.get("pending_confirmations") or [])


def confirm_row(item: dict) -> dict:
    return {
        "kind": str(item.get("kind") or "?"),
        "id": str(item.get("id") or "?"),
        "countdown": countdown_text(item.get("expires_in_s")),
        "reason": str(item.get("reason") or ""),
        "options": [str(option.get("route") or "?") for option in item.get("options") or []],
        "default": str(item.get("default") or ""),
    }


# -- teselas JEV / CLOUD / ENGRAM --------------------------------------------


def tiles(status: dict, telemetry: dict) -> list[dict]:
    """Tres teselas de estado: lámpara + un valor; el detalle va en el tooltip (S12)."""
    tiles_out: list[dict] = []

    jev = status.get("jev")
    if jev is None:
        tiles_out.append(
            {
                "key": "jev",
                "title": "JEV",
                "lamp": "warn",
                "value": UNKNOWN,
                "tip": "sin lectura de /status del router",
            }
        )
    else:
        has_key = bool(jev.get("key_present"))
        tiles_out.append(
            {
                "key": "jev",
                "title": "JEV",
                "lamp": "ok" if has_key else "warn",
                "value": "con clave" if has_key else "sin clave",
                "tip": (
                    f"modelo {jev.get('model') or UNKNOWN} · umbral de confianza "
                    f"{jev.get('confidence_min', UNKNOWN)} · errores "
                    f"{count(telemetry.get('jev_errors'))} · tokens acumulados "
                    f"{count(telemetry.get('jev_input_tokens_total'))} · coste estimado "
                    f"input_tokens × 42 / 1e9 = {telemetry.get('jev_spend_estimate_usd', 0)} USD"
                ),
            }
        )

    cloud = status.get("cloud")
    if cloud is None:
        tiles_out.append(
            {
                "key": "cloud",
                "title": "CLOUD",
                "lamp": "warn",
                "value": UNKNOWN,
                "tip": "sin lectura de /status del router",
            }
        )
    else:
        litellm = bool(cloud.get("litellm_healthy"))
        nan = bool(cloud.get("nan_configured"))
        openai = bool(cloud.get("openai_configured"))
        lamp = "crit" if not litellm else ("ok" if nan and openai else "warn")
        tiles_out.append(
            {
                "key": "cloud",
                "title": "CLOUD",
                "lamp": lamp,
                "value": (
                    f"{'NAN listo' if nan else 'NAN no configurado'} · "
                    f"{'OpenAI con credencial' if openai else 'OpenAI sin credencial'} · "
                    f"{'LiteLLM vivo' if litellm else 'LiteLLM caído'}"
                ),
                "tip": (
                    f"desde /status: NAN configurado={nan} · OpenAI configurado={openai} "
                    f"({cloud.get('openai_note') or 'sin nota'}) · LiteLLM saludable={litellm} "
                    f"con {len(cloud.get('litellm_models') or [])} aliases"
                ),
            }
        )

    engram = status.get("engram") or {}
    reachable = bool(engram.get("reachable"))
    service = bool(engram.get("service_active"))
    tiles_out.append(
        {
            "key": "engram",
            "title": "ENGRAM",
            "lamp": "ok" if reachable and service else "warn",
            "value": f"alcanzado · {engram.get('version') or UNKNOWN}"
            if reachable
            else "sin respuesta",
            "tip": (
                f"MCP alcanzable={reachable} · servicio activo={service} · "
                f"{engram.get('endpoint') or UNKNOWN} · memoria del agente; "
                "no es telemetría"
            ),
        }
    )
    return tiles_out


# -- sesiones ----------------------------------------------------------------


def sessions_rows(sessions: list[dict]) -> list[dict]:
    """Sesiones activas en filas compactas: id, ruta, edad, contexto."""
    rows: list[dict] = []
    for session in sessions:
        rows.append(
            {
                "id": str(session.get("short_id") or session.get("session_id") or UNKNOWN),
                "workflow": str(session.get("workflow") or UNKNOWN),
                "route": str(session.get("route") or UNKNOWN),
                "ctx": session.get("estimated_context"),
                "age": duration_text(session.get("age_s")),
            }
        )
    return rows


def ctx_text(ctx_pct: float | None, ctx_total: int | None) -> str:
    """Valor de la barra «ctx en uso»: «31 % · 131k», «131k» o «—» (S11)."""
    from pc_ai_monitor.formato import tokens

    if ctx_pct is not None and ctx_total is not None:
        return f"{round(ctx_pct)} % · {tokens(ctx_total)}"
    if ctx_total is not None:
        return tokens(ctx_total)
    return UNKNOWN
