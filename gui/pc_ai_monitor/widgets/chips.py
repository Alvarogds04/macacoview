"""Small building blocks: chips, cards and their headings.

These are pure layout helpers with no data of their own. Anything that holds a
value and updates in place belongs in ``barras`` (meters, KPI tiles) or in a
block (see ``pc_ai_monitor.blocks``).
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk


def chip(text: str, kind: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("chip")
    if kind:
        label.add_css_class(f"chip-{kind}")
    return label


def chips(items: list[tuple[str, str]]) -> Gtk.Widget:
    """(text, kind) pairs wrapped into a row of chips."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.set_halign(Gtk.Align.START)
    for text, kind in items:
        box.append(chip(text, kind))
    return box


def section_heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("section-head")
    return label


def card(title: str, content: Gtk.Widget) -> Gtk.Box:
    """A titled card: the shape every dashboard block is wrapped in."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("card")
    box.append(section_heading(title))
    box.append(content)
    return box


def note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("muted")
    label.set_wrap(True)
    return label


def scrolled(content: Gtk.Widget) -> Gtk.ScrolledWindow:
    """The scrolling frame and margins every dashboard page uses."""
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_vexpand(True)

    padded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    padded.set_margin_top(14)
    padded.set_margin_bottom(14)
    padded.set_margin_start(14)
    padded.set_margin_end(14)
    padded.append(content)

    scroller.set_child(padded)
    scroller.body = padded  # pages append their blocks here
    return scroller
