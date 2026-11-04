"""Lógica pura de la tarjeta "Rendimiento del motor" del panel Motores.

Todo lo que decide qué mostrar vive acá, sin GTK y sin subprocesos, para
poder testearlo sin ventana. El widget (``pc_ai_monitor.blocks.rendimiento``)
solo lee las fuentes y pinta estas decisiones.

Fuentes, todas de solo lectura y contra el propio motor Gufo en
``127.0.0.1:11070``: ``/metrics`` (texto Prometheus), ``/v1/models``
(contexto servido), ``/slots`` y ``gufo-flash status`` — el controlador del
propio motor, dueño documentado de los números de memoria. Nunca ``podman``
ni ``systemctl`` desde acá.

Regla de tolerancia: una serie que no aparece es "desconocida", nunca cero.
"""

import json
import time
from dataclasses import dataclass

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 11070
ENGINE_BASE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"

# Plazo acotado del subproceso ``gufo-flash status`` (solo desde un worker).
GUFO_FLASH_TIMEOUT_S = 15

# Umbrales del guard del motor: MemAvailable mínima tras cargar (15 GiB) y
# durante el trabajo (12 GiB). Por debajo de esos pisos el guard detiene el
# motor, así que la tarjeta avisa en español claro al cruzarlos.
POSTLOAD_FLOOR_GIB = 15.0
WORK_FLOOR_GIB = 12.0

UNKNOWN = "desconocido"
SPEED_UNKNOWN = "sin datos recientes"


# -- /metrics (texto Prometheus) -------------------------------------------


@dataclass(frozen=True)
class Metrics:
    """Series conocidas de ``/metrics``. ``None`` = la serie no apareció."""

    prompt_tokens_total: float | None = None
    prompt_tokens_processed_total: float | None = None
    tokens_predicted_total: float | None = None
    prompt_tokens_cached_total: float | None = None
    requests_processing: float | None = None
    kv_cache_usage_ratio: float | None = None
    device_lost_total: float | None = None
    # Contadores gufo_spec_* presentes, con su nombre de serie completo
    # (incluye etiquetas); la última muestra gana.
    spec: tuple[tuple[str, float], ...] = ()


# nombre de serie (sin etiquetas) -> campo de Metrics
_KNOWN_SERIES = {
    "llamacpp:prompt_tokens_total": "prompt_tokens_total",
    "llamacpp:prompt_tokens_processed_total": "prompt_tokens_processed_total",
    "llamacpp:tokens_predicted_total": "tokens_predicted_total",
    "llamacpp:prompt_tokens_cached_total": "prompt_tokens_cached_total",
    "llamacpp:requests_processing": "requests_processing",
    "llamacpp:kv_cache_usage_ratio": "kv_cache_usage_ratio",
    "gufo_device_lost_total": "device_lost_total",
}


def parse_metrics(text: str) -> Metrics:
    """Parsea el texto Prometheus de ``/metrics`` de forma tolerante.

    Ignora líneas ``#`` (HELP/TYPE, duplicados incluidos: no rompen), acepta
    muestras con etiquetas y con timestamp, suma muestras repetidas de una
    serie conocida y devuelve ``None`` por cada serie ausente.
    """
    totals: dict[str, float] = {}
    spec: dict[str, float] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        if not rest:
            continue
        try:
            value = float(rest.split(" ")[0])
        except ValueError:
            continue
        bare = name.split("{", 1)[0]
        if bare.startswith("gufo_spec_"):
            spec[name] = value
        elif bare in _KNOWN_SERIES:
            field = _KNOWN_SERIES[bare]
            totals[field] = totals.get(field, 0.0) + value

    return Metrics(spec=tuple(sorted(spec.items())), **totals)


def cached_percentage(
    cached: float | None, total: float | None
) -> float | None:
    """Porcentaje de prompt servido desde caché: cached/total. None sin datos."""
    if cached is None or total is None or total <= 0:
        return None
    percentage = cached / total * 100.0
    return round(min(max(percentage, 0.0), 100.0), 1)


def kv_percentage(ratio: float | None) -> float | None:
    """Ratio de uso de la ventana KV (0..1) como porcentaje, redondeado."""
    if ratio is None:
        return None
    percentage = ratio * 100.0
    return round(min(max(percentage, 0.0), 100.0), 1)


def spec_ratio(
    spec: tuple[tuple[str, float], ...],
) -> tuple[float, float, float] | None:
    """Ratio borradores aceptados/propuestos cuando ambos contadores existen.

    Acepta nombres con "accept" o "proposed"/"draft" en cualquier orden y
    suma los que aparezcan. Devuelve (ratio, aceptados, propuestos) o None
    si falta alguno o no hay propuestas que dividir.
    """
    accepted: float | None = None
    proposed: float | None = None
    for name, value in spec:
        lower = name.lower()
        if "accept" in lower:
            accepted = value if accepted is None else accepted + value
        elif "proposed" in lower or "draft" in lower:
            proposed = value if proposed is None else proposed + value
    if accepted is None or proposed is None or proposed <= 0:
        return None
    ratio = min(max(accepted / proposed, 0.0), 1.0)
    return (ratio, accepted, proposed)


