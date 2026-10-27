"""The A0.2 package layout imports and stays on the declared layers."""

from __future__ import annotations

import importlib

import aconsole


def test_version_and_roots_import() -> None:
    assert aconsole.__version__ == "0.0.0"
    for name in aconsole.package_roots():
        module = importlib.import_module(name)
        assert module.__name__ == name
