"""Vista rediseñada de la página Motores (diseño 07, T4).

Columna izquierda: «Motor residente» — headline, dos tarjetas con lámpara,
estado, modelo, ctx, las dos cifras de RAM (S9: «≈ 85 GiB reservados ·
49.2 GiB en uso») y el botón «Cambiar a …» sólo en la inactiva — y
«Actualizaciones del PC» (``blocks.actualizaciones``). Columna derecha:
«Rendimiento del motor» como instrumentos (S7: lámpara de salud, contexto
servido, pedidos, sparklines decode/prefill, dial MTP, anillo de caché,
barra de ventana KV, lámpara GPU, barras de memoria con sus mínimos,
«Actualizar» manual) y «Motores locales» (``blocks.motores.MotoresBlock``,
sin cambios).

Reutiliza sin duplicar: ``residente_logica`` (headline, engine_view,
confirm_text, switch_outcome, progress_*), ``blocks.rendimiento``
(fetchers ``_http_text`` / ``_run_gufo_flash_status``) + ``rendimiento_logica``
(build_view / stale_view, memory_warning, health_text, footer_text,
SpeedTracker), ``home_logica`` (reservas, medición «en uso», headroom_rows),
``blocks.home`` (barra de margen con mínimo) y los instrumentos de T2
(Lamp, Ring, Dial) más ``graficos.Sparkline``. Las órdenes pasan por el
``run_manager`` de ``blocks.motores``: nada en el hilo de GTK.

El motor retirado «flash» jamás aparece: las tarjetas de residente son
siempre Abito y Gufo (L9) y el inventario vive en ``MotoresBlock``.
"""

# pyright: reportAttributeAccessIssue=false

import threading
import time
from collections import deque

from gi.repository import Adw, GLib, Gtk

