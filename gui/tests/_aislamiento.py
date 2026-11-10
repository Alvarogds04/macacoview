# pyright: reportAttributeAccessIssue=false
"""Isolation for page tests: no test may reach the real manager or engine.

Building a page starts its blocks' worker threads, and those call
``local-ai-models``, ``gufo-flash`` and the engine on :11070. Tests must never
touch the live machine, so every test module that builds pages calls
``isolate()`` at import time. The guard replaces the four entry points every
block goes through with inert answers and records any attempt, so a test can
assert that nothing escaped.
"""

from unittest import mock

ATTEMPTS: list[tuple[str, tuple]] = []
_PATCHES: list = []


def _manager(args=(), timeout=None, on_line=None, stop=None):
    """Every ``local-ai-models`` call, ``stop abito`` included: refused."""
    from pc_ai_monitor.blocks.motores import CommandResult

    del timeout, on_line, stop
    ATTEMPTS.append(("local-ai-models", tuple(args)))
    return CommandResult(None, failure="missing")


def _http(path, timeout=None) -> str | None:
    ATTEMPTS.append(("engine-http", (path,)))
    return None


def _gufo_flash():
    ATTEMPTS.append(("gufo-flash", ("status",)))
    return "", None, "missing"


def _gufo_flash_command(args=(), timeout=None):
    """``gufo-flash stop --no-restore`` (Home: apagar Gufo, L31): refused."""
    from pc_ai_monitor.blocks.motores import CommandResult

    ATTEMPTS.append(("gufo-flash", tuple(args)))
    return CommandResult(None, failure="missing")


def isolate() -> None:
    """Patch every route to the live machine once per process."""
    if _PATCHES:
        return
    from pc_ai_monitor.blocks import motores, rendimiento

    targets = [
        (motores, "run_manager", _manager),
        (motores, "_run_manager", lambda *args, **kw: _manager(args, **kw)),
        # Callers resolve it as ``motores.run_gufo_flash`` at call time.
        (motores, "run_gufo_flash", _gufo_flash_command),
        (rendimiento, "_http_text", _http),
        (rendimiento, "_run_gufo_flash_status", _gufo_flash),
    ]
    # Modules that imported the names directly keep their own reference.
    for module_name, names in (
        ("pc_ai_monitor.blocks.residente", ("_run_manager", "run_manager")),
        ("pc_ai_monitor.blocks.engine", ("run_manager",)),
        ("pc_ai_monitor.blocks.actualizaciones", ("run_manager",)),
        ("pc_ai_monitor.blocks.motores_vista", ("_run_manager", "run_manager")),
        ("pc_ai_monitor.blocks.home", ("run_manager", "_run_manager", "_http_text", "_run_gufo_flash_status")),
        ("pc_ai_monitor.blocks.router", ("_http_text",)),
    ):
        module = __import__(module_name, fromlist=["_"])
        for name in names:
            if not hasattr(module, name):
                continue
            replacement = {
                "run_manager": _manager,
                "_run_manager": lambda *args, **kw: _manager(args, **kw),
                "_http_text": _http,
                "_run_gufo_flash_status": _gufo_flash,
            }[name]
            targets.append((module, name, replacement))

    for module, name, replacement in targets:
        patcher = mock.patch.object(module, name, replacement)
        patcher.start()
        _PATCHES.append(patcher)


class HeldLoad:
    """A model load (``ensure abito`` / ``switch gufo``) held until «Detener».

    Patched over a block's runner names on top of the guard (``manager`` for
    ``run_manager``, ``manager_args`` for ``_run_manager``, ``gufo_flash``
    for ``motores.run_gufo_flash``). Every call is recorded in ``ATTEMPTS``
    exactly like the guard. The load blocks until the stop request reaches
    it — then it answers like the real runner after terminating its child
    (``failure="stopped"``) — or until ``finish()``. The stop routes answer
    ``stop_rc``; ``resident`` and ``status --json`` answer the reread.
    Nothing here ever spawns a process.
    """

    def __init__(self, load, *, stop_rc=0, resident=None, inventory=None,
                 load_rc=0, load_stdout=""):
        import threading

        self.load = tuple(load)
        self.stop_rc = stop_rc
        self.resident = resident
        self.inventory = inventory
        self.load_rc = load_rc
        self.load_stdout = load_stdout
        self.started = threading.Event()
        self._finished = threading.Event()
        self.stop_seen: list = []

    def finish(self) -> None:
        """The load ends on its own (success or failure, per ``load_rc``)."""
        self._finished.set()

    def _result(self, rc, stdout="", stderr=""):
        from pc_ai_monitor.blocks.motores import CommandResult

        if rc is None:
            return CommandResult(None, stderr=stderr, failure="missing")
        return CommandResult(rc, stdout, stderr)

    def manager(self, args=(), timeout=None, on_line=None, stop=None):
        import json
        import time

        del timeout, on_line
        args = tuple(args)
        ATTEMPTS.append(("local-ai-models", args))
        if args == self.load:
            self.stop_seen.append(stop)
            self.started.set()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if self._finished.wait(0.02):
                    return self._result(self.load_rc, self.load_stdout, "carga falló")
                if stop is not None and stop.requested:
                    from pc_ai_monitor.blocks.motores import CommandResult

                    return CommandResult(None, failure="stopped")
            return self._result(None)
        if args[:1] == ("stop",):
            return self._result(self.stop_rc, "", "stop rechazado")
        if args == ("resident",) and self.resident is not None:
            return self._result(0, json.dumps(self.resident))
        if args == ("status", "--json") and self.inventory is not None:
            return self._result(0, json.dumps(self.inventory))
        if args[:1] == ("capacity",):
            return self._result(0, "route=local")
        return self._result(None)

    def manager_args(self, *args, stop=None):
        return self.manager(args, stop=stop)

    def gufo_flash(self, args=(), timeout=None):
        del timeout
        ATTEMPTS.append(("gufo-flash", tuple(args)))
        return self._result(self.stop_rc, "", "gufo-flash stop falló")
