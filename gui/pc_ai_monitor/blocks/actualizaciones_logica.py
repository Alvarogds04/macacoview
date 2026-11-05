"""Lógica pura de «Actualizaciones del PC» (S6, contrato L8/L11/L12).

Parsea ``local-ai-models updates --json`` en filas visibles y traduce los
resultados de ``local-ai-models update <id> --json`` a mensajes de usuario.
Sin GTK y sin subprocesos: el bloque GTK
(``pc_ai_monitor.blocks.actualizaciones``) sólo lee fuentes y pinta.

Contrato (odd/tasks/macacoview-gtk.md L8): ``updates --json`` sale 0 con
``{"checked_at", "components": [{id, kind, name, installed, available|null,
updatable, reason|null, restarts_engine, eta_s|null, apply|null}]}``;
``available`` null = sin canal de actualización real (``updatable`` false,
``reason`` con el motivo — el fallo del canal cuando el canal existe y no
respondió). ``updatable`` true sólo cuando se observó una versión distinta.
``update <id> [--yes] [--json]`` sale 0 verificado, 0 con ``applied: false``
si no hizo falta, 10 pide confirmación o no aplica, 1 falló y volvió atrás.
``engine-abito`` necesita ``--build`` (compila llama.cpp: decenas de minutos,
reinicia Abito) y la UI lo pide explícitamente antes de usarlo.
"""

import json
from dataclasses import dataclass

from pc_ai_monitor.blocks.engine_logica import CliResult

MISSING_MESSAGE = (
    "No se encontró local-ai-models. Revisá la instalación del administrador."
)

# Palabras que delatan un canal que falló al consultarse (L8: reason = el
# fallo) frente a un canal que directamente no existe (pesos a mano, repo sin
# remoto). ``available`` null + motivo de fallo → «No se pudo consultar».
_FAILURE_HINTS = (
    "no se pudo",
    "falló",
    "error",
    "inaccesible",
    "timeout",
    "unreachable",
    "no responde",
)

BUILD_CONFIRM_TITLE = "¿Compilar llama.cpp?"
BUILD_CONFIRM_TEXT = (
    "Compilar llama.cpp lleva decenas de minutos y reinicia Abito al terminar. "
    "Confirmá para actualizar con --build."
)


@dataclass(frozen=True)
class UpdateRow:
    """Un componente actualizable, listo para pintar."""

    id: str
    kind: str
    name: str
    installed: str
    available: str | None
    updatable: bool
    reason: str
    restarts_engine: bool
    eta_s: float | None
    apply: str | None
    pill: str
    pill_class: str  # ok / acc / warn / "" (muted)
    pill_tooltip: str
    eta_text: str
    button_enabled: bool

    @property
    def needs_build(self) -> bool:
        """Los componentes que compilan piden su propia confirmación."""
        return self.id == "engine-abito" or "--build" in (self.apply or "")


@dataclass(frozen=True)
class UpdatesView:
    """El listado entero: filas o el error que dejó la lista sin actualizador."""

    rows: tuple[UpdateRow, ...] = ()
    error: str = ""
    checked_at: str = ""

    @property
    def updatable_rows(self) -> tuple[UpdateRow, ...]:
        return tuple(row for row in self.rows if row.updatable)

    @property
    def count(self) -> int:
        return len(self.updatable_rows)


@dataclass(frozen=True)
class UpdateOutcome:
    """Resultado de una actualización individual, traducido a usuario."""

    kind: str  # applied / noop / refused / build_required / rollback / error
    message: str


def parse_updates(result: CliResult) -> UpdatesView:
    """Convierte la salida de ``updates --json`` en filas visibles.

    Comando ausente, salida ≠ 0 o JSON inválido dejan la lista entera en
    estado «sin actualizador» con un mensaje MISSING-like: mejor honesto que
    un listado vacío que parece «todo al día».
    """
    if result.failure == "missing":
        return UpdatesView(error=f"{MISSING_MESSAGE} La lista queda sin actualizador.")
    if result.failure == "timeout":
        return UpdatesView(
            error=(
                "local-ai-models updates agotó el tiempo de espera: "
                "la lista queda sin actualizador."
            )
        )
    if result.failure:
        return UpdatesView(
            error=(
                f"no se pudo ejecutar local-ai-models ({result.stderr}): "
                "la lista queda sin actualizador."
            )
        )
    if result.returncode != 0:
        detail = result.stderr or result.stdout or "sin detalle"
        return UpdatesView(
            error=(
                f"local-ai-models updates terminó con código "
                f"{result.returncode}: {detail} · sin actualizador."
            )
        )

    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return UpdatesView(
            error=(
                "local-ai-models updates devolvió un JSON inválido: sin actualizador."
            )
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("components"), list):
        return UpdatesView(
            error=(
                "local-ai-models updates devolvió un listado inválido: "
                "sin actualizador."
            )
        )

    rows = tuple(
        _parse_row(item) for item in payload["components"] if isinstance(item, dict)
    )
    return UpdatesView(rows=rows, checked_at=str(payload.get("checked_at") or ""))


