"""The A0.2 package layout imports and stays on the declared layers."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import aconsole


def test_version_and_roots_import() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    package = next(item for item in lock["package"] if item["name"] == "aconsole")
    assert aconsole.__version__ == project["project"]["version"] == package["version"] == "0.1.1"
    for name in aconsole.package_roots():
        module = importlib.import_module(name)
        assert module.__name__ == name
