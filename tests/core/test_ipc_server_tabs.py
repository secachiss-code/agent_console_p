"""IpcServer does not invent a tab service on an empty socket."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from aconsole.core.__main__ import serve_daemon
from aconsole.core.infrastructure.ipc.server import IpcServer
from aconsole.core.infrastructure.tabs import default_tabs

if TYPE_CHECKING:
    from pathlib import Path


def test_ipc_server_tabs_keyword_is_required() -> None:
    parameter = inspect.signature(IpcServer.__init__).parameters["tabs"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_ipc_server_tabs_keeps_the_given_service(tmp_path: Path) -> None:
    path = tmp_path / "core.sock"
    tabs = default_tabs(socket_path=str(path))
    server = IpcServer(path, tabs=tabs)
    assert server.tabs is tabs
    assert server.path == path


def test_ipc_server_tabs_main_socket_matches_the_server(tmp_path: Path) -> None:
    path = tmp_path / "core.sock"
    daemon = serve_daemon(path)
    assert daemon.socket == path
    assert daemon._host.socket_path() == str(path)
