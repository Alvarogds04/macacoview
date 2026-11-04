"""Lógica pura de la sección "Motor residente" del panel Motores.

Todo lo que decide qué mostrar y qué habilitar vive acá, sin GTK y sin
subprocesos, para poder testearlo sin ventana. El widget
(``pc_ai_monitor.blocks.residente``) solo pinta estas decisiones.

Contrato con el control plane: el monitor nunca corre ``podman`` ni
``systemctl`` ni habla con el registro; todo pasa por ``local-ai-models
engine status|update`` (docs/MODEL_CONTROL_CONTRACT.md §1). Los códigos de
salida son contrato: 0 verificado, 10 falta confirmación, 1 falló y volvió a
la versión anterior.
"""

import json
from dataclasses import dataclass

MANAGER = "local-ai-models"

MISSING_MESSAGE = "No se encontró local-ai-models. Revisá la instalación del administrador."

CONFIRMATION_TEXT = (
    "El motor se reinicia para cambiar de versión: el modelo quedará "
    "indisponible unos 85 segundos (2 minutos) y toda solicitud en curso se "
    "corta. La versión anterior queda instalada y se puede volver a ella."
)


@dataclass(frozen=True)
class CliResult:
    """Salida de una invocación a ``local-ai-models``. Sin GTK."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    failure: str | None = None


@dataclass(frozen=True)
class InventoryRow:
    """Un componente del inventario de solo lectura del estado."""

    component: str
    value: str
    updatable: bool = False


@dataclass(frozen=True)
class Admission:
    """Dictamen de hospedaje que informa el propio control plane."""

    required_gib: float = 0.0
    footprint_gib: float = 0.0
    footprint_source: str = ""
    available_gib: float = 0.0
    missing_gib: float = 0.0
    satisfies: bool = False
    context: int = 0


@dataclass(frozen=True)
class EngineStatus:
    """Estado observado del motor, listo para pintar."""

    component: str = ""
    tag: str = ""
    version_label: str = ""
    revision_label: str = ""
    context: str = ""
    port: str = ""
    serving: bool = False
    available_tag: str = ""
    available_error: str = ""
    updatable: bool = False
    reason: str = ""
    inventory: tuple[InventoryRow, ...] = ()
    # Dictamen de memoria del control plane; None con un controlador viejo.
    admission: Admission | None = None
    # Fallo del CLI (no del registro): CLI ausente, timeout, JSON inválido.
    error: str = ""


def parse_engine_status(result: CliResult) -> EngineStatus:
    """Convierte la salida de ``engine status --json`` en estado visible."""
    if result.failure == "missing":
        return EngineStatus(error=MISSING_MESSAGE)
    if result.failure == "timeout":
        return EngineStatus(error="local-ai-models engine status agotó el tiempo de espera.")
    if result.failure:
        return EngineStatus(error=f"no se pudo ejecutar local-ai-models ({result.stderr}).")

    if result.returncode != 0:
        detail = result.stderr or result.stdout or "sin detalle"
        return EngineStatus(
            error=(
                "local-ai-models engine status terminó con código "
                f"{result.returncode}: {detail}"
            )
        )

    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return EngineStatus(error="local-ai-models devolvió un estado JSON inválido.")
    if not isinstance(payload, dict):
        return EngineStatus(error="local-ai-models devolvió un estado JSON inválido.")

    running = payload.get("running") or {}
    available = payload.get("available") or {}
    if not isinstance(running, dict):
        running = {}
    if not isinstance(available, dict):
        available = {}

    inventory: list[InventoryRow] = []
    for raw in payload.get("inventory") or []:
        if not isinstance(raw, dict):
            continue
        inventory.append(
            InventoryRow(
                component=str(raw.get("component", "")),
                value=str(raw.get("value", "")),
                updatable=bool(raw.get("updatable", False)),
            )
        )

    admission = _parse_admission(payload.get("admission"))

    return EngineStatus(
        component=str(payload.get("component", "")),
        tag=str(running.get("tag", "")),
        version_label=str(running.get("version_label", "")),
        revision_label=str(running.get("revision_label", "")),
        context=str(running.get("context", "")),
        port=str(running.get("port", "")),
        serving=bool(running.get("serving", False)),
        available_tag=str(available.get("tag", "")),
        available_error=str(available.get("error", "")),
        updatable=bool(payload.get("updatable", False)),
        reason=str(payload.get("reason", "")),
        inventory=tuple(inventory),
        admission=admission,
    )


def _parse_admission(raw: object) -> Admission | None:
    """Lee el dictamen de admisión; ``None`` si falta o no es un objeto.

    Con un controlador viejo la clave no está o viene en ``null``: no es un
    error, la fila se muestra como ``desconocido``. Los números se aceptan
    como float o int y se vuelven float.
    """
    if not isinstance(raw, dict):
        return None

    def _number(key: str) -> float:
        try:
            return float(raw.get(key, 0.0))
        except (TypeError, ValueError):
            return 0.0

    try:
        context = int(raw.get("context", 0))
    except (TypeError, ValueError):
        context = 0

    return Admission(
        required_gib=_number("required_gib"),
        footprint_gib=_number("footprint_gib"),
        footprint_source=str(raw.get("footprint_source", "")),
        available_gib=_number("available_gib"),
        missing_gib=_number("missing_gib"),
        satisfies=bool(raw.get("satisfies", False)),
        context=context,
    )


def admission_text(admission: Admission | None) -> str:
    """Fila ``admisión``: si la máquina puede hospedar el motor ahora."""
    if admission is None:
        return "desconocido"
    required = f"{admission.required_gib:.1f}"
    available = f"{admission.available_gib:.1f}"
    if admission.satisfies:
        return f"sí (necesita {required} GiB, hay {available} GiB)"
    missing = f"{admission.missing_gib:.1f}"
    return f"no: faltan {missing} GiB (necesita {required}, hay {available})"


def admission_warning(admission: Admission | None) -> str:
    """Aviso explícito: actualizar es posible, arrancar no lo será.

    Vacío si no hay dictamen o si la memoria alcanza. No inventa umbrales:
    repite los números que el propio control plane informa.
    """
    if admission is None or admission.satisfies:
        return ""
    missing = f"{admission.missing_gib:.1f}"
    required = f"{admission.required_gib:.1f}"
    return (
        f"Ojo: el motor se va a negar a arrancar por falta de memoria "
        f"(faltan {missing} GiB de los {required} GiB que necesita). "
        f"Actualizar la versión no soluciona eso."
    )


def display_rows(status: EngineStatus) -> list[tuple[str, str]]:
    """Filas etiqueta/valor para la tarjeta. Vacío si no hay estado real."""
    if status.error:
        return []

    if status.available_error:
        upstream = f"no se pudo consultar: {status.available_error}"
    else:
        upstream = status.available_tag or "desconocida"

    return [
        ("motor", status.component or "desconocido"),
        ("imagen", status.tag or "desconocida"),
        ("versión", status.version_label or "desconocida"),
        ("revisión", status.revision_label or "desconocida"),
        ("contexto", status.context or "desconocido"),
        ("puerto", status.port or "desconocido"),
        ("sirviendo", "sí" if status.serving else "no"),
        ("admisión", admission_text(status.admission)),
        ("última versión", upstream),
    ]


def update_enabled(status: EngineStatus) -> bool:
    """El botón solo se habilita con estado real y una actualización válida."""
    return not status.error and status.updatable


def inventory_text(status: EngineStatus) -> str:
    """Lista de solo lectura: esta sección nunca actualiza esos componentes."""
    if not status.inventory:
        return ""
    lines = ["Componentes que esta sección no actualiza:"]
    lines.extend(
        f"• {row.component}: {row.value} (no se actualiza acá)"
        for row in status.inventory
    )
    return "\n".join(lines)


def parse_update_result(result: CliResult) -> str:
    """Traduce la salida de ``engine update --yes --json`` a un mensaje."""
    if result.failure == "missing":
        return MISSING_MESSAGE
    if result.failure == "timeout":
        return (
            "La actualización agotó el tiempo de espera; leé el estado antes "
            "de reintentar."
        )
    if result.failure:
        return f"no se pudo ejecutar local-ai-models ({result.stderr})."

    if result.returncode == 0:
        moved_from, moved_to = _parse_update_tags(result.stdout)
        if moved_from and moved_to:
            return f"Motor actualizado y verificado: {moved_from} → {moved_to}."
        return "Motor actualizado y verificado."
    if result.returncode == 10:
        return "La actualización quedó a la espera de confirmación; no se cambió la versión."

    detail = result.stderr or result.stdout or "sin detalle"
    if result.returncode == 1:
        return f"La actualización falló y se restauró la versión anterior: {detail}"
    return (
        "local-ai-models engine update terminó con código "
        f"{result.returncode}: {detail}"
    )


def _parse_update_tags(stdout: str) -> tuple[str, str]:
    """Lee from/to de la salida JSON, sin suponer que están."""
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    moved_from = payload.get("from") or payload.get("from_tag") or ""
    moved_to = payload.get("to") or payload.get("to_tag") or ""
    return str(moved_from), str(moved_to)


def rollback_hint(tag: str) -> str:
    """Cómo volver atrás, solo como texto: no hay botón de rollback acá."""
    return (
        f"La versión anterior ({tag}) queda instalada. Para volver a ella: "
        f"{MANAGER} engine update --to {tag} --yes"
    )
