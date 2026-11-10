"""Bloques de la página Home: el cockpit de MacacoView (diseño 07, T3).

Tres filas como el artboard, mapeadas a los widgets de ``instrumentos``:

* fila 1 — bloques de RAM (reserva rayada S9), banda de 15 minutos con
  marcadores, y a la derecha «Modelos locales» (S15, L30): interruptor
  maestro + uno por motor con la RAM que quedaría; Ctrl E sigue abriendo el
  cambio de motor;
* fila 2 — lanes decode/prefill con cursor compartido y valores actuales,
  anillos de caché y ventana KV, dial MTP, lámpara de GPU y pedidos;
* fila 3 — inventario compacto de ``local-ai-models`` con margen RAM
  (disponible / conservadora con sus mínimos) y la tarjeta de actualización
  de un solo estado (S6).

Toda decisión vive en ``home_logica`` y en los módulos ``*_logica`` que ya
existen; este widget sólo lee fuentes y pinta. Las lecturas (administrador,
motor Gufo en :11070, subproceso ``gufo-flash status``) corren en un único
worker compartido por la página — el hilo principal de GTK nunca se bloquea.

Acciones: el cambio de motor pasa por la MISMA confirmación y el MISMO
comando que el panel Motores (``confirm_text`` + ``local-ai-models
switch``); la actualización usa el MISMO diálogo y ``local-ai-models engine
update --yes --json``. Los atajos Ctrl E y Ctrl U los instala la página.
"""

# PyGObject no trae stubs propios; ver gui/typings/gi.
# pyright: reportAttributeAccessIssue=false

import threading
import time

from gi.repository import Adw, GLib, Gtk

from pc_ai_monitor import theme
from pc_ai_monitor.blocks import Block, home_logica, progreso
from pc_ai_monitor.blocks.actualizaciones_logica import sentence
from pc_ai_monitor.blocks.engine_logica import (
    CONFIRMATION_TEXT,
    CliResult,
    parse_engine_status,
    parse_update_result,
)
from pc_ai_monitor.blocks.historia_motor import (
    MotorHistory,
    Sample,
    band_segments,
    lane_points,
)
from pc_ai_monitor.blocks.motores import (
    CommandResult,
    _command_failure,
    _load_inventory,
    _run_manager,
    run_manager,
)
from pc_ai_monitor.blocks.rendimiento import _http_text, _run_gufo_flash_status
from pc_ai_monitor.blocks.rendimiento_logica import (
    SpeedTracker,
    format_timestamp,
    parse_guard,
    parse_metrics,
    parse_served_context,
)
from pc_ai_monitor.blocks.residente import (
    SWITCH_TIMEOUT_S as _RESIDENTE_SWITCH_TIMEOUT_S,
)
from pc_ai_monitor.blocks.residente_logica import (
    confirm_text,
    engine_view,
    headline,
    parse_resident,
)
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.widgets import instrumentos_logica as instrumentos
from pc_ai_monitor.widgets.base import Chart
from pc_ai_monitor.widgets.instrumentos import (
    Dial,
    Lamp,
    Lanes,
    MotorBand,
    RamBlocks,
    Ring,
    VersionRail,
)

REFRESH_INTERVAL_S = 5.0
STATUS_INTERVAL_S = 60.0
STATUS_TIMEOUT_S = 120
UPDATE_TIMEOUT_S = 900
SWITCH_TIMEOUT_S = _RESIDENTE_SWITCH_TIMEOUT_S
CTX_SCALE = home_logica.ENGINE_CTX["gufo"]


def _wall_clock(t: float) -> str:
    """«HH:MM» de pared para una muestra del reloj monotónico (eje de lanes)."""
    return instrumentos.wall_clock_hm(t + (time.time() - time.monotonic()))