def _parse_row(raw: dict) -> UpdateRow:
    available = raw.get("available")
    available = str(available) if isinstance(available, str) and available else None
    updatable = bool(raw.get("updatable", False))
    reason = raw.get("reason")
    reason = str(reason) if isinstance(reason, str) and reason else ""
    eta_s = _as_float(raw.get("eta_s"))
    restarts = bool(raw.get("restarts_engine", False))

    pill, pill_class, pill_tooltip = _pill_for(updatable, available, reason)
    # L21.4: installed null (el CLI no pudo leer el estado) → «—», con el
    # tooltip «sin dato» que pone el bloque; nunca «instalada None».
    installed_raw = raw.get("installed")
    installed = (
        str(installed_raw) if isinstance(installed_raw, str) and installed_raw else "—"
    )
    return UpdateRow(
        id=str(raw.get("id", "")),
        kind=str(raw.get("kind", "")),
        name=str(raw.get("name", "")) or str(raw.get("id", "")),
        installed=installed,
        available=available,
        updatable=updatable,
        reason=reason,
        restarts_engine=restarts,
        eta_s=eta_s,
        apply=(
            str(raw.get("apply"))
            if isinstance(raw.get("apply"), str) and raw.get("apply")
            else None
        ),
        pill=pill,
        pill_class=pill_class,
        pill_tooltip=pill_tooltip,
        eta_text=_eta_text(eta_s, restarts),
        button_enabled=updatable,
    )


def _pill_for(
    updatable: bool, available: str | None, reason: str
) -> tuple[str, str, str]:
    if updatable:
        label = f"Nueva {available}" if available else "Nueva versión"
        return label, "acc", reason or "se observó una versión distinta en el canal"
    if available is None:
        if _mentions_failure(reason):
            return "No se pudo consultar", "warn", reason
        return (
            "Sin canal",
            "",
            reason or "no hay canal de actualización para este componente",
        )
    return "Al día", "ok", reason or "está en la última versión publicada"


def _mentions_failure(reason: str) -> bool:
    lowered = reason.lower()
    return any(hint in lowered for hint in _FAILURE_HINTS)


def _eta_text(eta_s: float | None, restarts_engine: bool) -> str:
    """«≈ 85 s», «decenas de minutos» cuando no se puede medir y reinicia, «—»."""
    if eta_s is not None:
        return f"≈ {eta_s:g} s"
    if restarts_engine:
        return "decenas de minutos"
    return "—"


def parse_update_result(result: CliResult) -> UpdateOutcome:
    """Traduce la salida de ``update <id> --json`` a un mensaje de usuario.

    Códigos de salida contractuales: 0 verificado (o ``applied: false``
    si no hizo falta), 10 pide confirmación o no aplica (con
    ``build_required: true`` cuando falta compilar), 1 falló y volvió atrás.
    """
    if result.failure == "missing":
        return UpdateOutcome("error", MISSING_MESSAGE)
    if result.failure == "timeout":
        return UpdateOutcome(
            "error",
            "La actualización agotó el tiempo de espera; mirá el listado "
            "antes de reintentar.",
        )
    if result.failure:
        return UpdateOutcome(
            "error", f"no se pudo ejecutar local-ai-models ({result.stderr})."
        )

    payload = _json_dict(result.stdout)

    if result.returncode == 0:
        if payload.get("applied") is False:
            reason = str(payload.get("reason") or "ya está en la versión de destino")
            return UpdateOutcome("noop", f"No hizo falta actualizar: {reason}.")
        moved_from, moved_to = _tags(payload)
        verified = payload.get("verified") is True
        if moved_from and moved_to:
            verb = "Actualizado y verificado" if verified else "Actualizado"
            return UpdateOutcome("applied", f"{verb}: {moved_from} → {moved_to}.")
        if verified:
            return UpdateOutcome("applied", "Actualizado y verificado.")
        return UpdateOutcome("applied", "La actualización terminó.")

    if result.returncode == 10:
        if payload.get("build_required"):
            return UpdateOutcome(
                "build_required",
                "Hace falta compilar llama.cpp: decenas de minutos y reinicia "
                "Abito. Confirmá para actualizar con --build.",
            )
        reason = str(payload.get("reason") or "") or result.stderr
        return UpdateOutcome(
            "refused",
            f"No se actualizó: {reason or 'el administrador lo rechazó'}",
        )

    if result.returncode == 1:
        # L14: con --json el fallo emite JSON {action, from, to, applied,
        # verified, restored, reason}: la rama se decide por esos campos
        # (verified false / restored true), no por el texto de stderr.
        restored = payload.get("verified") is False and payload.get("restored") is True
        if restored:
            moved_from, moved_to = _tags(payload)
            reason = str(payload.get("reason") or "")
            detail = (
                f"{moved_from} → {moved_to}: {reason}"
                if moved_from and moved_to
                else reason or "sin detalle"
            )
        else:
            detail = result.stderr or result.stdout or "sin detalle"
        return UpdateOutcome(
            "rollback",
            f"La actualización falló y se restauró la versión anterior: {detail}",
        )

    detail = result.stderr or result.stdout or "sin detalle"
    return UpdateOutcome(
        "error",
        f"local-ai-models update terminó con código {result.returncode}: {detail}",
    )


def confirm_text(row: UpdateRow) -> tuple[str, str]:
    """Confirmación antes de actualizar: eta y si el motor se reinicia."""
    title = f"¿Actualizar {row.name}?"
    if row.restarts_engine:
        body = (
            "El motor se reinicia para aplicar el cambio "
            f"({row.eta_text}) y las peticiones en curso se cortan."
        )
    elif row.eta_text != "—":
        body = f"Tarda {row.eta_text} y no reinicia ningún motor."
    else:
        body = "No reinicia ningún motor."
    return title, body


def update_args(row: UpdateRow, with_build: bool = False) -> tuple[str, ...]:
    """La orden completa: ``update <id> [--build] --yes --json``."""
    args = ["update", row.id]
    if with_build:
        args.append("--build")
    args.extend(["--yes", "--json"])
    return tuple(args)


# -- conversiones tolerantes -------------------------------------------------


def _json_dict(stdout: str) -> dict:
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tags(payload: dict) -> tuple[str, str]:
    moved_from = payload.get("from") or payload.get("from_tag") or ""
    moved_to = payload.get("to") or payload.get("to_tag") or ""
    return str(moved_from), str(moved_to)


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
