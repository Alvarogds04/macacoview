"""Historia acotada de 15 minutos para la banda de motores de Home (T3).

Por cada sondeo del cockpit se guarda una muestra ``(t, residente, decode,
prefill, kv, cache)`` y, aparte, eventos de motor (cambio, caída, actualización,
reversión) que la banda pinta como marcadores. Todo puro y acotado a 900 s:
puro para testearlo sin GTK, acotado para que la página viva para siempre.

Regla de honestidad (S11/L9): una muestra sin motor es un hueco legítimo; una
caída sólo se registra cuando el motor desaparece **sin** un cambio de motor
que lo explique.
"""

import threading
import time
from dataclasses import dataclass

from pc_ai_monitor.widgets.instrumentos_logica import BandMarker, BandSegment

WINDOW_S = 900.0

# Tipos de evento que la banda sabe pintar como marcador.
EVENT_KINDS = ("switch", "crash", "update", "rollback")

# Estado residente → tipo de segmento de banda.
RESIDENT_KINDS = {
    "gufo": "gufo",
    "abito": "abito",
    "gufo-loading": "restart",
    "conflict": "down",
}

_ACTIVE = ("gufo", "abito")


@dataclass(frozen=True)
class Sample:
    """Una lectura del cockpit. ``resident None`` = ningún motor cargado."""

    t: float
    resident: str | None
    decode: float | None = None
    prefill: float | None = None
    kv: float | None = None
    cache: float | None = None


@dataclass(frozen=True)
class Event:
    """Un evento de motor para los marcadores de la banda."""

    t: float
    kind: str  # switch / crash / update / rollback
    detail: str
    engine: str = ""


def band_segments(samples) -> tuple[BandSegment, ...]:
    """Convierte la secuencia de muestras en segmentos de banda.

    Cada muestra representa el estado desde su ``t`` hasta la muestra
    siguiente; la última se extiende un intervalo para seguir visible.
    """
    segments: list[BandSegment] = []
    for index, current in enumerate(samples):
        kind = RESIDENT_KINDS.get(current.resident or "", "gap")
        end = samples[index + 1].t if index + 1 < len(samples) else current.t + 5.0
        if segments and segments[-1].kind == kind:
            # Extiende el segmento en curso en vez de acumular trozos iguales.
            previous = segments[-1]
            segments[-1] = BandSegment(previous.start_s, end, kind)
            continue
        segments.append(BandSegment(current.t, end, kind))
    return tuple(segments)


def lane_points(samples, attr: str) -> tuple[tuple[float, float | None], ...]:
    """Puntos ``(t, valor)`` de una serie; ``None`` marca hueco.

    ``attr`` es ``decode``, ``prefill``, ``kv`` o ``cache``.
    """
    return tuple((point.t, getattr(point, attr)) for point in samples)


class MotorHistory:
    """Buffer circular de muestras y eventos, acotado a ``window_s``."""

    def __init__(self, window_s: float = WINDOW_S) -> None:
        self._window = window_s
        self._samples: list[Sample] = []
        self._events: list[Event] = []
        # El poller escribe desde su hilo y GTK lee desde el principal.
        self._lock = threading.Lock()

    # -- escritura ---------------------------------------------------------

    def record(self, sample: Sample) -> Event | None:
        """Guarda una muestra; devuelve el evento crash si lo detecta.

        Caída (S11/L9): el motor activo desaparece de una lectura a otra sin
        un cambio de motor que lo explique.
        """
        with self._lock:
            previous = self._samples[-1] if self._samples else None
            self._samples.append(sample)
            self._trim(sample.t)

            if previous is None or previous.resident not in _ACTIVE or sample.resident is not None:
                return None
            if any(event.kind == "switch" and event.t >= previous.t for event in self._events):
                return None
            event = Event(
                sample.t, "crash",
                f"{previous.resident.capitalize()} dejó de responder sin un cambio de motor",
                engine=str(previous.resident),
            )
            self._events.append(event)
            return event

    def record_event(self, kind: str, detail: str, t: float | None = None) -> Event:
        """Registra un evento de motor; ``kind`` debe estar en ``EVENT_KINDS``."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"evento desconocido: {kind}")
        event = Event(time.monotonic() if t is None else t, kind, detail)
        with self._lock:
            self._events.append(event)
            self._trim(event.t)
        return event

    # -- lectura -----------------------------------------------------------

    def samples(self) -> tuple[Sample, ...]:
        with self._lock:
            return tuple(self._samples)

    def events(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._events)

    def markers(self) -> tuple[BandMarker, ...]:
        """Eventos como marcadores con tooltip para ``MotorBand``."""
        labels = {
            "switch": "cambio de motor",
            "crash": "caída del motor",
            "update": "actualización",
            "rollback": "reversión",
        }
        return tuple(
            BandMarker(event.t, event.kind, f"{labels.get(event.kind, event.kind)}: {event.detail}")
            for event in self.events()
        )

    def crash_engines(self, now: float | None = None) -> tuple[str, ...]:
        """Motores con una caída dentro de la ventana (mini tarjetas)."""
        samples, events = self.samples(), self.events()
        newest = samples[-1].t if samples else (now or 0.0)
        floor = newest - self._window
        return tuple(
            event.engine
            for event in events
            if event.kind == "crash" and event.engine and event.t >= floor
        )

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._events.clear()

    # -- interno -----------------------------------------------------------

    def _trim(self, now: float) -> None:
        # Llamar con self._lock tomado.
        floor = now - self._window
        while self._samples and self._samples[0].t < floor:
            self._samples.pop(0)
        while self._events and self._events[0].t < floor:
            self._events.pop(0)