from pc_ai_monitor.blocks import Block, progreso, rendimiento
from pc_ai_monitor.blocks.home import _headroom_value_text, _HeadroomBar
from pc_ai_monitor.blocks.home_logica import (
    ABITO_PORT,
    ENGINE_CTX,
    GUFO_PORT,
    RESERVATION_GIB,
    HeadroomRow,
    engine_model,
    gpu_lamp_state,
    headroom_rows,
    measured_gib,
    mtp_ratio,
)
from pc_ai_monitor.blocks.motores import _run_manager, run_manager
from pc_ai_monitor.blocks.rendimiento import _http_text, _run_gufo_flash_status
from pc_ai_monitor.blocks.rendimiento_logica import (
    ENGINE_PORT,
    Guard,
    Metrics,
    RendimientoView,
    SpeedTracker,
    build_view,
    cached_percentage,
    footer_text,
    format_timestamp,
    health_text,
    kv_percentage,
    parse_guard,
    parse_metrics,
    parse_served_context,
    parse_slots,
    speed_text,
    stale_view,
)
from pc_ai_monitor.blocks.residente import SWITCH_TIMEOUT_S
from pc_ai_monitor.blocks.residente_logica import (
    ENGINES,
    LOAD_HINT,
    NAMES,
    SUMMARY,
    confirm_text,
    engine_view,
    headline,
    parse_resident,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.widgets.graficos import Sparkline
from pc_ai_monitor.widgets.instrumentos import Dial, Lamp, Ring, _color

REFRESH_INTERVAL_S = 3.0
PERF_REFRESH_INTERVAL_S = 5.0


def _margin_bar(width: int) -> _HeadroomBar:
    return _HeadroomBar(width=width, height=12)


def resident_figures(
    engine: str, models: list[dict] | None, resident: str | None
) -> tuple[str, str]:
    """Las dos cifras S9 de una tarjeta: (reservados, en uso).

    La reserva viene de ``home_logica.RESERVATION_GIB`` (la misma decisión
    «los 2» del cockpit); el uso se mide del snapshot (el mayor de RSS y
    GTT, como en Home). Un motor parado no presume uso («—») y el residente
    sin medición usa la reserva con la marca «sin medir»: nunca un 0
    inventado.
    """
    reservation = RESERVATION_GIB[engine]
    reserved = f"≈ {reservation:.0f} GiB reservados"
    model = engine_model(models or [], GUFO_PORT if engine == "gufo" else ABITO_PORT)
    used = measured_gib(model) if model else None
    if used and _is_active(resident, engine):
        return reserved, f"{used:.1f} GiB en uso"
    if _is_active(resident, engine):
        return reserved, f"≈ {reservation:.0f} GiB en uso (sin medir)"
    return reserved, "—"


def figures_text(engine: str, models: list[dict] | None, resident: str | None) -> str:
    reserved, used = resident_figures(engine, models, resident)
    return f"{reserved} · {used}"


def _is_active(resident: str | None, engine: str) -> bool:
    return resident == engine or (engine == "gufo" and resident == "gufo-loading")


def _ctx_text(engine: str) -> str:
    return f"ctx {ENGINE_CTX[engine]:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Motor residente: dos tarjetas S9 + cambio (misma ruta que residente.py)
# ---------------------------------------------------------------------------


class ResidenteVistaBlock(Block):
    """«Motor residente» del diseño: tarjetas con lámpara y cifras S9."""

    title = "MOTOR RESIDENTE"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._info: dict | None = None
        self._switching: str | None = None
        self._snapshot: Snapshot | None = None
        self._refreshing = False
        self._last_refresh = 0.0

        self._headline = Gtk.Label(xalign=0)
        self._headline.add_css_class("heading")
        self.body.append(self._headline)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_homogeneous(True)
        self._cards = {engine: self._build_card(engine) for engine in ENGINES}
        for engine in ENGINES:
            row.append(self._cards[engine]["widget"])
        self.body.append(row)

        # S14: el cambio de motor muestra su progreso honesto acá (etapa de
        # gufo-flash status al cargar Gufo; nunca un % calculado por reloj).
        self._progress = progreso.ProgressInline("switch")
        self.body.append(self._progress)

        self._render()
        self._refresh(force=True)

    # -- construcción ------------------------------------------------------

    def _build_card(self, engine: str) -> dict:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.add_css_class("card")
        box.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lamp = Lamp(10)
        top.append(lamp)
        name = Gtk.Label(label=NAMES[engine], xalign=0)
        name.add_css_class("model-name")
        name.set_hexpand(True)
        top.append(name)
        status = Gtk.Label(xalign=1)
        status.add_css_class("mono")
        status.add_css_class("muted")
        top.append(status)
        box.append(top)

        meta = Gtk.Label(xalign=0)
        meta.add_css_class("caption")
        meta.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.append(meta)

        figures = Gtk.Label(xalign=0)
        figures.add_css_class("caption")
        figures.set_ellipsize(3)
        box.append(figures)

        hint = Gtk.Label(xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("muted")
        box.append(hint)

        reason = Gtk.Label(xalign=0)
        reason.add_css_class("muted")
        reason.set_wrap(True)
        reason.set_max_width_chars(40)
        reason.set_visible(False)
        box.append(reason)

        button = Gtk.Button()
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.START)
        button.connect("clicked", lambda _b, e=engine: self._ask_confirmation(e))
        box.append(button)

        return {
            "widget": box,
            "lamp": lamp,
            "name": name,
            "status": status,
            "meta": meta,
            "figures": figures,
            "hint": hint,
            "reason": reason,
            "button": button,
        }

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        # Las cifras S9 vienen del snapshot: re-pintar aunque el sondeo
        # del administrador siga throttled.
        self._render()
        self._refresh(force=False)

    def redraw(self) -> None:
        for card in self._cards.values():
            card["lamp"].redraw()

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing or self._switching is not None:
            return
        if not force and now - self._last_refresh < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_refresh = now

        def _worker() -> None:
            result = _run_manager("resident")
            info = parse_resident(result.stdout) if result.returncode == 0 else None
            GLib.idle_add(self._on_resident, info)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_resident(self, info: dict | None) -> bool:
        self._refreshing = False
        self._info = info
        self._render()
        return False

    # -- pintado -----------------------------------------------------------

    def _render(self) -> None:
        # Un cambio empezado en Home también bloquea estos botones.
        switching = self._switching or progreso.switching_target()
        self._headline.set_text(headline(self._info, switching))
        models = self._snapshot.models() if self._snapshot else None
        resident = self._info.get("resident") if self._info else None
        for engine in ENGINES:
            view = engine_view(self._info, engine, switching)
            self._render_card(self._cards[engine], view, models, resident)

    def _render_card(self, card: dict, view: dict, models, resident) -> None:
        engine = card["name"] and card["widget"]
        del engine  # las tarjetas son siempre Abito y Gufo (L9): nada más entra
        loading = resident == "gufo-loading" and view["active"]
        card["status"].set_text(view["status"].lstrip("●○ ").strip())
        card["lamp"].set_data("stale" if loading else "ok" if view["active"] else "off")
        card["meta"].set_text(
            f"{SUMMARY['gufo' if card is self._cards['gufo'] else 'abito'].split(' · ')[0]}"
            f" · {_ctx_text('gufo' if card is self._cards['gufo'] else 'abito')}"
        )
        card["figures"].set_text(
            figures_text(
                "gufo" if card is self._cards["gufo"] else "abito",
                models,
                resident,
            )
        )
        card["hint"].set_text(
            LOAD_HINT["gufo" if card is self._cards["gufo"] else "abito"]
        )
        card["reason"].set_text(view["reason"] or "")
        card["reason"].set_visible(bool(view["reason"]))
        card["button"].set_label(view["button"])
        card["button"].set_visible(not view["active"])
        card["button"].set_sensitive(view["enabled"])
        card["widget"].set_tooltip_text(view["detail"])

    # -- acción: cambiar de motor (misma confirmación y comando) -----------

    def _ask_confirmation(self, target: str) -> None:
        if self._switching is not None or progreso.HUB.running("switch"):
            return
        view = engine_view(self._info, target, None)
        if not view["enabled"]:
            return  # el motivo ya está en la tarjeta
        title, body = confirm_text(target)
        root = self.get_root()
        dialog = Adw.MessageDialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            heading=title,
            body=body,
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("switch", "Cambiar")
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response == "switch":
                self._start_switch(target)

        dialog.connect("response", _on_response)
        dialog.present()

    def _start_switch(self, target: str) -> None:
        if progreso.HUB.running("switch"):
            return  # otro cambio (Home) ya está en curso
        self._switching = target
        self._render()
        progreso.run_switch(
            target,
            lambda args, timeout: run_manager(args, timeout),
            lambda *args: _run_manager(*args),
            lambda: rendimiento._run_gufo_flash_status(),
            self._on_switched,
            section="motores",
            anchor=self._progress,
            retry=lambda: self._ask_confirmation(target),
            timeout=SWITCH_TIMEOUT_S,
        )

    def _on_switched(self, ok: bool, message: str, info: dict | None) -> None:
        del ok, message  # el resultado lo pinta el bloque de progreso
        self._switching = None
        self._on_resident(info)


# ---------------------------------------------------------------------------
# Rendimiento del motor como instrumentos (S7, mismos fetchers que T2)
# ---------------------------------------------------------------------------


class RendimientoVistaBlock(Block):
    """«Rendimiento del motor»: las 13 filas de texto, ahora instrumentos."""

    title = "RENDIMIENTO DEL MOTOR"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._view = RendimientoView()
        self._refreshing = False
        self._last_refresh = 0.0
        self._snapshot: Snapshot | None = None
        self._metrics: Metrics | None = None
        self._served: int | None = None
        self._guard: Guard | None = None
        self._prefill = SpeedTracker()
        self._decode = SpeedTracker()
        # Series cortas para las sparklines: sólo tasas observadas.
        self._decode_series: deque[float] = deque(maxlen=48)
        self._prefill_series: deque[float] = deque(maxlen=48)

        # Banners de aviso/error (S7, S12): color, frase corta, detalle en tooltip.
        self._warn_banner = Adw.Banner()
        self._warn_banner.set_revealed(False)
        self.body.append(self._warn_banner)
        self._err_banner = Adw.Banner()
        self._err_banner.set_revealed(False)
        self.body.append(self._err_banner)

        grid = Gtk.Grid(
            row_spacing=12, column_spacing=18, margin_top=4, margin_bottom=4
        )
        self.body.append(grid)

        self._salud_lamp = Lamp(12)
        self._salud_text = Gtk.Label(xalign=0)
        salud = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        salud.append(self._salud_lamp)
        salud.append(self._salud_text)
        grid.attach(self._tile(salud, "Salud"), 0, 0, 1, 1)

        self._ctx_value = Gtk.Label(xalign=0)
        self._ctx_value.add_css_class("heading")
        grid.attach(self._tile(self._ctx_value, "Contexto servido"), 1, 0, 1, 1)

        self._reqs_value = Gtk.Label(xalign=0)
        self._reqs_value.add_css_class("heading")
        grid.attach(self._tile(self._reqs_value, "Pedidos en curso"), 2, 0, 1, 1)

        self._decode_spark = Sparkline(width=190, height=34, color="accent")
        self._decode_value = Gtk.Label(xalign=0)
        grid.attach(
            self._tile(self._decode_spark, "Decode · tok/s", self._decode_value),
            0,
            1,
            1,
            1,
        )

        self._prefill_spark = Sparkline(width=190, height=34, color="blue")
        self._prefill_value = Gtk.Label(xalign=0)
        grid.attach(
            self._tile(self._prefill_spark, "Prefill · tok/s", self._prefill_value),
            1,
            1,
            1,
            1,
        )

        self._mtp_dial = Dial(size=54)
        grid.attach(self._tile(self._mtp_dial, "MTP"), 2, 1, 1, 1)

        self._cache_ring = Ring(size=54, thickness=9)
        grid.attach(self._tile(self._cache_ring, "Caché de prompt"), 0, 2, 1, 1)

        self._kv_bar = Gtk.ProgressBar()
        self._kv_value = Gtk.Label(xalign=0)
        grid.attach(self._tile(self._kv_bar, "Ventana KV", self._kv_value), 1, 2, 1, 1)

        self._gpu_lamp = Lamp(12)
        self._gpu_label = Gtk.Label(xalign=0)
        self._gpu_label.add_css_class("caption")
        gpu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        gpu.append(self._gpu_lamp)
        gpu.append(self._gpu_label)
        grid.attach(self._tile(gpu, "GPU"), 2, 2, 1, 1)

        # Memoria disponible / conservadora con sus mínimos (fila ancha).
        mem = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Label(label="Memoria · disponible y conservadora", xalign=0)
        head.add_css_class("caption")
        head.add_css_class("muted")
        mem.append(head)
        self._headroom_bars: list[_HeadroomBar] = []
        for label in ("Disponible", "Conservadora"):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("caption")
            caption.add_css_class("muted")
            caption.set_size_request(86, -1)
            line.append(caption)
            bar = _margin_bar(width=260)
            bar.set_hexpand(True)
            line.append(bar)
            value = Gtk.Label(xalign=1)
            value.add_css_class("mono")
            line.append(value)
            bar._value = value  # el test lee el valor desde la barra
            self._headroom_bars.append(bar)
            mem.append(line)
        self._mem_mins = Gtk.Label(xalign=0)
        self._mem_mins.add_css_class("caption")
        self._mem_mins.add_css_class("muted")
        mem.append(self._mem_mins)
        grid.attach(self._tile(mem, ""), 0, 3, 3, 1)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._refresh_button = Gtk.Button(label="Actualizar")
        self._refresh_button.connect("clicked", self._on_refresh_clicked)
        actions.append(self._refresh_button)
        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        actions.append(self._spinner)
        self._footer = Gtk.Label(xalign=0)
        self._footer.add_css_class("muted")
        self._footer.set_hexpand(True)
        actions.append(self._footer)
        self.body.append(actions)

        self._render()
        self._refresh(force=True)

    def _tile(
        self,
        widget: Gtk.Widget | None,
        label: str,
        extra: Gtk.Widget | None = None,
    ) -> Gtk.Box:
        """Una celda del grid: caption arriba, instrumento y valor debajo."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        if label:
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("caption")
            caption.add_css_class("muted")
            box.append(caption)
        for child in (widget, extra):
            if child is not None:
                child.set_halign(Gtk.Align.START)
                box.append(child)
        return box

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._refresh(force=False)

    def redraw(self) -> None:
        # graficos.Sparkline no implementa el contrato redraw() de los
        # instrumentos T2: un queue_draw directo es el mismo repintado.
        self._set_spark_series()
        self._decode_spark.queue_draw()
        self._prefill_spark.queue_draw()
        self._mtp_dial.redraw()
        self._cache_ring.redraw()
        self._salud_lamp.redraw()
        self._gpu_lamp.redraw()
        for bar in self._headroom_bars:
            bar.redraw()

    def _set_spark_series(self) -> None:
        """Sparkline pide un color literal: resolver las claves de paleta
        en cada render para que el cambio de tema no deje las series grises."""
        self._decode_spark.set_data(list(self._decode_series), _color("accent"))
        self._prefill_spark.set_data(list(self._prefill_series), _color("blue"))

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing:
            return
        if not force and now - self._last_refresh < PERF_REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_refresh = now
        self._render()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        metrics_text = _http_text("/metrics")
        if metrics_text is None:
            GLib.idle_add(
                self._on_read_failed, f"el motor no responde en :{ENGINE_PORT}"
            )
            return
        metrics = parse_metrics(metrics_text)
        served = parse_served_context(_http_text("/v1/models") or "")
        slots = parse_slots(_http_text("/slots") or "")
        stdout, returncode, failure = _run_gufo_flash_status()
        guard = parse_guard(stdout, returncode, failure)

        prefill_source = (
            metrics.prompt_tokens_processed_total
            if metrics.prompt_tokens_processed_total is not None
            else metrics.prompt_tokens_total
        )
        self._prefill.update(prefill_source, time.monotonic())
        self._decode.update(metrics.tokens_predicted_total, time.monotonic())

        GLib.idle_add(self._on_read_done, metrics, served, slots, guard)

    def _on_read_done(
        self, metrics: Metrics, served: int | None, slots, guard: Guard
    ) -> bool:
        self._refreshing = False
        if served is None and slots and slots[0].n_ctx is not None:
            served = slots[0].n_ctx
        self._metrics = metrics
        self._served = served
        self._guard = guard
        if self._decode.last is not None:
            self._decode_series.append(self._decode.last)
        if self._prefill.last is not None:
            self._prefill_series.append(self._prefill.last)
        self._view = build_view(
            metrics=metrics,
            served_context=served,
            slots=slots,
            guard=guard,
            prefill_rate=self._prefill.last,
            decode_rate=self._decode.last,
            last_read=format_timestamp(time.time()),
        )
        self._render()
        return False

    def _on_read_failed(self, error: str) -> bool:
        self._refreshing = False
        self._view = stale_view(self._view, error)
        self._render()
        return False

    def _on_refresh_clicked(self, _button: Gtk.Button) -> None:
        self._refresh(force=True)

    # -- pintado -----------------------------------------------------------

    def _render(self) -> None:
        view = self._view
        guard = self._guard
        metrics = self._metrics

        self._warn_banner.set_title(view.warning or "")
        self._warn_banner.set_revealed(bool(view.warning))
        self._warn_banner.set_tooltip_text(
            "Pisos del guard de memoria: 15 GiB tras cargar el motor, "
            "12 GiB durante el trabajo."
        )
        self._err_banner.set_title(view.error or "")
        self._err_banner.set_revealed(bool(view.error))
        self._err_banner.set_tooltip_text(
            "Las métricas vienen sólo del motor Gufo (:11070); una lectura "
            "fallida conserva los últimos valores conocidos."
        )

        if view.stale:
            lamp_state = "stale"
        elif guard is not None and guard.engine_ready and not guard.error:
            lamp_state = "ok"
        else:
            lamp_state = "down"
        self._salud_lamp.set_data(lamp_state)
        self._salud_text.set_text(
            health_text(guard) if guard is not None else "sin respuesta :11070"
        )
        self._salud_text.get_parent().set_tooltip_text(
            "gufo-flash status: engine_ready y fase del guard."
        )

        served = self._served
        self._ctx_value.set_text(
            f"{served:,}".replace(",", ".") + " tok"
            if served is not None
            else "desconocido"
        )
        self._ctx_value.set_tooltip_text(
            "Contexto servido por /v1/models del motor (no la ventana "
            "configurada en ningún registro)."
        )

        processing = metrics.requests_processing if metrics is not None else None
        self._reqs_value.set_text(
            f"{processing:.0f}" if processing is not None else "—"
        )
        self._reqs_value.set_tooltip_text(
            "Pedidos en curso según /metrics del motor; «—» sin lectura."
        )

        self._decode_value.set_text(speed_text(self._decode.last))
        self._set_spark_series()
        self._decode_spark.set_tooltip_text(
            "Decode derivado de llamacpp:tokens_predicted_total entre dos "
            "lecturas; «sin datos recientes» hasta ver actividad."
        )
        self._prefill_value.set_text(speed_text(self._prefill.last))
        self._prefill_spark.set_tooltip_text(
            "Prefill derivado de los contadores de prompt entre dos lecturas."
        )

        self._mtp_dial.set_data(mtp_ratio(metrics), "blue")
        self._mtp_dial.set_tooltip_text(
            "Aceptación de especulación MTP, derivada de los contadores "
            "gufo_spec_* del motor."
        )

        cache = (
            cached_percentage(
                metrics.prompt_tokens_cached_total, metrics.prompt_tokens_total
            )
            if metrics is not None
            else None
        )
        self._cache_ring.set_data(cache, "accent")
        self._cache_ring.set_tooltip_text(
            "Caché de prompt: prompt_tokens_cached / prompt_tokens del motor."
        )

        kv = (
            kv_percentage(metrics.kv_cache_usage_ratio) if metrics is not None else None
        )
        self._kv_bar.set_fraction((kv or 0.0) / 100.0)
        self._kv_value.set_text(f"{kv:.0f} %" if kv is not None else "sin datos")
        self._kv_bar.set_tooltip_text(
            "Ventana KV en uso (llamacpp:kv_cache_usage_ratio)."
        )

        device_lost = metrics.device_lost_total if metrics is not None else None
        gpu_state = gpu_lamp_state(device_lost)
        self._gpu_lamp.set_data(gpu_state)
        self._gpu_label.set_text(
            {
                "ok": "GPU sana",
                "down": "GPU con pérdidas",
                "off": "GPU sin dato",
            }.get(gpu_state, "GPU sin dato")
        )
        self._gpu_label.set_tooltip_text(
            "Pérdidas de contexto GPU desde el arranque "
            "(gufo_device_lost_total): 0 es la buena noticia."
        )

        memory = self._snapshot.memory() if self._snapshot else {}
        rows = (
            headroom_rows(memory, guard)
            if guard is not None
            else (
                HeadroomRow("disponible", None, None, 122.0),
                HeadroomRow("conservadora", None, None, 122.0),
            )
        )
        for bar, row in zip(self._headroom_bars, rows, strict=True):
            bar.set_data(row)
            bar._value.set_text(_headroom_value_text(row))
            bar.set_tooltip_text(
                f"RAM {row.label} · el mínimo observado marca el piso del guard."
            )
        if guard is not None:
            mins = (
                f"mínimos observados: disponible "
                f"{guard.min_mem_available_gib:.1f} · conservadora "
                f"{guard.min_conservative_gib:.1f} GiB"
                if guard.min_mem_available_gib is not None
                else "mínimos observados: sin datos todavía"
            )
        else:
            mins = "mínimos observados: sin datos todavía"
        self._mem_mins.set_text(mins)

        self._footer.set_text(footer_text(view))
        self._refresh_button.set_sensitive(not self._refreshing)
        if self._refreshing:
            self._spinner.start()
            self._spinner.set_visible(True)
        else:
            self._spinner.stop()
            self._spinner.set_visible(False)
