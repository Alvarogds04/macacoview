"""Minimal type stubs for PyGObject.

PyGObject ships no type information: without these stubs every `from
gi.repository import Gtk` in this project is reported as an unknown import and
the type checker turns into noise. The stubs stay permissive (`Any`) instead of
mirroring the whole GTK API: the goal is to let the checker resolve these
modules, not to reimplement GNOME typings.
"""

from typing import Any

require_version: Any
require_versions: Any
get_required_version: Any
repository: Any
