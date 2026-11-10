"""Pure progress model for long actions (S14): no GTK, no subprocess.

Every long action (update, start/stop a model, switch engine) is an
``Activity``: it starts indeterminate and becomes richer only with real
evidence. The honesty rule of design 08:

* a percentage exists only when the source prints a real number — today the
  «[ NN%]» lines cmake prints while ``update engine-abito --build`` compiles
  llama.cpp (``BuildProgress``);
* otherwise a stage «N de M» when the stage is observable — the phase
  ``gufo-flash status`` reports while Gufo loads (``SwitchStages``);
* otherwise an indeterminate bar with the elapsed time, the usual duration
  and the limit the source declares. Time is never turned into a percentage.

Actions end in success or error (reason, detail, «Reintentar»). There is no
«Cancelar» (L28): cutting a command midway leaves the engine off without
rolling back. The one exception is a model *load* (S21): ``ensure abito`` and
``switch gufo`` offer «Detener», which ends the command and stops the engine
on purpose, so nothing stays loaded; that outcome is ``STOPPED``, a neutral
end that is neither success nor error and leaves the header at once.

``ActivityHub`` keeps the activities of the whole app so the header
indicator can show the current one from any page; the GTK side
(``blocks.progreso``) adds the tick and the widgets.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

IDLE = "idle"
DETERMINATE = "determinate"
STAGE = "stage"
INDETERMINATE = "indeterminate"
SUCCESS = "success"
ERROR = "error"
STOPPED = "stopped"  # S21: «Detener» on a load; neutral, not an error

STOPPED_MESSAGE = "Carga detenida"

# A finished action stays in the header (and its toast is shown) this long.
LINGER_S = 10.0

# Sources of the usual/limit durations (local-ai-models, gufo_ctl.py):
ENSURE_LIMIT_S = 90.0  # ``ensure``: wait_health waits up to 90 s
USUAL_LOAD_S = {"abito": 30.0, "gufo": 90.0}  # measured switches (residente_logica)
SWITCH_LIMIT_S = 900.0  # gufo_ctl command_switch(timeout=900)
UPDATE_LIMIT_S = 2700.0  # monitor's own limit for ``update``: compiling takes tens of minutes
ENGINE_UPDATE_USUAL_S = 85.0  # ``engine update``: «el motor quedará ~85 s sin modelo»
ENGINE_UPDATE_LIMIT_S = 900.0
STOP_LIMIT_S = 300.0  # monitor's own command timeout

SWITCH_STAGES = {
    "gufo": ("Apagando Abito", "Cargando pesos (loading)", "Verificando (postload)"),
}


@dataclass(frozen=True)
class Progress:
    """What a widget paints for one action at one instant."""

    state: str = IDLE
    title: str = ""
    percent: float | None = None
    stage: int = 0
    stages: tuple[str, ...] = ()
    elapsed_s: float = 0.0
    typical_s: float | None = None
    limit_s: float | None = None
    message: str = ""
    detail: str = ""
    log: str = ""
    note: str = ""
    retry: bool = False
    stoppable: bool = False  # «Detener» is offered (a load, not asked yet)
    stopping: bool = False  # «Detener» was confirmed; the load is ending

    @property
    def running(self) -> bool:
        return self.state in (DETERMINATE, STAGE, INDETERMINATE)

    @property
    def finished(self) -> bool:
        return self.state in (SUCCESS, ERROR, STOPPED)


class Activity:
    """One long action from its start to its outcome."""

    def __init__(
        self,
        key: str,
        title: str,
        *,
        section: str = "",
        stages: tuple[str, ...] = (),
        typical_s: float | None = None,
        limit_s: float | None = None,
        note: str = "",
        anchor: object | None = None,
        retry: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.key = key
        self.title = title
        self.section = section
        self.stages = tuple(stages)
        self.typical_s = typical_s
        self.limit_s = limit_s
        self.note = note
        self.anchor = anchor
        self._retry = retry
        self._clock = clock
        self.started = clock()
        self.stage = 1 if self.stages else 0
        self.percent: float | None = None
        self.finished_at: float | None = None
        self.outcome: str | None = None
        self.message = ""
        self.detail = ""
        self.log = ""
        self.target = ""  # engine a switch/ensure acts on, when there is one
        self.announced = False
        # S21: only a model load gets a stopper (it ends the command); it is
        # called at most once, on the GTK thread.
        self.stopper: Callable[[], None] | None = None
        self.stop_requested = False

    # -- mutation (always on the GTK main thread) --------------------------

    def set_percent(self, value: float | None) -> None:
        """Only a real number in [0, 100] becomes a percentage."""
        if self.finished_at is not None or value is None:
            return
        if not 0 <= value <= 100:
            return
        self.percent = float(value)

    def set_stage(self, stage: int) -> None:
        """A new stage drops the percentage of the previous one."""
        if self.finished_at is not None or not self.stages:
            return
        stage = max(1, min(len(self.stages), int(stage)))
        if stage != self.stage:
            self.percent = None
        self.stage = stage

    def succeed(self, message: str, detail: str = "") -> None:
        self._finish(SUCCESS, message, detail, "")

    def fail(self, message: str, detail: str = "", log: str = "") -> None:
        """``message`` is the headline, ``detail`` the visible explanation and
        ``log`` the raw output behind «Ver detalle»."""
        self._finish(ERROR, message, detail, log)

    def halt(self, message: str = STOPPED_MESSAGE, detail: str = "") -> None:
        """The load was stopped on purpose («Detener»): neutral end."""
        self._finish(STOPPED, message, detail, "")

    def request_stop(self) -> bool:
        """«Detener» confirmed: True only the first time, while it runs.

        A load that already ended on its own (success or failure) ignores a
        late request: no stop is ever sent after it finished (S21 rule 4).
        """
        if self.finished_at is not None or self.stopper is None or self.stop_requested:
            return False
        self.stop_requested = True
        self.stopper()
        return True

    def _finish(self, outcome: str, message: str, detail: str, log: str) -> None:
        if self.finished_at is not None:
            return
        self.finished_at = self._clock()
        self.outcome = outcome
        self.message = message
        self.detail = detail
        self.log = log

    def retry(self) -> None:
        if self._retry is not None:
            self._retry()

    # -- reading -----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def finished_ago(self) -> float | None:
        """Seconds since the outcome, or None while it runs."""
        if self.finished_at is None:
            return None
        return self._clock() - self.finished_at

    def progress(self, now: float | None = None) -> Progress:
        end = self.finished_at if self.finished_at is not None else (
            self._clock() if now is None else now
        )
        if self.outcome is not None:
            state = self.outcome
        elif self.percent is not None:
            state = DETERMINATE
        elif self.stages:
            state = STAGE
        else:
            state = INDETERMINATE
        return Progress(
            state=state,
            title=self.title,
            percent=self.percent if state == DETERMINATE else None,
            stage=self.stage,
            stages=self.stages,
            elapsed_s=max(0.0, end - self.started),
            typical_s=self.typical_s,
            limit_s=self.limit_s,
            message=self.message,
            detail=self.detail,
            log=self.log,
            note=self.note,
            retry=self.outcome == ERROR and self._retry is not None,
            stoppable=(
                self.outcome is None
                and self.stopper is not None
                and not self.stop_requested
            ),
            stopping=self.outcome is None and self.stop_requested,
        )


class ActivityHub:
    """Every activity of the app, by key; the header shows ``current()``."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._activities: dict[str, Activity] = {}
        self._subs: list[Callable[[], None]] = []
        self._finished_subs: list[Callable[[Activity], None]] = []

    def reset(self) -> None:
        self._activities.clear()
        self.changed()

    # -- subscription -------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self._subs:
            self._subs.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        if callback in self._subs:
            self._subs.remove(callback)

    def on_finished(self, callback: Callable[[Activity], None]) -> None:
        if callback not in self._finished_subs:
            self._finished_subs.append(callback)

    def off_finished(self, callback: Callable[[Activity], None]) -> None:
        if callback in self._finished_subs:
            self._finished_subs.remove(callback)

    # -- activities ---------------------------------------------------------

    def begin(self, activity: Activity) -> Activity:
        # Elapsed time counts from the moment the action really starts (a
        # queued «Actualizar todo» item is built before its turn).
        activity.started = activity._clock()
        self._activities[activity.key] = activity
        self.changed()
        return activity

    def get(self, key: str) -> Activity | None:
        return self._activities.get(key)

    def running(self, key: str) -> bool:
        activity = self._activities.get(key)
        return activity is not None and activity.running

    def any_running(self, prefix: str = "") -> bool:
        return any(
            activity.running and activity.key.startswith(prefix)
            for activity in self._activities.values()
        )

    def find(self, match: str | Callable[[Activity], bool]) -> Activity | None:
        """The newest activity whose key starts with ``match`` (or satisfies it)."""
        test = match if callable(match) else (lambda a: a.key.startswith(match))
        found = [a for a in self._activities.values() if test(a)]
        if not found:
            return None
        running = [a for a in found if a.running]
        return max(running or found, key=lambda a: a.started)

    def current(self, now: float | None = None) -> Activity | None:
        """The running action started last; else one that finished < LINGER_S ago.

        A stopped load is not shown: «Detener» clears the header at once
        (S21); its card keeps «Detenido» while it lingers.
        """
        now = self._clock() if now is None else now
        running = [a for a in self._activities.values() if a.running]
        if running:
            return max(running, key=lambda a: a.started)
        recent = [
            a
            for a in self._activities.values()
            if a.finished_at is not None
            and a.outcome != STOPPED
            and now - a.finished_at < LINGER_S
        ]
        return max(recent, key=lambda a: a.finished_at or 0.0) if recent else None

    def lingering(self, now: float | None = None) -> bool:
        """Some outcome is still on screen (card or header): keep ticking."""
        now = self._clock() if now is None else now
        return any(
            a.finished_at is not None and now - a.finished_at < LINGER_S
            for a in self._activities.values()
        )

    def changed(self) -> None:
        """Announce newly finished activities once, then notify every view."""
        for activity in tuple(self._activities.values()):
            if activity.finished_at is not None and not activity.announced:
                activity.announced = True
                for callback in tuple(self._finished_subs):
                    callback(activity)
        for callback in tuple(self._subs):
            callback()


