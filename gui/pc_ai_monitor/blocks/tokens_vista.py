"""Vista de Tokens (diseño 07, T6): reglas puras del consumo.

Todo lo que decide valores vive acá sin GTK; los bloques
(``blocks.usage``) solo arman widgets y delegan.
"""

# pyright: reportAttributeAccessIssue=false

from collections import defaultdict
from collections.abc import Callable

from pc_ai_monitor.formato import count, safe, truncate
from pc_ai_monitor.formato import tokens as fmt_tokens
from pc_ai_monitor.widgets.base import Slice

TOP_SLICES = 8
TOP_MODELS = 8
# El paso entre columnas del gráfico es ~42 px al ancho de la tarjeta:
# etiquetas de 7 caracteres entran sin pisarse (L21.5); el nombre completo
# va en el tooltip de cada columna.
MODEL_LABEL_LIMIT = 7
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


def model_columns(
    models: list[dict],
    colour_for: Callable[[str, int], str],
    top: int = TOP_MODELS,
) -> tuple[list[Slice], list[str]]:
    """Columnas de «Por modelo» (L21.5): ``(slices, tooltips)`` alineados.

    Con 53 modelos los nombres y los valores se pisaban en una franja
    ilegible: quedan los top N por total y el resto se agrupa en «resto (M)»
    con su suma. La etiqueta se recorta con elipsis; el nombre completo va
    en el tooltip. La nota de los modelos sin conteo sigue aparte, en
    ``uncounted_note``.
    """
    counted = models_with_total(models)
    chosen = counted[:top]
    excluded = counted[top:]

    slices: list[Slice] = []
    tooltips: list[str] = []
    for index, model in enumerate(chosen):
        full = str(model.get("name") or "?")
        value = safe(model.get("total"))
        slices.append(
            Slice(
                label=truncate(full, MODEL_LABEL_LIMIT),
                value=value,
                color=colour_for(full, index),
            )
        )
        tooltips.append(f"{full} · {fmt_tokens(value)} tokens")

    if excluded:
        rest_total = sum(safe(model.get("total")) for model in excluded)
        label = f"resto ({len(excluded)})"
        slices.append(Slice(label=label, value=rest_total, color=RESTO_COLOR))
        names = [str(model.get("name") or "?") for model in excluded]
        listed = ", ".join(names[:10])
        if len(names) > 10:
            listed += f" … y {len(names) - 10} más"
        tooltips.append(f"{label}: {listed} · {fmt_tokens(rest_total)} tokens")
    return slices, tooltips


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
