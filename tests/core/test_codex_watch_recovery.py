"""A late container must not permanently disable the Codex watcher."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tests.core.test_wire import _daemon

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.core.infrastructure.ipc.session import Connection


@pytest.mark.parametrize("first_error", [False, True])
def test_codex_watch_retries_container_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, first_error: bool,
) -> None:
    async def run() -> None:
        from aconsole.core import bootstrap

        daemon = _daemon(tmp_path, serve_ingest=False)
        now = datetime.now(UTC).astimezone()
        home = tmp_path / "home"
        folder = home / ".codex" / "sessions" / f"{now:%Y/%m/%d}"
        folder.mkdir(parents=True)
        lines = [
            {"type": "session_meta", "payload": {"cwd": "/work", "id": "session-a"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
        ]
        (folder / "rollout-a.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        tries = 0
        observed = asyncio.Event()
        sessions: list[str] = []

        def agent_home(_name: str) -> Path | None:
            nonlocal tries
            tries += 1
            if tries == 1:
                if first_error:
                    raise OSError
                return None
            return home

        def turn(cwd: str, *, working: bool) -> None:
            assert (cwd, working) == ("/work", True)
            observed.set()

        monkeypatch.setattr(daemon._host, "agent_home", agent_home)
        monkeypatch.setattr(daemon.tabs, "codex_turn", turn)
        monkeypatch.setattr(daemon.tabs, "codex_session", lambda _cwd, session: sessions.append(session))
        monkeypatch.setattr(bootstrap, "_CODEX_TURN_POLL_S", 0.001)
        task = asyncio.create_task(daemon._watch_codex_turns())
        try:
            await asyncio.wait_for(observed.wait(), 1)
            await asyncio.sleep(0)
            assert sessions == ["session-a"]
            assert tries == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_sidecar_disconnect_uses_the_kernel_peer_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from aconsole.contracts.ipc import ResponseOk
    from aconsole.core.infrastructure.ipc.framing import decode_envelope
    from aconsole.sidecar.ipc import initialize
    from tests.core.test_wire import _connect, _stop

    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        departed = asyncio.Event()
        pids: list[int | None] = []

        def left(connection: Connection) -> None:
            if connection.sidecar_claim is not None:
                pids.append(connection.peer_pid)
                departed.set()

        monkeypatch.setattr(daemon.tabs, "claim_sidecar", lambda *_args: True)
        monkeypatch.setattr(daemon, "_sidecar_left", left)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            reader, writer = await asyncio.open_unix_connection(daemon.socket)
            try:
                writer.write(initialize("tab-a", "proof", 1))
                await writer.drain()
                reply = decode_envelope(await asyncio.wait_for(reader.readline(), 2))
                assert isinstance(reply, ResponseOk)
            finally:
                writer.close()
                await writer.wait_closed()
            await asyncio.wait_for(departed.wait(), 2)
            assert pids == [os.getpid()], "initialize.pid=1 is not the peer's PID in the core namespace"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(run())
