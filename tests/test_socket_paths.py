"""Long data paths remain real while Unix-domain transports use private short addresses."""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

from tests.socket_paths import short_runtime_path, short_socket_path

_HOME_AT_COLLECTION = Path.home()
_ADDRESS_LIMIT = 100
_RUNTIME_LIMIT = 60


def test_home_is_private_before_collection_and_in_child_processes() -> None:
    assert Path.home() == _HOME_AT_COLLECTION
    assert Path.home().name.startswith("aconsole-test-home-")
    child = subprocess.check_output([sys.executable, "-c", "from pathlib import Path; print(Path.home())"], text=True)
    assert child.strip() == str(Path.home())


def test_short_address_does_not_change_or_create_the_requested_path() -> None:
    path = Path(os.environ["ACONSOLE_TEST_SOCKET_DIR"]) / "unused.sock"
    assert short_socket_path(path) == path
    assert not path.exists()


def test_long_path_data_and_real_socket_round_trip(tmp_path: Path) -> None:
    data = tmp_path / ("deep" * 30)
    data.mkdir()
    marker = data / "data.txt"
    marker.write_text("kept under the long path\n", encoding="utf-8")
    requested = data / "core.sock"
    address = short_socket_path(requested)
    assert len(os.fsencode(requested)) > _ADDRESS_LIMIT
    assert len(os.fsencode(address)) <= _ADDRESS_LIMIT
    assert short_socket_path(requested) == address
    assert short_socket_path(data / "other.sock") != address
    assert stat.S_IMODE(address.parent.stat().st_mode) == 0o700
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(address))
        server.listen(1)
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(address))
            peer, _ = server.accept()
            with peer:
                client.sendall(b"test")
                assert peer.recv(4) == b"test"
    assert marker.read_text() == "kept under the long path\n"


def test_runtime_directory_leaves_room_for_daemon_socket_names(tmp_path: Path) -> None:
    original = tmp_path / ("long" * 30)
    runtime = short_runtime_path(original)
    assert len(os.fsencode(runtime)) <= _RUNTIME_LIMIT
    assert len(os.fsencode(runtime / "aconsole/core.sock")) <= _ADDRESS_LIMIT
