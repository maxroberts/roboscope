"""Smoke test: confirms the roboscope package installs and imports.

Details:
    Exists so `uv run pytest` has something to run from the first commit. Replace or
    extend with real tests as modules are added.
"""

import roboscope


def test_package_imports():
    """The package imports and carries its module docstring."""
    assert roboscope.__doc__
