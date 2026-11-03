"""What the package carries is read from the lock, and a mistake there only shows when the installed app starts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "build_deb.py"


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_deb", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_package_carries_what_runs_and_nothing_else() -> None:
    core, ui = _builder()._closure()
    assert {"mcp", "sqlalchemy", "uvicorn"} <= core
    assert "cryptography" in core, "`pyjwt[crypto]`: an extra's needs are listed apart"
    assert {"pygobject", "rapidfuzz", "msgspec"} <= ui
    assert "pygobject" not in core, "GTK cannot load under free threading"
    assert not {"pytest", "ruff", "basedpyright", "pygobject-stubs"} & ui, "tools for development stay out"
    assert not {"pywin32", "httpx2-jsfetch"} & ui, "other platforms' dependencies stay out"
