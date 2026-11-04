"""Block «Motor residente»: one switch between Abito and Gufo (never both in memory).

Everything goes through ``local-ai-models`` (``resident`` to read, ``switch`` to
act). No subprocess runs on the GTK main thread.
"""

# pyright: reportAttributeAccessIssue=false

import threading
import time

from gi.repository import Adw, GLib, Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.motores import _run_manager, run_manager
from pc_ai_monitor.blocks.residente_logica import (
    ENGINES,
    NAMES,
    confirm_text,
    engine_view,
    headline,
    parse_resident,
    progress_fraction,
    progress_text,
    switch_outcome,
)
from pc_ai_monitor.datos import Snapshot

REFRESH_INTERVAL_S = 3.0
SWITCH_TIMEOUT_S = 900


class EngineOption(Gtk.Box):
    """One side of the switch: name, state, details and its button."""

    def __init__(self, engine: str, on_click) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("card")
        self.set_hexpand(True)
        self.engine = engine

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=NAMES[engine], xalign=0)
        name.add_css_class("model-name")
        name.set_hexpand(True)
        top.append(name)
        self._status = Gtk.Label(xalign=1)
        self._status.add_css_class("mono")
        top.append(self._status)
        self.append(top)

        self._detail = Gtk.Label(xalign=0)
        self._detail.add_css_class("caption")
        self._detail.set_wrap(True)
        self._detail.set_max_width_chars(46)
        self.append(self._detail)

        self._reason = Gtk.Label(xalign=0)
        self._reason.add_css_class("muted")
        self._reason.set_wrap(True)
        self._reason.set_max_width_chars(46)
        self._reason.set_visible(False)
        self.append(self._reason)

        self._button = Gtk.Button()
        self._button.add_css_class("suggested-action")
        self._button.connect("clicked", lambda _b: on_click(engine))
        self.append(self._button)

    def render(self, view: dict) -> None:
        self._status.set_text(view["status"])
        if view["active"]:
            self._status.add_css_class("success")
        else:
            self._status.remove_css_class("success")
        self._detail.set_text(view["detail"])
        self._reason.set_text(view["reason"] or "")
        self._reason.set_visible(bool(view["reason"]))
        self._button.set_label(view["button"])
        self._button.set_visible(not view["active"])
        self._button.set_sensitive(view["enabled"])


class ResidenteBlock(Block):
    title = "MOTOR RESIDENTE"

    def __init__(self) -> None:
        super().__init__()
        self._info: dict | None = None
        self._switching: str | None = None
        self._refreshing = False
        self._last_refresh = 0.0

        self._headline = Gtk.Label(xalign=0)
        self._headline.add_css_class("heading")
        self.body.append(self._headline)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_homogeneous(True)
        self._options = {engine: EngineOption(engine, self._ask_confirmation) for engine in ENGINES}
        for engine in ENGINES:
            row.append(self._options[engine])
        self.body.append(row)

        self._bar = Gtk.ProgressBar()
        self._bar.set_visible(False)
        self.body.append(self._bar)
        self._message = Gtk.Label(xalign=0)
        self._message.add_css_class("muted")
        self._message.set_wrap(True)
        self._message.set_max_width_chars(80)
        self._message.set_visible(False)
        self.body.append(self._message)
        self._started = 0.0
        self._tick_id = 0

        self._render()
        self._refresh(force=True)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._refresh(force=False)

    def _render(self) -> None:
        self._headline.set_text(headline(self._info, self._switching))
        for engine, option in self._options.items():
            option.render(engine_view(self._info, engine, self._switching))

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing or (not force and now - self._last_refresh < REFRESH_INTERVAL_S):
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

    def _ask_confirmation(self, target: str) -> None:
        if self._switching is not None:
            return
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
        self._switching = target
        self._started = time.monotonic()
        self._message.remove_css_class("error")
        self._message.set_text(progress_text(0, target))
        self._message.set_visible(True)
        self._bar.set_fraction(0.0)
        self._bar.set_visible(True)
        self._tick_id = GLib.timeout_add(250, self._tick)
        self._render()

        def _worker() -> None:
            result = run_manager(("switch", target), SWITCH_TIMEOUT_S)
            ok, message = switch_outcome(result.returncode, result.stderr, result.stdout, target, result.failure)
            after = _run_manager("resident")
            info = parse_resident(after.stdout) if after.returncode == 0 else None
            GLib.idle_add(self._on_switched, ok, message, info)

        threading.Thread(target=_worker, daemon=True).start()

    def _tick(self) -> bool:
        if self._switching is None:
            self._tick_id = 0
            return False
        elapsed = time.monotonic() - self._started
        self._bar.set_fraction(progress_fraction(elapsed, self._switching))
        self._message.set_text(progress_text(elapsed, self._switching))
        return True

    def _on_switched(self, ok: bool, message: str, info: dict | None) -> bool:
        self._switching = None
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._bar.set_fraction(1.0 if ok else self._bar.get_fraction())
        GLib.timeout_add(1500, self._hide_bar)
        self._message.set_text(("✔ " if ok else "✘ ") + message)
        self._message.set_visible(True)
        if ok:
            self._message.remove_css_class("error")
        else:
            self._message.add_css_class("error")
        self._on_resident(info)
        return False

    def _hide_bar(self) -> bool:
        if self._switching is None:
            self._bar.set_visible(False)
        return False