# -- /v1/models y /slots ----------------------------------------------------


def parse_served_context(text: str) -> int | None:
    """Lee ``context_length`` de ``/v1/models``: la ventana que sirve de verdad."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("data")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("context_length")
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


@dataclass(frozen=True)
class Slot:
    """Un slot de ``/slots`` con los campos que muestra la tarjeta."""

    n_ctx: int | None = None
    is_processing: bool | None = None
    n_prompt_tokens: float | None = None
    n_prompt_tokens_processed: float | None = None


def parse_slots(text: str) -> tuple[Slot, ...]:
    """Parsea ``/slots`` (lista directa o envuelta en {"slots": [...]})."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return ()
    if isinstance(payload, dict):
        payload = payload.get("slots")
    if not isinstance(payload, list):
        return ()

    slots: list[Slot] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        slots.append(
            Slot(
                n_ctx=_as_int(raw.get("n_ctx")),
                is_processing=_as_bool(raw.get("is_processing")),
                n_prompt_tokens=_as_float(raw.get("n_prompt_tokens")),
                n_prompt_tokens_processed=_as_float(
                    raw.get("n_prompt_tokens_processed")
                ),
            )
        )
    return tuple(slots)


# -- gufo-flash status (dueño de los números de memoria) --------------------


@dataclass(frozen=True)
class Guard:
    """Números del guard de memoria según ``gufo-flash status``."""

    mem_available_gib: float | None = None
    conservative_gib: float | None = None
    min_mem_available_gib: float | None = None
    min_conservative_gib: float | None = None
    phase: str = ""
    engine_ready: bool = False
    error: str = ""


def parse_guard(
    stdout: str, returncode: int | None, failure: str | None
) -> Guard:
    """Convierte la salida de ``gufo-flash status`` en números del guard."""
    if failure == "missing":
        return Guard(
            error="No se encontró gufo-flash: la memoria queda sin datos."
        )
    if failure == "timeout":
        return Guard(
            error="gufo-flash status agotó el tiempo de espera: memoria sin datos."
        )
    if failure:
        return Guard(error=f"no se pudo ejecutar gufo-flash status ({failure}).")
    if returncode != 0:
        return Guard(
            error=f"gufo-flash status terminó con código {returncode}: memoria sin datos."
        )
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return Guard(error="gufo-flash status devolvió un JSON inválido.")
    if not isinstance(payload, dict):
        return Guard(error="gufo-flash status devolvió un JSON inválido.")

    status = payload.get("status")
    if not isinstance(status, dict):
        status = {}
    return Guard(
        mem_available_gib=_as_float(status.get("mem_available_gib")),
        conservative_gib=_as_float(status.get("conservative_gib")),
        min_mem_available_gib=_as_float(status.get("min_mem_available_gib")),
        min_conservative_gib=_as_float(status.get("min_conservative_gib")),
        phase=str(status.get("phase") or ""),
        engine_ready=bool(payload.get("engine_ready", False)),
    )


def memory_warning(mem_available_gib: float | None) -> str:
    """Aviso en claro al cruzar los pisos del guard: 15 GiB y 12 GiB."""
    if mem_available_gib is None:
        return ""
    if mem_available_gib < WORK_FLOOR_GIB:
        return (
            "⚠️ MemAvailable por debajo de 12 GiB: es el mínimo que el guard "
            "exige durante el trabajo y puede detener el motor."
        )
    if mem_available_gib < POSTLOAD_FLOOR_GIB:
        return (
            "⚠️ MemAvailable por debajo de 15 GiB: es el mínimo que el guard "
            "exige tras cargar el motor; puede detenerlo al entrar en trabajo."
        )
    return ""


# -- velocidades ------------------------------------------------------------


class SpeedTracker:
    """Convierte el delta de un contador propio del motor en tokens/s.

    Guarda la última tasa observada: si entre lecturas no hubo actividad
    (delta 0) o el contador se reinició, devuelve None en vez de inventar un
    cero, y la tarjeta sigue mostrando el último valor conocido.
    """

    def __init__(self) -> None:
        self._prev_value: float | None = None
        self._prev_time: float | None = None
        self._last_rate: float | None = None

    def update(self, value: float | None, now: float) -> float | None:
        """Registra una muestra; devuelve tokens/s solo con delta positivo."""
        prev_value, prev_time = self._prev_value, self._prev_time
        self._prev_value, self._prev_time = value, now
        if (
            value is None
            or prev_value is None
            or prev_time is None
            or now <= prev_time
            or value <= prev_value
        ):
            return None
        rate = (value - prev_value) / (now - prev_time)
        self._last_rate = rate
        return rate

    @property
    def last(self) -> float | None:
        """Última tasa observada, para mostrar mientras no hay actividad."""
        return self._last_rate


