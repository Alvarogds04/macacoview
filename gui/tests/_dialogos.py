# pyright: reportAttributeAccessIssue=false
"""Real confirmation dialogs for the page tests, without any fake.

Every destructive button opens a confirmation first. Doubles of
``Gtk.MessageDialog`` hid a GTK3-only call (``format_secondary_text``) that
GTK 4 does not have, so in the installed app no dialog ever opened. These
helpers let a test run the block's own path against the real GTK/Adw
classes: they find the dialog the block presented among the toplevels,
answer it through its real ``response`` signal and wait for the command the
isolation guard (``_aislamiento``) records.
"""

import sys
import time

from gi.repository import Adw, GLib, Gtk

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

DIALOG_TYPES = (Gtk.MessageDialog, Adw.MessageDialog)


def pump(seconds: float = 0.0) -> None:
    """Run pending main-loop work, then keep pumping for ``seconds``."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while True:
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def pump_until(condition, timeout: float = 5.0) -> bool:
    """Pump the default context until ``condition()`` holds or time runs out."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        context.iteration(False)
        time.sleep(0.01)
    return True


def presented_dialogs(action) -> list:
    """Run ``action`` and return the message dialogs it presented.

    PyGObject does not propagate an exception raised inside a signal handler
    (a button's ``clicked``): it hands it to ``sys.excepthook`` and carries
    on, which is exactly how the GTK3-only call went unnoticed. Here such an
    exception is re-raised so the test fails with the real cause.
    """
    before = list(Gtk.Window.list_toplevels())
    errors: list[BaseException] = []
    previous = sys.excepthook
    sys.excepthook = lambda _type, value, _tb: errors.append(value)
    try:
        action()
        pump()
    finally:
        sys.excepthook = previous
    new = [
        window
        for window in Gtk.Window.list_toplevels()
        if isinstance(window, DIALOG_TYPES) and window not in before
    ]
    if errors:
        for window in new:
            window.destroy()
        raise errors[0]
    # Built but never presented is not a dialog the user can answer.
    return [window for window in new if window.get_visible()]


def button_label(dialog: Gtk.MessageDialog, response: Gtk.ResponseType) -> str | None:
    """Label of the real button bound to ``response``, or None if absent."""
    button = dialog.get_widget_for_response(response)
    return button.get_label() if button is not None else None


def close(dialog) -> None:
    """Drop a dialog the test left open so it never leaks into another test."""
    if dialog in Gtk.Window.list_toplevels():
        dialog.destroy()
    pump()


def mark() -> int:
    """Position in the guard's log from which a test reads its own commands."""
    return len(_aislamiento.ATTEMPTS)


def commands_since(start: int) -> list[tuple]:
    """Manager commands recorded by the isolation guard after ``start``."""
    return [
        args
        for route, args in _aislamiento.ATTEMPTS[start:]
        if route == "local-ai-models"
    ]


def wait_for_command(start: int, command: tuple, timeout: float = 5.0) -> bool:
    """Pump until the guard records ``command`` after ``start``."""
    return pump_until(lambda: command in commands_since(start), timeout)
