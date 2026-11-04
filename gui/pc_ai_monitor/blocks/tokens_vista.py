"""Vista de Tokens (diseño 07, T6): reglas puras del consumo.

Todo lo que decide valores vive acá sin GTK; los bloques
(``blocks.usage``) solo arman widgets y delegan.
"""

# pyright: reportAttributeAccessIssue=false

from collections import defaultdict
from collections.abc import Callable

from pc_ai_monitor.formato import count, safe
from pc_ai_monitor.widgets.base import Slice

TOP_SLICES = 8
RESTO_COLOR = "#5a6b7d"


def provider_totals(models: list[dict]) -> dict[str, float]:
    """Tokens por proveedor: suma de los modelos de la sección de Pi."""
    totals: dict[str, float] = defaultdict(float)
    for model in models:
        provider = str(model.get("provider") or "sin proveedor")
        totals[provider] += safe(model.get("total"))
    return dict(totals)


def provider_slices(
    totals: dict[str, float],
    colour_for: Callable[[str, int], str],
    top: int = TOP_SLICES,
) -> list[Slice]:
    """Slices del donut: top N por total, más «resto (k)» si sobra alguno."""
    ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    slices = [
        Slice(label=name, value=value, color=colour_for(name, index))
        for index, (name, value) in enumerate(ordered[:top])
    ]
    rest = sum(value for _name, value in ordered[top:])
    if rest > 0:
        slices.append(
            Slice(
                label=f"resto ({len(ordered) - top})",
                value=rest,
                color=RESTO_COLOR,
            )
        )
    return slices


def models_with_total(models: list[dict]) -> list[dict]:
    """Los modelos con conteo, mayor primero: los del gráfico de columnas."""
    counted = [model for model in models if safe(model.get("total")) > 0]
    counted.sort(key=lambda model: safe(model.get("total")), reverse=True)
    return counted


def uncounted_note(models: list[dict], limit: int = 6) -> str:
    """Nota de los modelos que Pi no contó: «… (N turnos), …»."""
    uncounted = [model for model in models if safe(model.get("total")) == 0]
    if not uncounted:
        return ""
    listed = ", ".join(
        f"{model.get('name')} ({count(safe(model.get('turns')))} turnos)"
        for model in uncounted[:limit]
    )
    return f"Sin conteo de tokens (Pi no los recibe): {listed}."


def window_colour(used: float, error: str, warning: str, green: str) -> str:
    """Color de la ventana de Codex: ≥90 rojo, ≥70 ámbar, si no verde."""
    if used >= 90:
        return error
    if used >= 70:
        return warning
    return green


def window_tooltip(label: str, used: float) -> str:
    """El detalle de la ventana va en el tooltip (S12)."""
    return (
        f"Ventana {label}: {used:.0f}% consumido, {max(0.0, 100 - used):.0f}% "
        f"disponible · ≥70 % ámbar, ≥90 % rojo"
    )


def codex_note(plan: object, windows: list[dict]) -> str:
    """Nota de la suscripción: plan y reseteo de la primera ventana."""
    if not windows:
        return "Sin ventanas informadas por codexbar."
    return f"Plan {plan or '—'} · resetea {windows[0].get('resets_at', '—')}"
