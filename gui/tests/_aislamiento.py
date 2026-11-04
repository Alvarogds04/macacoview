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


def _manager(args=(), timeout=None):
    from pc_ai_monitor.blocks.motores import CommandResult

    ATTEMPTS.append(("local-ai-models", tuple(args)))
    return CommandResult(None, failure="missing")


def _http(path, timeout=None) -> str | None:
    ATTEMPTS.append(("engine-http", (path,)))
    return None


def _gufo_flash():
    ATTEMPTS.append(("gufo-flash", ("status",)))
    return "", None, "missing"


def isolate() -> None:
    """Patch every route to the live machine once per process."""
    if _PATCHES:
        return
    from pc_ai_monitor.blocks import motores, rendimiento

    targets = [
        (motores, "run_manager", _manager),
        (motores, "_run_manager", lambda *args: _manager(args)),
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
                "_run_manager": lambda *args: _manager(args),
                "_http_text": _http,
                "_run_gufo_flash_status": _gufo_flash,
            }[name]
            targets.append((module, name, replacement))

    for module, name, replacement in targets:
        patcher = mock.patch.object(module, name, replacement)
        patcher.start()
        _PATCHES.append(patcher)
