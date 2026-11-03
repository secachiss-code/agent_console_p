"""Setup shared by every test: Git reads a config of its own, and the window's state stays in a temporary folder."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name to commit with, and nothing from the machine.

    A CI runner has no `user.name`, and a developer's own config may sign commits or add hooks. Subprocesses inherit
    the environment, so a core started by a test reads the same config.
    """
    config: Path = tmp_path_factory.getbasetemp() / "gitconfig"
    if not config.exists():
        _ = config.write_text("[user]\n\tname = AConsole Test\n\temail = test@aconsole.invalid\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the window remembers (folded sections) stays out of the real `~/.local/state`."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
