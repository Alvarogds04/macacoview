"""Pure logic of the «Motor residente» switch: no GTK, no subprocess, fully testable.

The panel never decides on its own what is resident: it renders what
``local-ai-models resident`` reports and turns ``local-ai-models switch`` exit
codes into messages (0 ok, 10 refused with a human reason on stderr, 1 failed).
"""

import json
import math

ENGINES = ("abito", "gufo")
NAMES = {"abito": "Abito", "gufo": "Gufo"}
SUMMARY = {
    "abito": "Qwen3.6-35B-A3B · MTP · rápido, motor base de Pi y el router",
    "gufo": "Qwen3.8 Flash-Next FULL · MTP · razonamiento xhigh",
}
FOOTPRINT = {"abito": "≈ 20 GiB de RAM", "gufo": "≈ 85 GiB de RAM"}
LOAD_HINT = {"abito": "tarda ~30 s", "gufo": "tarda ~90 s en cargar"}
TRANSIENT = {"gufo-loading"}
# Measured on this machine (Abito -> Gufo 83 s, Gufo -> Abito 19 s). The bar is an estimate: the
# real end comes from the manager, so it is held below 100 % until the switch actually finishes.
EXPECTED_SECONDS = {"gufo": 90.0, "abito": 30.0}
BAR_CEILING = 0.95


def parse_resident(stdout: str) -> dict | None:
    try:
        info = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("resident") is None:
        return None
    return info


def _context(info: dict, engine: str) -> str:
    if engine == "gufo":
        value = info.get("gufo", {}).get("context") or info.get("gufo", {}).get("configured_context")
    else:
        value = 65536 if info.get("abito", {}).get("healthy") else None
    return f"ctx {value:,}".replace(",", ".") if value else "ctx —"


def headline(info: dict | None, switching: str | None) -> str:
    if switching:
        return f"Cambiando a {NAMES[switching]}…"
    if info is None:
        return "Estado no disponible"
    resident = info.get("resident")
    if resident in NAMES:
        return f"Residente: {NAMES[resident]}"
    if resident == "gufo-loading":
        return "Gufo está cargando…"
    if resident == "conflict":
        return "⚠ Abito y Gufo figuran activos a la vez"
    return "Ningún motor grande cargado"


def engine_view(info: dict | None, engine: str, switching: str | None) -> dict:
    """One engine card: status text, whether it is the active one, and the switch button state."""
    resident = info.get("resident") if info else None
    active = resident == engine or (engine == "gufo" and resident == "gufo-loading")
    if info is None:
        status = "sin datos"
    elif active:
        status = "● activo" if resident == engine else "● cargando"
    else:
        status = "○ parado"

    detail = f"{SUMMARY[engine]}\n{_context(info or {}, engine)} · {FOOTPRINT[engine]}"
    if engine == "gufo" and info and active:
        gufo = info.get("gufo", {})
        minimum = gufo.get("min_mem_available_gib")
        if minimum is not None:
            detail += f"\nRAM libre mínima en esta sesión: {minimum:.1f} GiB"

    reason = None
    enabled = False
    if switching:
        reason = "Hay un cambio en curso."
    elif info is None:
        reason = "No se pudo leer el estado del administrador."
    elif active:
        reason = None
    elif info.get("switch_in_progress"):
        reason = "Hay un cambio de motor en curso."
    elif info.get("others_active"):
        names = ", ".join(unit.removesuffix(".service") for unit in info["others_active"])
        reason = f"Hay otro modelo grande cargado ({names}); apagalo primero."
    elif engine == "gufo" and info.get("abito", {}).get("processing"):
        reason = "Abito está ocupado respondiendo; esperá a que termine."
    elif resident in TRANSIENT:
        reason = "Gufo está cargando."
    else:
        enabled = True
    return {"status": status, "active": active, "detail": detail, "enabled": enabled,
            "reason": reason, "button": f"Cambiar a {NAMES[engine]}"}


def confirm_text(target: str) -> tuple[str, str]:
    other = "gufo" if target == "abito" else "abito"
    title = f"¿Cambiar a {NAMES[target]}?"
    if target == "gufo":
        body = (
            "Se apaga Abito (Pi y el router dejan de usarlo) y Gufo carga el modelo "
            f"grande: {LOAD_HINT['gufo']}. Si Abito está respondiendo, el cambio se rechaza. "
            "No pueden convivir los dos en memoria."
        )
    else:
        body = (
            f"Se apaga {NAMES[other]}: cualquier petición en curso se corta. "
            f"Abito {LOAD_HINT['abito']}. No pueden convivir los dos en memoria."
        )
    return title, body


def switch_outcome(returncode: int | None, stderr: str, stdout: str, target: str,
                   failure: str | None = None) -> tuple[bool, str]:
    """Message for the user after ``switch``. ``ok`` is True only when exit 0."""
    name = NAMES[target]
    if failure == "missing":
        return False, "No se encontró local-ai-models. Revisá la instalación del administrador."
    if failure == "timeout":
        return False, f"El cambio a {name} agotó el tiempo de espera; mirá el estado real arriba."
    if failure:
        return False, f"No se pudo ejecutar el cambio a {name} ({stderr or failure})."
    if returncode == 0:
        if '"ALREADY"' in stdout:
            return True, f"{name} ya era el motor residente."
        return True, f"{name} es ahora el motor residente."
    detail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
    if returncode == 10:
        return False, f"No se cambió a {name}: {detail or 'el administrador lo rechazó'}"
    if returncode == 2:
        return False, f"El administrador no reconoce el motor {name}. Es un problema del monitor."
    return False, f"El cambio a {name} falló (código {returncode}): {detail or 'sin detalle'}"


def progress_fraction(elapsed: float, target: str) -> float:
    """Estimated progress in [0, 0.95]: linear to 80 % of the expected time, then easing towards the ceiling."""
    if not elapsed or elapsed < 0:
        return 0.0
    share = elapsed / EXPECTED_SECONDS[target]
    if share <= 0.8:
        return min(BAR_CEILING, share)
    return min(BAR_CEILING, 0.8 + (BAR_CEILING - 0.8) * (1 - math.exp(-(share - 0.8) * 4)))


def progress_text(elapsed: float, target: str) -> str:
    seconds = max(0, int(elapsed))
    text = f"Cargando {NAMES[target]}… {seconds} s (progreso estimado)"
    if elapsed > EXPECTED_SECONDS[target] * 3:
        text += " · está tardando más de lo habitual"
    return text
