"""GTK side of the long-action progress (S14): hub tick, inline bar, header pill.

* ``HUB`` — the app-wide ``ActivityHub`` plus a 0.5 s tick while something
  runs or just finished, so elapsed times and pulsing bars move.
* ``ProgressInline`` — the «acción en curso» block of design 08: it lives in
  the card that started the action, under its button.
* ``HeaderIndicator`` — the compact pill every section header carries;
  clicking it opens the section and focuses the card that started it.

Workers never touch these objects: they hand changes to the main thread with
``post`` (``GLib.idle_add``), which also re-announces the hub.
"""

# pyright: reportAttributeAccessIssue=false

import weakref
from collections.abc import Callable

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks import progreso_logica as pl

TICK_MS = 500


class GtkActivityHub(pl.ActivityHub):
    """The pure hub plus a main-loop tick while an activity is on screen."""

    def __init__(self) -> None:
        super().__init__()
        self._tick_id = 0

    def changed(self) -> None:
        super().changed()
        if self.current() is not None and not self._tick_id:
            self._tick_id = GLib.timeout_add(TICK_MS, self._tick)

    def reset(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        super().reset()

    def _tick(self) -> bool:
        if self.current() is None:
            self._tick_id = 0
            pl.ActivityHub.changed(self)  # last repaint: hide what expired
            return False
        pl.ActivityHub.changed(self)
        return True

    def subscribe_widget(self, method) -> None:
        """Subscribe a bound method without keeping its widget alive."""
        ref = weakref.WeakMethod(method)

        def _callback() -> None:
            target = ref()
            if target is None:
                self.unsubscribe(_callback)
                return
            target()

        self.subscribe(_callback)


HUB = GtkActivityHub()


def post(fn: Callable, *args) -> None:
    """From a worker: run ``fn(*args)`` on the GTK thread, then repaint."""

    def _run() -> bool:
        fn(*args)
        HUB.changed()
        return False

    GLib.idle_add(_run)


def switching_target() -> str | None:
    """The engine an in-flight switch is moving to, from any page."""
    activity = HUB.get("switch")
    if activity is None or not activity.running:
        return None
    return activity.target or None


def begin(activity: pl.Activity) -> pl.Activity:
    """Main thread: register a new activity and repaint every view."""
    return HUB.begin(activity)


def finish(
    activity: pl.Activity, ok: bool, message: str, detail: str = "", log: str = ""
) -> None:
    """Main thread: close an activity with its outcome."""
    if ok:
        activity.succeed(message, detail)
    else:
        activity.fail(message, detail, log=log)
    HUB.changed()


def _caption(css: str = "caption") -> Gtk.Label:
    label = Gtk.Label(xalign=0)
    label.add_css_class(css)
    return label


class ProgressInline(Gtk.Box):
    """Inline progress of the action whose key starts with ``match``."""

    def __init__(
        self, match: str | Callable[[pl.Activity], bool], max_chars: int = 70
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._match = match
        self.add_css_class("act")
        # Explicit: the inner labels expand, but that must not leak out and
        # widen the card's column (GTK propagates hexpand to the parents).
        self.set_hexpand(False)
        # Focusable so the header pill can bring the card into view.
        self.set_focusable(True)
        self.set_visible(False)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Gtk.Spinner()
        head.append(self._spinner)
        self._title = Gtk.Label(xalign=0)
        self._title.add_css_class("act-title")
        self._title.set_wrap(True)
        self._title.set_max_width_chars(max_chars)
        self._title.set_hexpand(True)
        head.append(self._title)
        self.append(head)

        self._steps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._steps_box.set_homogeneous(True)
        self.append(self._steps_box)
        self._steps: list[tuple[Gtk.Box, Gtk.Label]] = []
        self._steps_for: tuple[str, ...] = ()

        self._bar = Gtk.ProgressBar()
        self._bar.add_css_class("act-bar")
        self._bar.set_pulse_step(0.08)
        self.append(self._bar)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._meta_left = _caption()
        self._meta_left.add_css_class("muted")
        self._meta_left.add_css_class("num")
        self._meta_left.set_hexpand(True)
        self._meta_left.set_wrap(True)
        self._meta_left.set_max_width_chars(max_chars)
        meta.append(self._meta_left)
        self._meta_right = _caption()
        self._meta_right.add_css_class("muted")
        meta.append(self._meta_right)
        self.append(meta)

        self._note = _caption("why")
        self._note.set_wrap(True)
        self._note.set_max_width_chars(max_chars)
        self.append(self._note)

        # Error: the reason in words, readable without opening anything.
        self._msg = Gtk.Label(xalign=0)
        self._msg.add_css_class("act-msg")
        self._msg.set_wrap(True)
        self._msg.set_max_width_chars(max_chars)
        self.append(self._msg)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._retry_button = Gtk.Button(label="Reintentar")
        self._retry_button.add_css_class("suggested-action")
        self._retry_button.connect("clicked", self._on_retry)
        buttons.append(self._retry_button)
        self._detail_button = Gtk.Button(label="Ver detalle")
        self._detail_button.connect("clicked", self._on_detail)
        buttons.append(self._detail_button)
        self._buttons = buttons
        self.append(buttons)

        self._detail = Gtk.Label(xalign=0)
        self._detail.add_css_class("caption")
        self._detail.add_css_class("mono")
        self._detail.set_wrap(True)
        self._detail.set_selectable(True)
        self._detail.set_max_width_chars(max_chars)
        self._detail.set_visible(False)
        self.append(self._detail)

        self._activity: pl.Activity | None = None
        HUB.subscribe_widget(self._render)
        self._render()

    # -- events --------------------------------------------------------------

    def _on_retry(self, _button) -> None:
        if self._activity is not None:
            self._activity.retry()

    def _on_detail(self, _button) -> None:
        self._detail.set_visible(not self._detail.get_visible())
        self._detail_button.set_label(
            "Ocultar detalle" if self._detail.get_visible() else "Ver detalle"
        )

    # -- render --------------------------------------------------------------

    def _visible_activity(self) -> pl.Activity | None:
        activity = HUB.find(self._match)
        if activity is None:
            return None
        if activity.running or activity.outcome == pl.ERROR:
            return activity
        # Success: shown while it lingers in the header, then the card is clean.
        ago = activity.finished_ago()
        if ago is not None and ago < pl.LINGER_S:
            return activity
        return None

    def _render(self) -> None:
        activity = self._visible_activity()
        if activity is not self._activity:
            self._detail.set_visible(False)
            self._detail_button.set_label("Ver detalle")
        self._activity = activity
        if activity is None:
            self.set_visible(False)
            self._spinner.stop()
            return
        progress = activity.progress()
        self.set_visible(True)

        for css in ("act-ok", "act-err"):
            self.remove_css_class(css)
        if progress.state == pl.SUCCESS:
            self.add_css_class("act-ok")
        elif progress.state == pl.ERROR:
            self.add_css_class("act-err")

        if progress.running:
            self._spinner.start()
            self._spinner.set_visible(True)
        else:
            self._spinner.stop()
            self._spinner.set_visible(False)
        self._title.set_text(pl.headline(progress))

        self._render_steps(progress)
        self._render_bar(progress)

        left, right = pl.meta_texts(progress)
        self._meta_left.set_text(left)
        self._meta_right.set_text(right)
        self._meta_right.set_visible(bool(right))

        note = progress.note if progress.running else ""
        self._note.set_text(note)
        self._note.set_visible(bool(note))

        failed = progress.state == pl.ERROR
        self._msg.set_text(progress.detail if failed else "")
        self._msg.set_visible(failed and bool(progress.detail))
        self._buttons.set_visible(failed and (progress.retry or bool(progress.log)))
        self._retry_button.set_visible(failed and progress.retry)
        self._detail_button.set_visible(failed and bool(progress.log))
        self._detail.set_text(progress.log)
        if not failed:
            self._detail.set_visible(False)

    def _render_steps(self, progress: pl.Progress) -> None:
        if progress.stages != self._steps_for:
            for bar, _label in self._steps:
                self._steps_box.remove(bar.get_parent())
            self._steps = []
            for name in progress.stages:
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                bar = Gtk.Box()
                bar.add_css_class("step-bar")
                bar.set_size_request(-1, 3)
                cell.append(bar)
                label = Gtk.Label(label=name, xalign=0)
                label.add_css_class("caption")
                label.add_css_class("muted")
                label.set_ellipsize(3)  # Pango.EllipsizeMode.END
                cell.append(label)
                self._steps_box.append(cell)
                self._steps.append((bar, label))
            self._steps_for = progress.stages
        self._steps_box.set_visible(bool(self._steps))
        for index, (bar, label) in enumerate(self._steps, start=1):
            for css in ("step-done", "step-now", "step-fail"):
                bar.remove_css_class(css)
            label.remove_css_class("step-now-label")
            if progress.state == pl.SUCCESS or index < progress.stage:
                bar.add_css_class("step-done")
            elif index == progress.stage:
                if progress.state == pl.ERROR:
                    bar.add_css_class("step-fail")
                else:
                    bar.add_css_class("step-now")
                    label.add_css_class("step-now-label")

    def _render_bar(self, progress: pl.Progress) -> None:
        for css in ("bar-green", "bar-error"):
            self._bar.remove_css_class(css)
        if progress.state == pl.DETERMINATE and progress.percent is not None:
            self._bar.set_fraction(progress.percent / 100.0)
            self._bar.set_visible(True)
        elif progress.running:
            self._bar.pulse()
            self._bar.set_visible(True)
        elif progress.state == pl.SUCCESS:
            self._bar.add_css_class("bar-green")
            self._bar.set_fraction(1.0)
            self._bar.set_visible(True)
        else:
            self._bar.set_visible(False)


class HeaderIndicator(Gtk.Button):
    """The header pill: the current action, from any page."""

    def __init__(self, on_open: Callable[[str, object | None], None]) -> None:
        super().__init__()
        self._on_open = on_open
        self.add_css_class("gpill")
        self.set_valign(Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._bar = Gtk.ProgressBar()
        self._bar.set_size_request(56, -1)
        self._bar.set_valign(Gtk.Align.CENTER)
        self._bar.set_pulse_step(0.1)
        box.append(self._bar)
        self._label = Gtk.Label(xalign=0)
        self._label.set_ellipsize(3)
        self._label.set_max_width_chars(46)
        box.append(self._label)
        self.set_child(box)
        self._activity: pl.Activity | None = None
        self.connect("clicked", self._on_clicked)
        self.set_visible(False)
        HUB.subscribe_widget(self._render)
        self._render()

    def _on_clicked(self, _button) -> None:
        if self._activity is not None:
            self._on_open(self._activity.section, self._activity.anchor)

    def _render(self) -> None:
        activity = HUB.current()
        self._activity = activity
        if activity is None:
            self.set_visible(False)
            return
        progress = activity.progress()
        self.set_visible(True)
        self._label.set_text(pl.header_text(progress))
        left, right = pl.meta_texts(progress)
        self.set_tooltip_text(
            f"{pl.headline(progress)}\n{left}" + (f" · {right}" if right else "")
            + "\nClic: ir a la tarjeta"
        )
        for css in ("gpill-ok", "gpill-err"):
            self.remove_css_class(css)
        for css in ("bar-green", "bar-error"):
            self._bar.remove_css_class(css)
        if progress.state == pl.DETERMINATE and progress.percent is not None:
            self._bar.set_fraction(progress.percent / 100.0)
        elif progress.running:
            self._bar.pulse()
        elif progress.state == pl.SUCCESS:
            self.add_css_class("gpill-ok")
            self._bar.add_css_class("bar-green")
            self._bar.set_fraction(1.0)
        else:
            self.add_css_class("gpill-err")
            self._bar.add_css_class("bar-error")
            self._bar.set_fraction(1.0)


# ---------------------------------------------------------------------------
# Shared runners: the same honest progress wherever the action starts
# ---------------------------------------------------------------------------

STAGE_POLL_S = 2.0


def run_switch(
    target: str,
    runner: Callable,
    reader: Callable,
    status_reader: Callable[[], tuple[str, int | None, str | None]],
    on_done: Callable[[bool, str, dict | None], None],
    *,
    section: str,
    anchor: object | None = None,
    retry: Callable[[], None] | None = None,
    poll_s: float | None = None,
    timeout: float = pl.SWITCH_LIMIT_S,
) -> pl.Activity:
    """Main thread: ``switch <target>`` with its stage read from the guard.

    ``runner`` / ``reader`` are the caller module's ``run_manager`` /
    ``_run_manager`` and ``status_reader`` its ``gufo-flash status`` reader:
    the test isolation guard replaces exactly those names. ``on_done`` runs on
    the GTK thread with ``(ok, message, resident_info)``.
    """
    import threading

    from pc_ai_monitor.blocks.rendimiento_logica import parse_guard
    from pc_ai_monitor.blocks.residente_logica import (
        NAMES,
        parse_resident,
        switch_outcome,
    )

    activity = begin(pl.switch_action(target, section=section, anchor=anchor, retry=retry))
    done = threading.Event()
    interval = STAGE_POLL_S if poll_s is None else poll_s

    def _poll() -> None:
        stages = pl.SwitchStages()
        while not done.wait(interval):
            guard = parse_guard(*status_reader())
            if not guard.error and stages.feed(guard.phase):
                post(activity.set_stage, stages.stage)

    def _worker() -> None:
        if activity.stages:
            threading.Thread(target=_poll, daemon=True).start()
        result = runner(("switch", target), timeout)
        done.set()
        ok, message = switch_outcome(
            result.returncode, result.stderr, result.stdout, target, result.failure
        )
        after = reader("resident")
        info = parse_resident(after.stdout) if after.returncode == 0 else None

        def _finish() -> None:
            if ok:
                activity.succeed(f"Residente: {NAMES[target]}", message)
            else:
                activity.fail(
                    f"No se cambió a {NAMES[target]}",
                    message,
                    log=(result.stderr or result.stdout or "").strip(),
                )
            on_done(ok, message, info)

        post(_finish)

    threading.Thread(target=_worker, daemon=True).start()
    return activity


def engine_outcome(model: str, action: str, result, inventory: list[dict] | None,
                   failure_text: str) -> tuple[bool, str, str]:
    """(ok, headline, detail) of an ``ensure`` / ``stop`` after rereading state.

    ``ok`` only when the backend confirmed the new state: exit 0 is not
    enough if the reread inventory says otherwise.
    """
    name = pl.display_name(model)
    verb_ok = "encendido" if action == "ensure" else "apagado"
    verb_fail = "No se encendió" if action == "ensure" else "No se apagó"
    if result.returncode != 0:
        return False, f"{verb_fail} {name}", failure_text
    if inventory is None:
        return False, f"{name}: estado sin verificar", (
            "La orden terminó, pero no se pudo leer el estado real."
        )
    actual = next((item for item in inventory if item.get("model") == model), None)
    healthy = bool(actual and actual.get("healthy", False))
    if (action == "ensure") == healthy:
        return True, f"{name} {verb_ok}", ""
    return False, f"{verb_fail} {name}", (
        "La orden terminó, pero el modelo no está saludable."
        if action == "ensure"
        else "La orden terminó, pero todavía está saludable."
    )
