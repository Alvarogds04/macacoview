"""Entry point: ``python3 -m pc_ai_monitor``."""

import sys

# The package is imported by name because the launcher puts gui/ on PYTHONPATH;
# the analyzer resolves it through extraPaths in pyrightconfig.json.
from pc_ai_monitor.app import main  # pyright: ignore[reportMissingImports]

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
