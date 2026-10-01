"""PC-AI Monitor — native GNOME front end.

A GTK4/libadwaita app over the local AI stack: resources, token consumption,
listening ports and processes. The collectors are external scripts (see
``pc_ai_monitor.config``); this package only presents what they report.

The :mod:`gi` setup below runs on import, before any submodule loads a
namespace: ``require_version`` pins GTK4 (a machine with the GTK3 typelib would
otherwise resolve ``Gtk`` to the wrong major version) and ``require_foreign``
registers the cairo converter that ``Gtk.DrawingArea`` draw functions need.
"""

import contextlib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

# Without the cairo foreign converter every draw function fails with
# "Couldn't find foreign struct converter for 'cairo.Context'". A machine whose
# gobject-introspection lacks it still runs: only the charts stay blank.
with contextlib.suppress(ImportError):
    gi.require_foreign("cairo")

__version__ = "0.2.0-rc2"