# ---------------------------------------------------------------------------
# Real evidence parsers
# ---------------------------------------------------------------------------

_CMAKE_PERCENT = re.compile(r"^\s*\[\s*(\d{1,3})%\]")
# A drop this large means cmake started over: the update's rollback recompile.
_REBUILD_DROP = 25


def cmake_percent(line: str) -> int | None:
    """The percentage of a «[ NN%]» line printed by cmake (Unix Makefiles)."""
    match = _CMAKE_PERCENT.match(line or "")
    if match is None:
        return None
    value = int(match.group(1))
    return value if value <= 100 else None


class BuildProgress:
    """Stages of ``update engine-abito --build`` observed from its output.

    The script fetches (git, no percentage), compiles (cmake «[ NN%]») and
    then restarts and verifies the service (no output: one stage). A second
    compile after the first one is the rollback recompile.
    """

    STAGES = ("Descargando", "Compilando", "Reiniciando y verificando")

    def __init__(self) -> None:
        self.stage = 1
        self.percent: int | None = None
        self.rolled_back = False
        self._peak: int | None = None

    def feed(self, line: str) -> bool:
        value = cmake_percent(line)
        if value is None:
            return False
        if self._peak is not None and value <= self._peak - _REBUILD_DROP:
            self.rolled_back = True
            self._peak = None
        if self._peak is None or value > self._peak:
            self._peak = value
        if self._peak >= 100:
            self.stage, self.percent = 3, None
        else:
            self.stage, self.percent = 2, self._peak
        return True


