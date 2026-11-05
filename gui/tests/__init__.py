"""Test package: dotted runs (`python3 -m unittest tests.test_x`) import this
first, so the sandbox HOME is in place before any test module loads."""

from tests import _sandbox  # noqa: F401 - importing it enters the sandbox
