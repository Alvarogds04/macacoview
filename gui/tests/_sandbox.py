"""Sandbox HOME for the test suite: importing this module enters it.

``pc_ai_monitor.config`` binds ``~/.config/macacoview/config.toml`` as the
default argument of ``load``/``save``/``write_template`` when it is imported,
and the app rewrites ``~/.local/share/applications/macacoview.desktop``. A
settings apply left pending by one test fired inside another and overwrote
the user's real config. Every test module imports this module before anything
from ``pc_ai_monitor``, so all those paths land in a temporary directory.
"""

import atexit
import os
import shutil
import sys
import tempfile

_MARKER = "MACACOVIEW_TEST_HOME"

# The user's real HOME, captured before the sandbox replaces it, so tests can
# assert that nothing reaches it.
REAL_HOME = os.environ.get("MACACOVIEW_REAL_HOME") or os.path.expanduser("~")


def _enter() -> str:
    current = os.environ.get(_MARKER)
    if current and os.environ.get("HOME") == current:
        return current  # already inside (the module may load under two names)
    if any(name == "pc_ai_monitor" or name.startswith("pc_ai_monitor.") for name in sys.modules):
        raise RuntimeError(
            "pc_ai_monitor was imported before the test sandbox: its config "
            "paths already point at the real HOME"
        )
    sandbox = tempfile.mkdtemp(prefix="macacoview-tests-")
    os.environ["MACACOVIEW_REAL_HOME"] = REAL_HOME
    os.environ[_MARKER] = sandbox
    os.environ["HOME"] = sandbox
    atexit.register(shutil.rmtree, sandbox, True)
    return sandbox


SANDBOX = _enter()