class SwitchStages:
    """Stage of a switch to Gufo from the ``gufo-flash status`` phase.

    loading → postload → workload never goes back (gufo_ctl ``Phase``). A
    phase read before «loading» was seen may be the previous run's: ignored.
    """

    def __init__(self) -> None:
        self.stage = 1
        self._seen_loading = False

    def feed(self, phase: str) -> bool:
        if phase == "loading":
            self._seen_loading = True
            new = 2
        elif phase in ("postload", "workload") and self._seen_loading:
            new = 3
        else:
            return False
        if new <= self.stage:
            return False
        self.stage = new
        return True


# ---------------------------------------------------------------------------
# Activities of each long action
# ---------------------------------------------------------------------------


def display_name(model: str) -> str:
    return model[:1].upper() + model[1:]


def engine_action(model: str, action: str, **kw) -> Activity:
    """``ensure`` / ``stop`` of a model: exit code only, no stage, no %."""
    name = display_name(model)
    if action == "ensure":
        activity = Activity(
            f"engine:{model}",
            f"Encendiendo {name}",
            typical_s=USUAL_LOAD_S.get(model),
            limit_s=ENSURE_LIMIT_S,
            note="Sin porcentaje: local-ai-models ensure solo avisa al terminar.",
            **kw,
        )
    else:
        activity = Activity(
            f"engine:{model}",
            f"Apagando {name}",
            limit_s=STOP_LIMIT_S,
            note="Sin porcentaje: local-ai-models stop solo avisa al terminar.",
            **kw,
        )
    activity.target = model
    return activity