def _caption(text: str, expand: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("section-head")
    if expand:
        label.set_hexpand(True)
    return label


class _HomePoller:
    """Sondeo compartido del cockpit: un worker cada ~5 s para toda la página.

    Reutiliza las mismas lecturas que Motores (``_run_manager`` /
    ``_load_inventory``) y el motor Gufo (``_http_text`` +
    ``_run_gufo_flash_status`` de ``blocks.rendimiento``). Los deltas de
    prefill/decode salen de ``SpeedTracker`` (S11: nunca un 0 inventado).
    """

    def __init__(self) -> None:
        self.state = home_logica.HomeState()
        self.history = MotorHistory()
        self._subs: list = []
        self._refreshing = False
        self._last_poll = 0.0
        self._last_status = 0.0
        self._prefill = SpeedTracker()
        self._decode = SpeedTracker()

    # -- suscripción -------------------------------------------------------

    def subscribe(self, callback) -> None:
        if callback not in self._subs:
            self._subs.append(callback)

    def unsubscribe(self, callback) -> None:
        if callback in self._subs:
            self._subs.remove(callback)

    def notify(self) -> None:
        for callback in tuple(self._subs):
            callback()

    # -- lectura -----------------------------------------------------------

    def refresh(self, snapshot: Snapshot, force: bool = False) -> None:
        now = time.monotonic()
        if self._refreshing:
            return
        if not force and now - self._last_poll < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_poll = now
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        # Residente (el administrador es la única autoridad).
        resident_result = _run_manager("resident")
        manager_ok = resident_result.failure is None and resident_result.returncode == 0
        info = parse_resident(resident_result.stdout) if manager_ok else None

        # Motor Gufo: métricas, contexto servido y guard del propio motor.
        metrics_text = _http_text("/metrics")
        metrics = parse_metrics(metrics_text) if metrics_text is not None else None
        served = None
        guard = None
        if metrics is not None:
            served = parse_served_context(_http_text("/v1/models") or "")
            guard = parse_guard(*_run_gufo_flash_status())

        # Inventario de modelos (misma ruta que el panel Motores).
        inventory, inventory_error = _load_inventory()

        # Estado de actualización: consulta de registro, con moderación.
        status = self.state.status
        available = self.state.available_label
        now = time.monotonic()
        if status is None or now - self._last_status >= STATUS_INTERVAL_S:
            self._last_status = now
            status_result = run_manager(("engine", "status", "--json"), STATUS_TIMEOUT_S)
            status = parse_engine_status(_as_cli_result(status_result))
            available = (
                home_logica.parse_available_version(status_result.stdout)
                if status_result.returncode == 0
                else None
            )

        # Deltas de contadores → tok/s. Como el gate _refreshing impide
        # solapar workers, acá no hay carrera (mismo argumento que
        # blocks/rendimiento.py).
        prefill_source = None
        if metrics is not None:
            prefill_source = (
                metrics.prompt_tokens_processed_total
                if metrics.prompt_tokens_processed_total is not None
                else metrics.prompt_tokens_total
            )
        self._prefill.update(prefill_source, time.monotonic())
        self._decode.update(
            metrics.tokens_predicted_total if metrics is not None else None,
            time.monotonic(),
        )

        # Muestra de historia (hueco honesto cuando el motor no habla).
        resident_value = info.get("resident") if info else None
        self.history.record(Sample(
            time.monotonic(),
            resident_value,
            self._decode.last,
            self._prefill.last,
            home_logica.kv_pct(metrics),
            home_logica.cache_pct(metrics),
        ))

        GLib.idle_add(self._publish, home_logica.HomeState(
            resident_info=info,
            manager_ok=manager_ok,
            metrics=metrics,
            served_context=served,
            guard=guard,
            inventory=inventory,
            inventory_error=inventory_error,
            status=status,
            available_label=available,
            decode=self._decode.last,
            prefill=self._prefill.last,
            down_engines=self.history.crash_engines(),
            last_read=format_timestamp(time.time()),
        ))

    def _publish(self, state: home_logica.HomeState) -> bool:
        # Los estados de acción viven más que un ciclo de sondeo.
        state.switching = self.state.switching
        state.updating = self.state.updating
        state.last_update_rc = self.state.last_update_rc
        state.update_message = self.state.update_message
        self.state = state
        self.notify()
        return False

    # -- acciones ----------------------------------------------------------

    def begin_switch(self, target: str) -> None:
        self.state.switching = target
        self.history.record_event("switch", f"cambio a {target.capitalize()} iniciado")
        self.notify()

    def end_switch(self, ok: bool, message: str, info: dict | None = None) -> None:
        self.state.switching = None
        if info is not None:
            self.state.resident_info = info
        if message == progreso.pl.STOPPED_MESSAGE:
            self.state.update_message = message  # «Detener»: neutral, not ✘
        else:
            self.state.update_message = ("✔ " if ok else "✘ ") + message
        self.notify()

    def begin_update(self) -> None:
        self.state.updating = True
        self.state.update_message = "Actualizando el motor…"
        self.history.record_event("update", "actualización del motor iniciada")
        self.notify()

    def end_update(self, returncode: int | None, message: str,
                   status=None) -> None:
        self.state.updating = False
        self.state.last_update_rc = returncode
        self.state.update_message = message
        if status is not None:
            self.state.status = status
        if returncode == 1:
            self.history.record_event("rollback", message)
        elif returncode == 0:
            self.history.record_event("update", message)
        self.notify()


POLLER = _HomePoller()


def _as_cli_result(result: CommandResult) -> CliResult:
    """Adapta la salida común del administrador a la lógica pura."""
    return CliResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        failure=result.failure,
    )


def _engine_in_flight(state) -> str | None:
    """El motor con una acción de motor en curso (cambio, encender, apagar)."""
    target = state.switching or progreso.switching_target()
    if target:
        return target
    for engine in home_logica.ENGINES:
        if progreso.HUB.running(f"engine:{engine}"):
            return engine
    return None


def _engine_locks(state) -> dict[str, str]:
    """Motivos visibles por motor cuando una actualización lo tiene parado."""
    locks: dict[str, str] = {}
    if progreso.HUB.running("update:engine-abito"):
        locks["abito"] = (
            "Bloqueado mientras se actualiza Abito: el actualizador lo para, "
            "compila y lo vuelve a arrancar."
        )
    if (
        state.updating
        or progreso.HUB.running("engine-update")
        or progreso.HUB.running("update:engine-gufo")
    ):
        locks["gufo"] = (
            "Bloqueado mientras se actualiza el motor Gufo: la actualización lo reinicia."
        )
    return locks


def start_switch(poller, target: str, *, anchor=None, retry=None) -> None:
    """``switch <target>`` desde Home con el progreso S14 (etapas del guard).

    Los nombres ``run_manager`` / ``_run_manager`` / ``_run_gufo_flash_status``
    se resuelven en este módulo en cada llamada: el guard de aislamiento de los
    tests los sustituye aquí.
    """
    if progreso.HUB.running("switch"):
        return
    poller.begin_switch(target)
    progreso.run_switch(
        target,
        lambda args, timeout, **kw: run_manager(args, timeout, **kw),
        lambda *args: _run_manager(*args),
        lambda: _run_gufo_flash_status(),
        lambda ok, message, info: poller.end_switch(ok, message, info),
        section="home",
        anchor=anchor,
        retry=retry,
        timeout=SWITCH_TIMEOUT_S,
    )


# ---------------------------------------------------------------------------
# Fila 1 · RAM + banda + cambio compacto
# ---------------------------------------------------------------------------


STOP_ABITO_BODY = "Es el motor base usado por Pi y el router. Confirmá para detenerlo."
STOP_GUFO_BODY = (
    "Se apaga Gufo sin volver a Abito: no queda ningún motor local y Pi va a la "
    "nube. Lo que esté respondiendo se corta. Volver a cargarlo libera ≈ 85 GiB "
    "de RAM y tarda ~90 s."
)