def speed_text(rate: float | None) -> str:
    if rate is None:
        return SPEED_UNKNOWN
    return f"{rate:.1f} tok/s"


# -- vista ------------------------------------------------------------------


@dataclass(frozen=True)
class RendimientoView:
    """Estado listo para pintar: filas, avisos y frescura de los datos."""

    rows: tuple[tuple[str, str], ...] = ()
    warning: str = ""
    error: str = ""
    stale: bool = False
    last_read: str = ""


def build_view(
    metrics: Metrics,
    served_context: int | None,
    slots: tuple[Slot, ...],
    guard: Guard,
    prefill_rate: float | None,
    decode_rate: float | None,
    last_read: str,
) -> RendimientoView:
    """Arma las filas de la tarjeta a partir de una lectura correcta."""
    ratio = spec_ratio(metrics.spec)
    if ratio is None:
        speculation = "sin contadores de borradores"
    else:
        ratio_value, accepted, proposed = ratio
        speculation = (
            f"{ratio_value * 100.0:.0f} % aceptados "
            f"({accepted:.0f} de {proposed:.0f} borradores)"
        )

    if metrics.requests_processing is None:
        processing = UNKNOWN
    else:
        processing = f"{metrics.requests_processing:.0f}"

    rows: list[tuple[str, str]] = [
        ("salud", health_text(guard)),
        ("contexto servido", str(served_context) if served_context is not None else UNKNOWN),
        ("solicitudes en curso", processing),
        ("slot", slot_text(slots)),
        ("prefill", speed_text(prefill_rate)),
        ("decode", speed_text(decode_rate)),
        (
            "caché de prompt",
            _pct_text(
                cached_percentage(
                    metrics.prompt_tokens_cached_total,
                    metrics.prompt_tokens_total,
                )
            ),
        ),
        ("ventana en uso", _pct_text(kv_percentage(metrics.kv_cache_usage_ratio))),
        ("especulación", speculation),
        ("memoria disponible", _gib_text(guard.mem_available_gib)),
        ("memoria conservadora", _gib_text(guard.conservative_gib)),
        (
            "mínimos observados",
            f"disponible {_gib_text(guard.min_mem_available_gib)} · "
            f"conservador {_gib_text(guard.min_conservative_gib)}",
        ),
        ("pérdidas de contexto GPU", device_lost_text(metrics.device_lost_total)),
    ]
    return RendimientoView(
        rows=tuple(rows),
        warning=memory_warning(guard.mem_available_gib),
        last_read=last_read,
    )


def stale_view(
    previous: RendimientoView, error: str
) -> RendimientoView:
    """Mantiene los últimos valores conocidos y los marca desactualizados.

    Una lectura fallida nunca borra la tarjeta: conserva filas, aviso y la
    marca de la última lectura correcta, y añade el error de esta vez.
    """
    return RendimientoView(
        rows=previous.rows,
        warning=previous.warning,
        error=error,
        stale=True,
        last_read=previous.last_read,
    )


def footer_text(view: RendimientoView) -> str:
    """Marca de frescura: cuándo fue la última lectura correcta."""
    if view.last_read:
        text = f"última lectura correcta: {view.last_read}"
    else:
        text = "sin lecturas correctas todavía"
    if view.stale:
        text += " · valores desactualizados"
    return text


def health_text(guard: Guard) -> str:
    """Línea de salud: listo o no, fase del guard y puerto del motor."""
    if guard.error:
        estado = "desconocido"
    elif guard.engine_ready:
        estado = "listo"
    else:
        estado = "no listo"
    if guard.phase and not guard.error:
        estado += f" · fase: {guard.phase}"
    return f"{estado} · puerto {ENGINE_PORT}"


def slot_text(slots: tuple[Slot, ...]) -> str:
    """Estado del primer slot: procesando o en espera, con prompt procesado."""
    if not slots:
        return UNKNOWN
    slot = slots[0]
    if slot.is_processing is None and slot.n_prompt_tokens_processed is None:
        return UNKNOWN
    estado = "procesando" if slot.is_processing else "en espera"
    tokens = ""
    if slot.n_prompt_tokens_processed is not None:
        tokens = f" · prompt procesado: {slot.n_prompt_tokens_processed:.0f}"
    return f"{estado}{tokens}"


def device_lost_text(value: float | None) -> str:
    """Pérdidas de contexto GPU desde el arranque: 0 es la buena noticia."""
    if value is None:
        return UNKNOWN
    if value == 0:
        return "0 (sin pérdidas desde el arranque)"
    return f"{value:.0f} (hubo pérdida de contexto)"


def format_timestamp(wall: float) -> str:
    """Hora local legible para la marca de última lectura correcta."""
    return time.strftime("%H:%M:%S", time.localtime(wall))


# -- Conversiones tolerantes -------------------------------------------------


def _pct_text(value: float | None) -> str:
    return f"{value:.1f} %" if value is not None else UNKNOWN


def _gib_text(value: float | None) -> str:
    return f"{value:.1f} GiB" if value is not None else UNKNOWN


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
