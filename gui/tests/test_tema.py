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


if __name__ == "__main__":
    unittest.main()