class LocalModelsCard(Gtk.Box):
    """«Modelos locales» (S15, diseño 08 tab 2): maestro + un interruptor por
    motor con lo que quedaría de RAM al encenderlo.

    Un interruptor nunca muestra un estado que el administrador no confirmó:
    al tocarlo se dispara la acción (o su confirmación) y vuelve enseguida a
    lo confirmado; el cambio real llega con la relectura de ``resident`` y la
    transición se ve en el bloque de progreso S14 de su fila.
    """

    def __init__(self, owner: "MotorRowBlock") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self._owner = owner
        self._syncing = False
        self._view: home_logica.LocalModelsView | None = None

        cap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cap.append(_caption("Modelos locales", expand=True))
        hint = Gtk.Label(label="un motor grande a la vez", xalign=1)
        hint.add_css_class("caption")
        hint.add_css_class("muted")
        cap.append(hint)
        self.append(cap)

        # Maestro
        master = Gtk.Grid(column_spacing=10, row_spacing=2)
        self._master_lamp = Lamp(10)
        self._master_lamp.set_valign(Gtk.Align.CENTER)
        master.attach(self._master_lamp, 0, 0, 1, 1)
        title = Gtk.Label(label="Modelos locales", xalign=0)
        title.add_css_class("model-name")
        master.attach(title, 1, 0, 1, 1)
        self._master = Gtk.Switch()
        self._master.set_valign(Gtk.Align.CENTER)
        self._master.set_halign(Gtk.Align.END)
        self._master.set_hexpand(True)
        self._master.set_tooltip_text(
            "Encendido: enciende Abito, el motor base de Pi. "
            "Apagado: apaga el motor residente."
        )
        self._master.connect("state-set", self._on_master)
        master.attach(self._master, 2, 0, 1, 2)
        self._master_status = Gtk.Label(xalign=0)
        self._master_status.add_css_class("caption")
        self._master_status.add_css_class("muted")
        self._master_status.set_wrap(True)
        master.attach(self._master_status, 1, 1, 1, 1)
        self._master_reason = _why()
        master.attach(self._master_reason, 1, 2, 2, 1)
        self.append(master)

        self._rows: dict[str, dict] = {}
        for engine in home_logica.ENGINES:
            self.append(Gtk.Separator())
            self._rows[engine] = self._build_row(engine)
            self.append(self._rows[engine]["box"])

    def _build_row(self, engine: str) -> dict:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        grid = Gtk.Grid(column_spacing=10, row_spacing=2)
        lamp = Lamp(10)
        lamp.set_valign(Gtk.Align.CENTER)
        grid.attach(lamp, 0, 0, 1, 1)
        names = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=engine.capitalize(), xalign=0)
        name.add_css_class("model-name")
        names.append(name)
        subtitle = Gtk.Label(label=home_logica.ENGINE_SUBTITLE[engine], xalign=0)
        subtitle.add_css_class("caption")
        subtitle.add_css_class("muted")
        subtitle.set_ellipsize(3)  # Pango.EllipsizeMode.END
        subtitle.set_hexpand(True)
        names.append(subtitle)
        grid.attach(names, 1, 0, 1, 1)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_halign(Gtk.Align.END)
        switch.update_property([Gtk.AccessibleProperty.LABEL], [engine.capitalize()])
        switch.connect("state-set", self._on_engine, engine)
        grid.attach(switch, 2, 0, 1, 2)
        status = Gtk.Label(xalign=0)
        status.add_css_class("caption")
        status.add_css_class("mono")
        status.add_css_class("muted")
        status.set_ellipsize(3)
        grid.attach(status, 1, 1, 1, 1)
        box.append(grid)

        # Lo que quedaría disponible si se enciende (con su reserva).
        projection = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar = Gtk.ProgressBar()
        bar.set_hexpand(True)
        bar.set_valign(Gtk.Align.CENTER)
        projection.append(bar)
        value = Gtk.Label(xalign=1)
        value.add_css_class("caption")
        value.add_css_class("num")
        projection.append(value)
        box.append(projection)

        reason = _why()
        box.append(reason)

        progress = progreso.ProgressInline(
            lambda a, e=engine: a.key == f"engine:{e}" or (a.key == "switch" and a.target == e),
            max_chars=52,
        )
        box.append(progress)
        return {
            "box": box, "lamp": lamp, "switch": switch, "status": status,
            "projection": projection, "bar": bar, "value": value,
            "reason": reason, "progress": progress,
        }

    # -- render --------------------------------------------------------------

    def render(self, view: home_logica.LocalModelsView, total_gib: float,
               running: str | None) -> None:
        self._view = view
        self._syncing = True
        try:
            self._master.set_active(view.master_active)
            self._master.set_state(view.master_active)
        finally:
            self._syncing = False
        self._master.set_sensitive(view.master_sensitive)
        self._master_lamp.set_data("stale" if running else view.master_lamp)
        self._master_status.set_text(view.master_status)
        _set_why(self._master_reason, view.master_reason)

        for engine_view_ in view.engines:
            row = self._rows[engine_view_.engine]
            self._syncing = True
            try:
                row["switch"].set_active(engine_view_.active)
                row["switch"].set_state(engine_view_.active)
            finally:
                self._syncing = False
            row["switch"].set_sensitive(engine_view_.sensitive)
            row["lamp"].set_data(
                "stale" if running == engine_view_.engine else engine_view_.lamp
            )
            row["status"].set_text(engine_view_.status)
            _set_why(row["reason"], engine_view_.reason)

            value = engine_view_.projection_gib
            row["projection"].set_visible(value is not None)
            if value is not None:
                row["bar"].set_fraction(max(0.0, min(1.0, value / max(total_gib, 1.0))))
                for css in ("bar-blue", "bar-warning"):
                    row["bar"].remove_css_class(css)
                row["bar"].add_css_class(
                    "bar-warning" if engine_view_.projection_low else "bar-blue"
                )
                row["value"].set_text(f"≈ {value:.1f} GiB")
                for css in ("tier-warn",):
                    row["value"].remove_css_class(css)
                if engine_view_.projection_low:
                    row["value"].add_css_class("tier-warn")
                tip = (
                    f"RAM disponible si encendés {engine_view_.name}, con su reserva "
                    f"(≈ {home_logica.RESERVATION_GIB[engine_view_.engine]:.0f} GiB)."
                )
                if engine_view_.projection_low:
                    tip += (
                        " Quedarían menos de 20 GiB libres: el guard del motor "
                        "decide si lo admite."
                    )
                row["projection"].set_tooltip_text(tip)

    # -- events --------------------------------------------------------------

    def _on_master(self, _switch, want_on: bool) -> bool:
        if self._syncing:
            return False
        info = self._owner._poller.state.resident_info
        self._owner.run_plan(home_logica.master_plan(info, want_on))
        GLib.idle_add(self._owner._render)
        return True  # el estado lo pone la relectura del administrador

    def _on_engine(self, _switch, want_on: bool, engine: str) -> bool:
        if self._syncing:
            return False
        info = self._owner._poller.state.resident_info
        self._owner.run_plan(home_logica.toggle_plan(info, engine, want_on))
        GLib.idle_add(self._owner._render)
        return True


