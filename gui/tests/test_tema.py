"""Theme surfaces: the Gentle theme keeps MacacoView's graphite background."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento  # noqa: F401

from pc_ai_monitor import theme

GRAPHITE = {"background": "#17191e", "element": "#1e2128", "subtle": "#24272f"}


def _write(directory: Path, name: str, variables: dict) -> None:
    (directory / name).write_text(json.dumps({"vars": variables}))


class GraphiteSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(theme.THEMES)

    def tearDown(self):
        theme.THEMES.clear()
        theme.THEMES.update(self._saved)

    def test_gentle_uses_the_graphite_surfaces(self):
        gentle = theme.get("gentle")
        for field, value in GRAPHITE.items():
            self.assertEqual(getattr(gentle, field), value, field)

    def test_gentle_pi_refresh_keeps_the_graphite_surfaces(self):
        """gentle-pi's Gentle.json paints bg/bgPanel/bgElement #06080f; the
        refresh must not bring the old near-black back, but still takes the
        other colors from the package."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp),
                "Gentle.json",
                {
                    "bg": "#06080f",
                    "bgPanel": "#06080f",
                    "bgElement": "#06080f",
                    "bgSubtle": "#0d0f14",
                    "accent": "#ABCDEF",
                },
            )
            self.assertEqual(theme.load_external(Path(tmp)), 1)
        gentle = theme.get("gentle")
        for field, value in GRAPHITE.items():
            self.assertEqual(getattr(gentle, field), value, field)
        self.assertEqual(gentle.accent, "#ABCDEF")

    def test_other_themes_still_take_their_background_from_gentle_pi(self):
        theme.THEMES["cute"] = replace(theme.THEMES["cute"], background="#000001")
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "Gentleman-Cute.json", {"bg": "#123456"})
            theme.load_external(Path(tmp))
        self.assertEqual(theme.get("cute").background, "#123456")

    def test_installed_css_paints_the_window_graphite(self):
        css = theme.colors_css(theme.get("gentle"))
        self.assertIn("@define-color theme_bg #17191e;", css)


class MutedTextContrastTests(unittest.TestCase):
    """S18 / L28: secondary text uses #8394A3 (≥ 4.5:1 on graphite), owned
    by the app like the graphite surfaces."""

    def setUp(self):
        self._saved = dict(theme.THEMES)

    def tearDown(self):
        theme.THEMES.clear()
        theme.THEMES.update(self._saved)

    def test_gentle_muted_is_the_readable_grey(self):
        self.assertEqual(theme.get("gentle").muted, MUTED)

    def test_gentle_pi_refresh_keeps_the_readable_muted(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp),
                "Gentle.json",
                {"muted": "#5C6170", "dim": "#5C6170", "accent": "#ABCDEF"},
            )
            theme.load_external(Path(tmp))
        gentle = theme.get("gentle")
        self.assertEqual(gentle.muted, MUTED)
        self.assertEqual(gentle.accent, "#ABCDEF")

    def test_muted_is_app_owned_for_gentle(self):
        self.assertIn("muted", theme.APP_OWNED_FIELDS["gentle"])

    def test_muted_passes_aa_on_every_graphite_surface(self):
        gentle = theme.get("gentle")
        for surface in (gentle.background, gentle.element, gentle.subtle):
            self.assertGreaterEqual(_contrast(gentle.muted, surface), 4.5, surface)

    def test_installed_css_paints_muted_text_with_it(self):
        css = theme.colors_css(theme.get("gentle"))
        self.assertIn(f"@define-color theme_muted {MUTED};", css)


MUTED = "#8394A3"


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


if __name__ == "__main__":
    unittest.main()
