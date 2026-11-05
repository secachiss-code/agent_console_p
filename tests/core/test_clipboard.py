"""Attachments stay private, untracked, validated, and readable by the selected agent."""

from __future__ import annotations

import struct
import subprocess
import zlib
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import PreparedImagePaste, PrepareImagePasteParams
from aconsole.contracts.common import AgentKind, ClientRole, TabId
from aconsole.contracts.ipc import ResponseErr
from aconsole.core.application.services.tabs import TabRequestError
from aconsole.core.infrastructure.clipboard import MAX_IMAGE_BYTES, store_image, validate_png
from aconsole.core.infrastructure.containers.podman import ContainerError
from aconsole.core.infrastructure.tabs import HostTabs
from tests.core.test_tabs import _call, _open, _ready, _seed, _service

_GIT = Path("/usr/bin/git")


def png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff"))
        + chunk(b"IEND", b"")
    )


def test_private_persistent_untracked_images(tmp_path: Path) -> None:
    subprocess.run((str(_GIT), "init", str(tmp_path)), check=True, capture_output=True)
    first = store_image(str(tmp_path), png(), git=_GIT)
    second = store_image(str(tmp_path), png(), git=_GIT)
    assert first != second
    assert first.read_bytes() == second.read_bytes() == png()
    assert first.stat().st_mode & 0o777 == 0o600
    assert first.parent.stat().st_mode & 0o777 == 0o700
    assert not subprocess.run(
        (str(_GIT), "-C", str(tmp_path), "status", "--porcelain"),
        check=True, capture_output=True,
    ).stdout
    assert (tmp_path / ".git/info/exclude").read_text().count("/.aconsole/clipboard/") == 1


@pytest.mark.parametrize("payload", [b"", b"text", png()[:-1], png() + b"extra", png(100000, 100000)])
def test_invalid_images(payload: bytes) -> None:
    with pytest.raises(ValueError, match=r"PNG|megapixels"):
        validate_png(payload)


@pytest.mark.parametrize("directory", [".aconsole", ".aconsole/clipboard"])
def test_refuse_symlink_directories(tmp_path: Path, directory: str) -> None:
    subprocess.run((str(_GIT), "init", str(tmp_path)), check=True, capture_output=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / directory
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match=r"directory|symbolic"):
        store_image(str(tmp_path), png(), git=_GIT)
    assert list(outside.iterdir()) == []


def test_wire_size_bound() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(
            msgspec.json.encode({"tab_id": "tab-a", "png": b"x" * (MAX_IMAGE_BYTES + 1)}),
            type=PrepareImagePasteParams,
        )


@pytest.mark.parametrize("agent", list(AgentKind))
def test_same_image_route_for_every_agent(agent: AgentKind) -> None:
    service, ports = _service(_seed())
    params = msgspec.structs.replace(_open(), agent=agent)
    tab = service.open_tab(params)
    received: list[tuple[str, str, bytes]] = []

    def prepare(worktree: str, container: str, data: bytes) -> str:
        received.append((worktree, container, data))
        return str(Path(worktree) / ".aconsole/clipboard/image.png")

    ports.prepare_image = prepare
    result = service.dispatch("terminal/prepareImagePaste", msgspec.json.encode(PrepareImagePasteParams(
        tab_id=tab.id, png=png(),
    )))
    assert isinstance(result, PreparedImagePaste)
    assert received == [(tab.worktree_path, "agentbox", png())]
    assert result.path.endswith("/.aconsole/clipboard/image.png")


def test_image_ipc_requires_ui_and_existing_tab() -> None:
    service, _ports = _service(_seed())
    connection = _ready(service)
    params = PrepareImagePasteParams(tab_id=TabId("missing"), png=png())
    result = _call(connection, 2, "terminal/prepareImagePaste", params)
    assert isinstance(result, ResponseErr)
    assert result.error.message == "TAB_NOT_FOUND"
    connection.role = ClientRole.CLI
    result = _call(connection, 3, "terminal/prepareImagePaste", params)
    assert isinstance(result, ResponseErr)
    assert result.error.message == "ROLE_FORBIDDEN"


def test_container_must_read_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run((str(_GIT), "init", str(tmp_path)), check=True, capture_output=True)
    ports = HostTabs()

    def unreadable(_container: str, _user: str, command: tuple[str, ...]) -> str:
        assert command[:2] == ("test", "-r")
        assert Path(command[2]).read_bytes() == png()
        message = "not mounted"
        raise ContainerError(message)

    monkeypatch.setattr(ports._podman, "run_in", unreadable)
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        ports.prepare_image(str(tmp_path), "container", png())
    assert list((tmp_path / ".aconsole/clipboard").iterdir()) == []


def test_git_failure_is_reported_without_preparing_an_attachment(tmp_path: Path) -> None:
    ports = HostTabs()
    with pytest.raises(TabRequestError, match="PATH_NOT_ALLOWED"):
        ports.prepare_image(str(tmp_path), "container", png())
    assert not (tmp_path / ".aconsole").exists()