def _why() -> Gtk.Label:
    label = Gtk.Label(xalign=0)
    label.add_css_class("why")
    label.set_wrap(True)
    label.set_max_width_chars(52)
    label.set_visible(False)
    return label


def _set_why(label: Gtk.Label, text: str) -> None:
    label.set_text(text)
    label.set_visible(bool(text))


class MotorRowBlock(Block):
    """Fila 1: RAM, banda de 15 minutos e interruptores de modelos locales."""

    title = ""
    frame = False

    def __init__(self, poller=None) -> None:
        super().__init__()
        self._poller = poller or POLLER
        self._snapshot: Snapshot | None = None

        # Grid, not a horizontal Box: a Box asks its children how wide they
        # want to be *for its height*, and the wrapping texts of the switches
        # card (an error message) then claimed more than the window. A Grid
        # settles the widths first and the labels wrap inside them.
        row = Gtk.Grid(column_spacing=12)
        row.set_hexpand(True)

        # — tarjeta izquierda: RAM + banda —
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.add_css_class("card")
        left.set_hexpand(True)
        cap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cap.append(_caption("RAM", expand=True))
        self._ram_total = Gtk.Label(xalign=1)
        self._ram_total.add_css_class("mono")
        self._ram_total.add_css_class("muted")
        cap.append(self._ram_total)
        left.append(cap)

        self._ram = RamBlocks(width=560, height=78)
        left.append(self._ram)

        cap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cap.append(_caption("Motor"))
        self._headline = Gtk.Label(xalign=0)
        self._headline.add_css_class("heading")
        cap.append(self._headline)
        left.append(cap)

        self._band = MotorBand(width=560, height=66)
        left.append(self._band)
        row.attach(left, 0, 0, 1, 1)

        # — derecha: «Modelos locales» (S15, L30: reemplaza las mini tarjetas
        #    y la píldora «Cambiar a … Ctrl E»; Ctrl E sigue funcionando) —
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._models = LocalModelsCard(self)
        self._models.set_size_request(430, -1)
        right.append(self._models)

        row.attach(right, 1, 0, 1, 1)
        self.body.append(row)

        self._poller.subscribe(self._on_state)
        self.connect("destroy", self._on_destroy)
        progreso.HUB.subscribe_widget(self._render_models)
        self._render()

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._poller.refresh(snapshot)

    def redraw(self) -> None:
        self._ram.redraw()
        self._band.redraw()

    def _on_state(self) -> None:
        self._render()

    def _on_destroy(self, *_args) -> None:
        self._poller.unsubscribe(self._on_state)

    # -- render ------------------------------------------------------------

    def _render(self) -> None:
        state = self._poller.state
        info = state.resident_info
        resident = info.get("resident") if info else None

        self._ram.set_data(home_logica.ram_blocks(self._snapshot or Snapshot(), resident))
        memory = self._snapshot.memory() if self._snapshot else {}
        total = home_logica._optional(memory.get("total_gib"))
        self._ram_total.set_text(f"{total:.0f} GiB" if total is not None else "")
        self._headline.set_text(headline(info, state.switching))

        self._band.set_data(
            band_segments(self._poller.history.samples()),
            self._poller.history.markers(),
            time.monotonic(),
        )

        self._render_models()

    def _render_models(self) -> None:
        """Interruptores: también se repintan con cada cambio del hub S14,
        para que una acción empezada en otra página los bloquee al instante."""
        state = self._poller.state
        info = state.resident_info
        memory = self._snapshot.memory() if self._snapshot else {}
        total = home_logica._optional(memory.get("total_gib"))
        running = _engine_in_flight(state)
        self._models.render(
            home_logica.local_models(
                info, memory, self._snapshot.models() if self._snapshot else [],
                _engine_locks(state), running,
            ),
            total or home_logica.DEFAULT_TOTAL_GIB,
            running,
        )

    # -- acción: cambiar de motor (misma ruta que Motores) -----------------

    def request_switch(self) -> None:
        state = self._poller.state
        if state.switching is not None or state.updating or progreso.HUB.running("switch"):
            return
        info = state.resident_info
        target = home_logica.switch_target(info.get("resident") if info else None)
        view = engine_view(info, target, None)
        if not view["enabled"]:
            return  # el motivo ya está en pantalla
        self._confirm_switch(target)

    def _confirm_switch(self, target: str) -> None:
        """Mismo diálogo y mismo texto que el panel Motores (residente.py)."""
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

        def _on_response(_dialog, response: str) -> None:
            if response == "switch":
                self._start_switch(target)

        dialog.connect("response", _on_response)
        dialog.present()

    def _start_switch(self, target: str) -> None:
        start_switch(self._poller, target,
                     anchor=self._models._rows[target]["progress"],
                     retry=lambda: self._confirm_switch(target))
        self._render()

    # -- interruptores (S15): ensure / stop / gufo-flash stop --no-restore ----

    def run_plan(self, plan: home_logica.Plan) -> None:
        if plan.kind == "none" or _engine_in_flight(self._poller.state):
            return
        if plan.kind == "switch":
            if plan.confirm:
                self._confirm_switch(plan.target)
            else:
                self._start_switch(plan.target)
        elif plan.kind == "ensure":
            self._run_engine(plan.target, "ensure")
        elif plan.kind == "stop":
            self._confirm_stop(
                f"¿Apagar {plan.target}?", STOP_ABITO_BODY,
                lambda: self._run_engine(plan.target, "stop"),
            )
        elif plan.kind == "stop_gufo":
            self._confirm_stop(
                "¿Apagar Gufo?", STOP_GUFO_BODY, lambda: self._run_engine("gufo", "stop")
            )

    def _confirm_stop(self, title: str, body: str, accept) -> None:
        """Mismo estilo de diálogo que «¿Apagar abito?» de Motores."""
        root = self.get_root()
        dialog = Gtk.MessageDialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
            # GTK 4: propiedad «secondary-text» (no format_secondary_text).
            secondary_text=body,
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apagar", Gtk.ResponseType.ACCEPT)

        def _on_response(current: Gtk.MessageDialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT:
                accept()

        dialog.connect("response", _on_response)
        dialog.present()

    def _run_engine(self, engine: str, action: str) -> None:
        """``ensure abito`` / ``stop abito`` / ``gufo-flash stop --no-restore``."""
        from pc_ai_monitor.blocks import motores

        if progreso.HUB.running(f"engine:{engine}"):
            return
        plan_on = action == "ensure"
        activity = progreso.begin(progreso.pl.engine_action(
            engine, action, section="home",
            anchor=self._models._rows[engine]["progress"],
            retry=lambda: self.run_plan(
                home_logica.toggle_plan(self._poller.state.resident_info, engine, plan_on)
            ),
        ))
        if engine == "gufo" and action == "stop":
            activity.limit_s = progreso.pl.STOP_LIMIT_S
            activity.note = (
                "Sin porcentaje: gufo-flash stop solo avisa al terminar. "
                "Abito no vuelve solo."
            )
        # S21: «Encender» Abito is a load: «Detener» ends it (stop abito).
        stop = progreso.attach_stop(activity, engine) if action == "ensure" else None
        self._render()

        def _worker() -> None:
            if engine == "gufo":
                result = motores.run_gufo_flash(("stop", "--no-restore"), 300)
            elif stop is not None:
                result = _run_manager(action, engine, stop=stop)
            else:
                result = _run_manager(action, engine)
            stop_result = None
            if result.failure == "stopped":
                stop_result = progreso.run_stop_route(engine, _run_manager)
            after = _run_manager("resident")
            info = parse_resident(after.stdout) if after.returncode == 0 else None
            if stop_result is not None:
                GLib.idle_add(self._on_engine_stopped, activity, engine, stop_result, info)
            else:
                GLib.idle_add(self._on_engine_done, activity, engine, action, result, info)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_engine_stopped(self, activity, engine: str, stop_result: CommandResult,
                           info: dict | None) -> bool:
        """«Detener» ended the load: the switches follow the reread state."""
        progreso.finish_stopped_load(activity, engine, stop_result, info)
        if info is not None:
            self._poller.state.resident_info = info
        self._render()
        return False

    def _on_engine_done(self, activity, engine: str, action: str,
                        result: CommandResult, info: dict | None) -> bool:
        name = engine.capitalize()
        resident = info.get("resident") if info else None
        failure = _command_failure(result, action, engine)
        if result.returncode != 0:
            ok, title, detail = False, (
                f"No se encendió {name}" if action == "ensure" else f"No se apagó {name}"
            ), failure
        elif info is None:
            ok, title, detail = False, f"{name}: estado sin verificar", (
                "La orden terminó, pero no se pudo leer el estado real."
            )
        elif (resident == engine) == (action == "ensure"):
            ok, title, detail = True, (
                f"{name} encendido" if action == "ensure" else f"{name} apagado"
            ), ""
        else:
            ok, title, detail = False, (
                f"No se encendió {name}" if action == "ensure" else f"No se apagó {name}"
            ), "La orden terminó, pero el administrador informa otro estado."
        log = "" if ok else (result.stderr or result.stdout or "").strip()
        progreso.finish(activity, ok, title, detail, log)
        if info is not None:
            self._poller.state.resident_info = info
        self._render()
        return False


# ---------------------------------------------------------------------------
# Fila 2 · lanes decode/prefill + instrumentos
# ---------------------------------------------------------------------------


class RendimientoRowBlock(Block):
    """Fila 2: lanes con cursor compartido, anillos, dial MTP y pedidos."""

    title = ""
    frame = False

    def __init__(self, poller=None) -> None:
        super().__init__()
        self._poller = poller or POLLER

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)

        # — lanes con cursor compartido; los valores actuales van como
        #    números grandes al final de cada lane (artboard) —
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.add_css_class("card")
        left.set_hexpand(True)

        self._freshness = Gtk.Label(xalign=1)
        self._freshness.add_css_class("caption")
        self._freshness.add_css_class("muted")
        left.append(self._freshness)

        self._lanes = Lanes(width=560, lane_height=66, clock=_wall_clock)
        left.append(self._lanes)
        row.append(left)

        # — instrumentos ahora —
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.add_css_class("card")
        self._cache_ring = Ring(size=52, thickness=8)
        self._kv_ring = Ring(size=52, thickness=8)
        self._mtp_dial = Dial(size=50)
        self._gpu_lamp = Lamp(12)
        self._gpu_label = Gtk.Label(xalign=0)
        self._gpu_label.add_css_class("caption")

        tiles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tiles.set_homogeneous(True)
        tiles.append(_tile(self._cache_ring, "Caché prompt"))
        tiles.append(_tile(self._kv_ring, "Ventana KV"))
        right.append(tiles)

        trio = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        trio.set_homogeneous(True)
        trio.append(_tile(self._mtp_dial, "MTP"))
        gpu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        gpu.set_valign(Gtk.Align.CENTER)
        gpu.append(self._gpu_lamp)
        gpu.append(self._gpu_label)
        trio.append(gpu)
        self._reqs_value = Gtk.Label(label="—")
        self._reqs_value.add_css_class("heading")
        trio.append(_tile(self._reqs_value, "Pedidos"))
        right.append(trio)

        row.append(right)
        self.body.append(row)

        self._poller.subscribe(self._on_state)
        self.connect("destroy", self._on_destroy)
        self._render()

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._poller.refresh(snapshot)

    def redraw(self) -> None:
        self._lanes.redraw()
        self._cache_ring.redraw()
        self._kv_ring.redraw()
        self._mtp_dial.redraw()

    def _on_state(self) -> None:
        self._render()

    def _on_destroy(self, *_args) -> None:
        self._poller.unsubscribe(self._on_state)

    # -- render ------------------------------------------------------------

    def _lane(self, label: str, color: str, points, high: float) -> instrumentos.Lane:
        return instrumentos.Lane(
            label=label, color=color,
            points=tuple(points), low=0.0, high=high, stale_tail=True,
            unit="tok/s",
        )

    def _render(self) -> None:
        state = self._poller.state
        history = self._poller.history

        self._freshness.set_text(state.last_read)

        samples = history.samples()
        decode_high = _lane_high(lane_points(samples, "decode"))
        prefill_high = _lane_high(lane_points(samples, "prefill"))
        self._lanes.set_data((
            self._lane("decode", "accent", lane_points(samples, "decode"), decode_high),
            self._lane("prefill", "blue", lane_points(samples, "prefill"), prefill_high),
        ))

        metrics = state.metrics
        self._cache_ring.set_data(home_logica.cache_pct(metrics), "accent")
        self._kv_ring.set_data(home_logica.kv_pct(metrics), "blue")
        self._mtp_dial.set_data(home_logica.mtp_ratio(metrics))

        device_lost = metrics.device_lost_total if metrics is not None else None
        gpu_state = home_logica.gpu_lamp_state(device_lost)
        self._gpu_lamp.set_data(gpu_state)
        self._gpu_label.set_text({
            "ok": "GPU sana",
            "down": "GPU con pérdidas",
            "off": "GPU sin dato",
        }.get(gpu_state, "GPU sin dato"))
        self._gpu_label.set_tooltip_text(
            "Pérdidas de contexto GPU desde el arranque (gufo_device_lost_total): "
            "0 es la buena noticia."
        )

        requests = metrics.requests_processing if metrics is not None else None
        self._reqs_value.set_text(
            f"{requests:.0f}" if requests is not None else "—"
        )
        self._reqs_value.set_tooltip_text(
            "Pedidos en curso según /metrics del motor; «—» sin lectura."
        )