def switch_action(target: str, **kw) -> Activity:
    stages = SWITCH_STAGES.get(target, ())
    note = (
        "La etapa sale de gufo-flash status (phase); la carga de pesos no informa porcentaje."
        if stages
        else "Sin porcentaje: el cambio solo avisa al terminar."
    )
    activity = Activity(
        "switch",
        f"Cambiando a {display_name(target)}",
        stages=stages,
        typical_s=USUAL_LOAD_S.get(target),
        limit_s=SWITCH_LIMIT_S,
        note=note,
        **kw,
    )
    activity.target = target
    return activity


def update_action(
    component_id: str, name: str, eta_s: float | None, with_build: bool = False, **kw
) -> Activity:
    if with_build:
        return Activity(
            f"update:{component_id}",
            f"Actualizando {name}",
            stages=BuildProgress.STAGES,
            limit_s=UPDATE_LIMIT_S,
            note=(
                "% solo al compilar (líneas «[ NN%]» de cmake). "
                "El motor queda apagado hasta el final."
            ),
            **kw,
        )
    return Activity(
        f"update:{component_id}",
        f"Actualizando {name}",
        typical_s=eta_s,
        limit_s=UPDATE_LIMIT_S,
        note="Sin porcentaje: local-ai-models update solo avisa al terminar.",
        **kw,
    )


def engine_update_action(**kw) -> Activity:
    return Activity(
        "engine-update",
        "Actualizando el motor",
        typical_s=ENGINE_UPDATE_USUAL_S,
        limit_s=ENGINE_UPDATE_LIMIT_S,
        note="Sin porcentaje: engine update solo avisa al terminar; el motor queda ~85 s sin modelo.",
        **kw,
    )


# ---------------------------------------------------------------------------
# «Detener» a model load (S21)
# ---------------------------------------------------------------------------

# The route that leaves nothing loaded once the load command is ended:
# ``stop abito`` cancels a unit still starting; ``--no-restore`` records an
# explicit stop, so Gufo's post-stop does not bring Abito back (L31, L42).
STOP_ROUTES = {
    "abito": ("local-ai-models", ("stop", "abito")),
    "gufo": ("gufo-flash", ("stop", "--no-restore")),
}
# What ``resident`` reports while the engine is still (being) loaded.
_STILL_LOADED = {"abito": ("abito", "conflict"), "gufo": ("gufo", "gufo-loading", "conflict")}


def stop_confirm_text(target: str) -> tuple[str, str]:
    """Title and body of the «Detener» confirmation (style of «¿Apagar abito?»)."""
    name = display_name(target)
    return (
        f"¿Detener la carga de {name}?",
        (
            f"{name} deja de cargarse y ningún motor queda cargado. "
            "Para usarlo hay que volver a encenderlo."
        ),
    )


