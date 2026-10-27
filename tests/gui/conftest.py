"""Fixtures the UI client tests share with the frontend."""

import tempfile

import pytest

from aconsole.core.infrastructure.fake.server import FakeCore


@pytest.fixture
def fake_core() -> str:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, "errors")
    core.start()
    try:
        yield path
    finally:
        core.stop()


class UiApp:
    """Handle the client tests use. Separate from ``fake_core``."""

    socket: str
    projects: int

    def __init__(self, socket: str) -> None:
        self.socket = socket
        self.projects = 0


@pytest.fixture
def ui_app(running_app: str) -> UiApp:
    return UiApp(running_app)


@pytest.fixture
def running_app() -> str:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, "demo")
    core.start()
    try:
        yield path
    finally:
        core.stop()