def _lane_high(points) -> float:
    """Techo del lane: 1.2× el máximo visto; piso 10 tok/s para no explotar."""
    values = [value for _t, value in points if value is not None]
    return max(values + [10.0]) * 1.2


def _tile(widget: Gtk.Widget, label: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_valign(Gtk.Align.CENTER)
    box.append(widget)
    caption = Gtk.Label(label=label)
    caption.add_css_class("caption")
    caption.add_css_class("muted")
    box.append(caption)
    return box


# ---------------------------------------------------------------------------
# Fila 3 · inventario + margen RAM + tarjeta de actualización
# ---------------------------------------------------------------------------


class _HeadroomBar(Chart):
    """Barra de margen RAM: escala 0–total, mínimo observado marcado."""

    def __init__(self, width: int = 240, height: int = 14) -> None:
        super().__init__(width, height)
        self._row = None

    def set_data(self, row) -> None:
        self._row = row
        self.queue_draw()

    def redraw(self) -> None:
        self.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:
        palette = theme.active()
        from pc_ai_monitor.widgets.instrumentos import round_rect

        cr.set_source_rgba(*theme.rgba(palette.subtle))
        round_rect(cr, 0, 2, width, height - 4, 5)
        cr.fill()

        if self._row is None or self._row.value_gib is None:
            return
        row = self._row
        scale = max(1e-9, row.total_gib)

        if row.min_gib is not None:
            min_x = min(width, max(0.0, row.min_gib / scale * width))
            # Zona de riesgo hasta el mínimo + marca del mínimo observado.
            cr.set_source_rgba(*theme.rgba(palette.error, 0.22))
            round_rect(cr, 0, 2, min_x, height - 4, 5)
            cr.fill()
            cr.set_source_rgba(*theme.rgba(palette.text))
            cr.rectangle(min_x - 1, 0, 2, height)
            cr.fill()

        value_w = min(width, max(0.0, row.value_gib / scale * width))
        fill_color = palette.error if row.low else palette.green
        cr.set_source_rgba(*theme.rgba(fill_color, 0.85))
        round_rect(cr, 0, 2, value_w, height - 4, 5)
        cr.fill()


class InventarioRowBlock(Block):
    """Fila 3 izquierda: inventario compacto + margen RAM con mínimos."""

    title = ""
    frame = False

    def __init__(self, poller=None) -> None:
        super().__init__()
        self._poller = poller or POLLER
        self._snapshot: Snapshot | None = None
        self._running: dict[str, str] = {}
        self._rows: list[dict] = []

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.set_hexpand(True)
        card.append(_caption("Motores locales"))

        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.append(self._rows_box)

        card.append(Gtk.Separator())

        cap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cap.append(_caption("Margen RAM", expand=True))
        self._mem_head = Gtk.Label(xalign=1)
        self._mem_head.add_css_class("mono")
        self._mem_head.add_css_class("muted")
        cap.append(self._mem_head)
        card.append(cap)

        self._headroom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.append(self._headroom_box)
        self._headroom_bars: list[tuple[_HeadroomBar, Gtk.Label]] = []
        for label in ("disponible", "conservadora"):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("caption")
            caption.add_css_class("muted")
            caption.set_size_request(82, -1)
            line.append(caption)
            bar = _HeadroomBar(width=240, height=12)
            bar.set_hexpand(True)
            line.append(bar)
            value = Gtk.Label(xalign=1)
            value.add_css_class("mono")
            line.append(value)
            self._headroom_box.append(line)
            self._headroom_bars.append((bar, value))

        self.body.append(card)

        self._poller.subscribe(self._on_state)
        self.connect("destroy", self._on_destroy)
        self._render()

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._poller.refresh(snapshot)

    def redraw(self) -> None:
        for bar, _value in self._headroom_bars:
            bar.redraw()

    def _on_state(self) -> None:
        self._render()

    def _on_destroy(self, *_args) -> None:
        self._poller.unsubscribe(self._on_state)

    # -- render ------------------------------------------------------------

    def _render(self) -> None:
        state = self._poller.state
        memory = self._snapshot.memory() if self._snapshot else {}
        self._mem_head.set_text(home_logica.mem_head(memory))
        for (bar, value), row in zip(
            self._headroom_bars,
            home_logica.headroom_rows(memory, state.guard),
            strict=True,
        ):
            bar.set_data(row)
            value.set_text(_headroom_value_text(row))
            bar.set_tooltip_text(_headroom_tooltip(row))

        self._render_rows(state)

    def _render_rows(self, state) -> None:
        incoming = state.inventory or []
        manager_ok = state.manager_ok and not state.inventory_error

        # Filas nuevas o retiradas (los nombres mandan).
        incoming_names = [str(model.get("model", "")) for model in incoming]
        for row in tuple(self._rows):
            if row["name"] not in incoming_names:
                self._rows_box.remove(row["widget"])
                self._rows.remove(row)
        for model in incoming:
            name = str(model.get("model", ""))
            row = self._find_row(name)
            if row is None:
                row = self._build_row(name)
                self._rows.append(row)
                self._rows_box.append(row["widget"])
            self._update_row(row, model, manager_ok)

        if state.inventory_error:
            self._render_error_row(state.inventory_error)
        elif not incoming and manager_ok:
            self._render_error_row("sin modelos informados por el administrador")

    def _find_row(self, name: str) -> dict | None:
        return next((row for row in self._rows if row["name"] == name), None)

    def _build_row(self, name: str) -> dict:
        # Orden del artboard (07, fila 3): lámpara, nombre, estado, barra de
        # contexto (~40 % de la fila), puerto y acción a la derecha; debajo,
        # el motivo cuando la acción está apagada (S16).
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.append(widget)
        lamp = Lamp(10)
        widget.append(lamp)
        name_label = Gtk.Label(label=name, xalign=0)
        name_label.add_css_class("row-name")
        widget.append(name_label)
        state_label = Gtk.Label(xalign=0)
        state_label.add_css_class("caption")
        state_label.add_css_class("muted")
        widget.append(state_label)
        ctx_bar = Gtk.ProgressBar()
        ctx_bar.set_size_request(330, -1)
        ctx_bar.set_show_text(False)
        ctx_bar.set_valign(Gtk.Align.CENTER)
        # Azul serie como el artboard (no el acento): ver BASE_CSS.
        ctx_bar.add_css_class("inv-ctx")
        widget.append(ctx_bar)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        widget.append(spacer)
        port = Gtk.Label(xalign=1)
        port.add_css_class("mono")
        port.add_css_class("muted")
        widget.append(port)
        power = Gtk.Button()
        power.add_css_class("flat")
        power.connect("clicked", self._on_power, name)
        widget.append(power)
        why = _why()
        why.set_max_width_chars(80)
        outer.append(why)
        return {
            "name": name, "widget": outer, "why": why, "lamp": lamp,
            "name_label": name_label, "state": state_label,
            "ctx": ctx_bar, "port": port,
            "power": power, "reason": "",
        }

    def _update_row(self, row: dict, model: dict, manager_ok: bool) -> None:
        view = home_logica.inventory_row(
            model,
            manager_ok=manager_ok,
            action_running=self._running.get(row["name"]) is not None,
        )
        row["lamp"].set_data(view["lamp"])
        row["state"].set_text(view["state"])
        row["ctx"].set_fraction(view["fraction"])
        port = view["port"]
        row["port"].set_text(f":{port}" if port is not None else "")
        row["power"].set_icon_name(
            "system-shutdown-symbolic" if view["healthy"] else "media-playback-start-symbolic"
        )
        running = row["name"] in self._running
        blocked = view["blocked"] or running
        row["power"].set_sensitive(not blocked)
        verb = "Apagar" if view["healthy"] else "Encender"
        if not blocked:
            why = ""
        elif running:
            why = f"«{verb}»: hay una orden en curso sobre {row['name']}; esperá a que termine."
        elif not manager_ok:
            why = f"«{verb}»: local-ai-models no respondió; no se actúa sobre un estado viejo."
        else:
            why = f"«{verb}»: {view['reason'] or 'no disponible'}."
        _set_why(row["why"], why)
        row["power"].set_tooltip_text(
            f"{'Apagar' if view['healthy'] else 'Encender'} {row['name']}"
            + (f" (bloqueado: {view['reason']})" if view["reason"] else "")
        )
        row["widget"].set_tooltip_text(
            f"{row['name']} · :{port} · ctx {view['ctx']} · {view['state']}"
            + (f" · {view['reason']}" if view["reason"] else "")
        )
        row["reason"] = view["reason"]

    def _render_error_row(self, message: str) -> None:
        row = self._find_row("—")
        if row is None:
            row = self._build_row("—")
            row["power"].set_visible(False)
            row["ctx"].set_visible(False)
            row["lamp"].set_visible(False)
            self._rows.append(row)
            self._rows_box.append(row["widget"])
        row["state"].set_text(message)
        row["port"].set_text("")

    # -- acción: encender / apagar (misma ruta que Motores) ----------------

    def _on_power(self, _button, name: str) -> None:
        row = self._find_row(name)
        if row is None or name in self._running:
            return
        healthy = row["state"].get_text() == "encendido"
        action = "stop" if healthy else "ensure"
        if name == "abito" and healthy:
            self._confirm_resident_stop(name)
            return
        self._run_action(name, action)

    def _confirm_resident_stop(self, name: str) -> None:
        """Misma confirmación que el panel Motores para apagar a Abito."""
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"¿Apagar {name}?",
            # GTK 4: propiedad «secondary-text» (no format_secondary_text).
            secondary_text=(
                "Es el motor base usado por Pi y el router. Confirmá para detenerlo."
            ),
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apagar", Gtk.ResponseType.ACCEPT)

        def _on_response(current: Gtk.MessageDialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT:
                self._run_action(name, "stop")

        dialog.connect("response", _on_response)
        dialog.present()

    def _run_action(self, name: str, action: str) -> None:
        self._running[name] = action
        self._render_rows(self._poller.state)

        def _worker() -> None:
            result = _run_manager(action, name)
            inventory, inventory_error = _load_inventory()
            GLib.idle_add(self._on_action_done, name, action, result, inventory, inventory_error)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_action_done(self, name, action, result, inventory, inventory_error) -> bool:
        self._running.pop(name, None)
        if inventory is not None:
            self._poller.state.inventory = inventory
            message = self._action_message(name, action, result, inventory)
        else:
            message = _command_failure(result, action, name) or (
                f"{name}: la orden terminó, pero no se pudo verificar el estado real."
            )
            if inventory_error:
                self._poller.state.inventory_error = inventory_error
        self._poller.state.update_message = message
        self._render_rows(self._poller.state)
        self._render()
        return False

    def _action_message(self, name: str, action: str, result: CommandResult,
                        inventory: list[dict]) -> str:
        if result.returncode != 0:
            return _command_failure(result, action, name)
        actual = next((item for item in inventory if item.get("model") == name), None)
        healthy = bool(actual and actual.get("healthy", False))
        if action == "ensure" and healthy:
            return f"{name}: encendido y saludable."
        if action == "ensure":
            return f"{name}: la orden terminó, pero el modelo no está saludable."
        if action == "stop" and not healthy:
            return f"{name}: apagado."
        return f"{name}: la orden terminó, pero todavía está saludable."


def _headroom_value_text(row) -> str:
    if row.value_gib is None:
        return "sin datos"
    text = f"{row.value_gib:.1f}"
    if row.min_gib is not None:
        text += f" / mín {row.min_gib:.0f} GiB"
    return text


def _headroom_tooltip(row) -> str:
    text = (
        f"RAM {row.label} · escala 0–{row.total_gib:.0f} GiB"
        if row.value_gib is not None
        else f"RAM {row.label} · sin datos (el guard no respondió)"
    )
    if row.min_gib is not None:
        text += f" · mínimo observado {row.min_gib:.1f} GiB (piso fijo del guard: 15 GiB tras cargar, 12 en trabajo)"
    return text


class UpdateCardBlock(Block):
    """Fila 3 derecha: la tarjeta de actualización de un solo estado (S6)."""

    title = ""
    frame = False

    def __init__(self, poller=None) -> None:
        super().__init__()
        self._poller = poller or POLLER
        self._updating = False

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.append(_caption("Actualización del motor"))

        self._pill = Gtk.Label(xalign=0)
        self._pill.add_css_class("vpill")
        self._pill.set_wrap(True)
        card.append(self._pill)

        # El rail flota en el espacio vertical de la tarjeta (artboard:
        # «space-between»); sin vexpand: dentro de un viewport el espacio
        # sobrante de la página le inflaría la tarjeta (defecto 4).
        self._rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._rail_box.set_valign(Gtk.Align.CENTER)
        self._rail = VersionRail(width=230, height=56)
        self._rail_box.append(self._rail)
        card.append(self._rail_box)

        self._button = Gtk.Button()
        self._button.add_css_class("suggested-action")
        # Ancho natural (defecto 2 del pase visual 2), no una barra completa.
        self._button.set_halign(Gtk.Align.START)
        self._button.connect("clicked", lambda _b: self.request_update())
        card.append(self._button)

        # S16: por qué el botón está apagado, visible bajo él.
        self._why = _why()
        self._why.set_max_width_chars(30)
        card.append(self._why)

        self._message = Gtk.Label(xalign=0)
        self._message.add_css_class("muted")
        self._message.add_css_class("caption")
        self._message.set_wrap(True)
        self._message.set_visible(False)
        card.append(self._message)

        # S14: el progreso de la actualización vive en esta tarjeta.
        # Columna de 276 px: textos estrechos para no ensancharla.
        self._progress = progreso.ProgressInline("engine-update", max_chars=28)
        card.append(self._progress)

        self.body.append(card)

        self._poller.subscribe(self._on_state)
        self.connect("destroy", self._on_destroy)
        self._render()

    # -- ciclo -------------------------------------------------------------

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._poller.refresh(snapshot)

    def redraw(self) -> None:
        self._rail.redraw()

    def _on_state(self) -> None:
        self._render()

    def _on_destroy(self, *_args) -> None:
        self._poller.unsubscribe(self._on_state)

    # -- render ------------------------------------------------------------

    def _render(self) -> None:
        state = self._poller.state
        card = home_logica.update_card(
            state.status, state.available_label,
            updating=state.updating, last_update_rc=state.last_update_rc,
        )
        self._pill.set_text(card.pill)
        self._pill.set_tooltip_text(card.pill_tooltip)
        for key in ("vpill-ok", "vpill-warn", "vpill-acc"):
            self._pill.remove_css_class(key)
        if card.pill_class:
            self._pill.add_css_class(f"vpill-{card.pill_class}")

        self._rail.set_data(card.from_label, card.to_label, card.rail_status)
        self._rail.set_tooltip_text(card.rail_tooltip)

        self._button.set_label(card.button_label)
        enabled = card.button_enabled and not state.updating
        self._button.set_sensitive(enabled)
        self._button.set_tooltip_text(card.button_tooltip + " (Ctrl U)")
        # En curso lo explica el bloque de progreso; si no, el motivo real.
        running = state.updating or progreso.HUB.running("engine-update")
        _set_why(self._why, "" if enabled or running else sentence(card.button_tooltip))

        # Mientras el bloque de progreso muestra la acción, el mensaje suelto
        # sobraría (dice lo mismo).
        self._message.set_text(state.update_message)
        self._message.set_visible(
            bool(state.update_message) and not self._progress.get_visible()
        )

    # -- acción: actualizar (misma ruta que Motores) -----------------------

    def request_update(self) -> None:
        state = self._poller.state
        if state.updating or state.status is None:
            return
        if not state.status.updatable or state.status.error:
            return
        self._confirm_update()

    def _confirm_update(self) -> None:
        """Mismo diálogo y mismo texto que el panel Motores (engine.py)."""
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="¿Actualizar el motor?",
            # GTK 4: propiedad «secondary-text» (no format_secondary_text).
            secondary_text=CONFIRMATION_TEXT,
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Actualizar", Gtk.ResponseType.ACCEPT)

        def _on_response(current: Gtk.MessageDialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT:
                self._begin_update()

        dialog.connect("response", _on_response)
        dialog.present()

    def _begin_update(self) -> None:
        if progreso.HUB.running("engine-update"):
            return
        self._poller.begin_update()
        activity = progreso.begin(progreso.pl.engine_update_action(
            section="home", anchor=self._progress, retry=self.request_update,
        ))

        def _worker() -> None:
            result = run_manager(("engine", "update", "--yes", "--json"), UPDATE_TIMEOUT_S)
            # El contrato pide releer el estado real en vez de asumir.
            status = run_manager(("engine", "status", "--json"), STATUS_TIMEOUT_S)
            message = parse_update_result(_as_cli_result(result))
            parsed = parse_engine_status(_as_cli_result(status))
            GLib.idle_add(self._on_update_done, result.returncode, message, parsed,
                          activity, (result.stderr or result.stdout or "").strip())

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_done(self, returncode: int | None, message: str, status,
                        activity=None, log: str = "") -> bool:
        if activity is not None:
            progreso.finish(
                activity, returncode == 0,
                "Motor actualizado" if returncode == 0 else (
                    "Actualización revertida" if returncode == 1
                    else "No se actualizó el motor"
                ),
                message, log,
            )
        self._poller.end_update(returncode, message, status)
        return False