def stop_outcome(target: str, result, resident: str | None) -> tuple[str, str, str]:
    """(state, message, detail) once the stop route ran and ``resident`` was reread.

    ``result`` is the stop route's ``CommandResult``; ``resident`` the value
    of ``local-ai-models resident`` afterwards (None when it could not be
    read). «Detenido» only when the backend confirms the engine is not
    loaded; a failed route is an error with its reason.
    """
    name = display_name(target)
    tool, args = STOP_ROUTES[target]
    command = " ".join((tool, *args))
    if result.failure == "missing":
        return ERROR, f"No se pudo detener {name}", f"No se encontró {tool}."
    if result.failure == "timeout":
        return ERROR, f"No se pudo detener {name}", f"{command} agotó el tiempo de espera."
    if result.failure:
        return ERROR, f"No se pudo detener {name}", (
            f"No se pudo ejecutar {command}: {result.stderr or result.failure}"
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "sin detalle").strip()
        return ERROR, f"No se pudo detener {name}", (
            f"{command} terminó con código {result.returncode}: {detail}"
        )
    if resident is None:
        return ERROR, f"{name}: detención sin verificar", (
            "La orden de detener terminó, pero no se pudo leer el estado real."
        )
    if resident in _STILL_LOADED.get(target, (target,)):
        return ERROR, f"No se pudo detener {name}", (
            "La orden de detener terminó, pero el administrador informa que sigue cargado."
        )
    if resident == "none":
        return STOPPED, STOPPED_MESSAGE, "Ningún motor quedó cargado."
    return STOPPED, STOPPED_MESSAGE, f"Ahora figura como residente: {resident}."


# ---------------------------------------------------------------------------
# Texts
# ---------------------------------------------------------------------------


def clock_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def duration_text(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"


def _stage_name(progress: Progress) -> str:
    if not progress.stages or not 1 <= progress.stage <= len(progress.stages):
        return ""
    return progress.stages[progress.stage - 1]


def headline(progress: Progress) -> str:
    if progress.state == SUCCESS:
        return f"✓ {progress.message}"
    if progress.state == ERROR:
        return progress.message
    if progress.state == STOPPED:
        return "Detenido"
    if progress.stopping:
        return "Deteniendo la carga…"
    if progress.state in (DETERMINATE, STAGE) and progress.stages:
        return (
            f"{progress.title} · etapa {progress.stage} de {len(progress.stages)}"
            f" · {_stage_name(progress).lower()}"
        )
    return f"{progress.title}…"


def meta_texts(progress: Progress) -> tuple[str, str]:
    """(left, right) under the bar: elapsed + usual time | limit."""
    elapsed = clock_text(progress.elapsed_s)
    if progress.state == SUCCESS:
        return f"Tardó {elapsed}", ""
    if progress.state in (ERROR, STOPPED):
        return f"Tras {elapsed}", ""
    limit = f"límite {duration_text(progress.limit_s)}" if progress.limit_s else ""
    if progress.state == DETERMINATE and progress.percent is not None:
        return f"{progress.percent:.0f} % · {elapsed} transcurridos", limit
    left = f"{elapsed} transcurridos"
    if progress.typical_s:
        usual = duration_text(progress.typical_s)
        if progress.elapsed_s > progress.typical_s:
            left += f" · más de lo habitual (~{usual})"
        else:
            left += f" · suele tardar ~{usual}"
    return left, limit


def header_text(progress: Progress) -> str:
    """The compact header pill."""
    if progress.finished:
        return progress.message
    elapsed = clock_text(progress.elapsed_s)
    if progress.state == DETERMINATE and progress.percent is not None:
        return f"{progress.title} · {_stage_name(progress).lower()} {progress.percent:.0f} %"
    if progress.state == STAGE:
        return f"{progress.title} · {progress.stage}/{len(progress.stages)} · {elapsed}"
    return f"{progress.title} · {elapsed}"
