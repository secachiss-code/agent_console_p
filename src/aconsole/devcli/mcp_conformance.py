"""Run the cached MCP conformance suite against an isolated real daemon."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

import msgspec

from aconsole.contracts.common import ClientRole
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion, Request, ResponseOk
from aconsole.core.bootstrap import Daemon

_SUITE = "@modelcontextprotocol/conformance@0.2.0-alpha.12"
_REVISION = "2026-07-28"
_STOP_TIMEOUT = 5.0


async def _terminate(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(TimeoutError):
        _ = await asyncio.wait_for(process.wait(), _STOP_TIMEOUT)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        _ = await asyncio.wait_for(process.wait(), _STOP_TIMEOUT)


async def _command(root: Path, args: tuple[str, ...], timeout_s: float) -> int:
    process = await asyncio.create_subprocess_exec(*args, cwd=root, start_new_session=True)
    try:
        _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError:
        await _terminate(process)
        return 124
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    return process.returncode if process.returncode is not None else 1


async def _probe_tooling(root: Path) -> int:
    return await _command(root, ("npx", "--offline", "--no", _SUITE, "--help"), 30.0)


async def _run_suite(root: Path, url: str, timeout_s: float) -> int:
    return await _command(
        root,
        ("npx", "--offline", "--no", _SUITE, "server", "--url", url, "--requirements", _REVISION),
        timeout_s,
    )


def _validate_holder_response(line: bytes) -> None:
    response = msgspec.json.decode(line, type=ResponseOk)
    if response.id != 1:
        message = "daemon rejected holder initialization"
        raise ValueError(message)


async def _holder(daemon: Daemon, task: asyncio.Task[int]) -> asyncio.StreamWriter:
    async def connect() -> asyncio.StreamWriter:
        while not daemon.socket.exists():
            if task.done():
                message = "daemon stopped before socket was ready"
                raise RuntimeError(message)
            await asyncio.sleep(0.01)
        reader, writer = await asyncio.open_unix_connection(str(daemon.socket))
        try:
            version = ProtocolVersion(major=1, minor=0)
            params = InitializeParams(
                protocol=(version, version), role=ClientRole.CLI, client_name="mcp-conformance",
                client_version="0.0.0", pid=os.getpid(), capabilities=frozenset({"events.v1"}),
            )
            request = Request(id=1, method="initialize", params=msgspec.Raw(msgspec.json.encode(params)))
            writer.write(msgspec.json.encode(request) + b"\n")
            await writer.drain()
            _validate_holder_response(await reader.readline())
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise
        return writer

    return await asyncio.wait_for(connect(), 30.0)


async def run_managed(*, root: Path, suite_timeout_s: float = 90) -> int:
    """Run the suite after preflight; own and clean up all daemon state."""
    try:
        probe = await _probe_tooling(root)
        if probe:
            return probe
        with tempfile.TemporaryDirectory(prefix="aconsole-mcp-") as directory:
            private = Path(directory)
            daemon = Daemon(socket=private / "core.sock", database=private / "core.db")
            daemon.serve_ingest = True
            task = asyncio.create_task(daemon.serve())
            holder: asyncio.StreamWriter | None = None
            try:
                holder = await _holder(daemon, task)
                port = daemon.ingest_port
                if port is None:
                    message = "daemon did not start its HTTP endpoint"
                    raise RuntimeError(message)
                _ = sys.stdout.write(f"run={private.name} port={port}\n")
                _ = sys.stdout.flush()
                result = await _run_suite(root, f"http://127.0.0.1:{port}/mcp", suite_timeout_s)
            finally:
                try:
                    if holder is not None:
                        holder.close()
                        await holder.wait_closed()
                finally:
                    daemon.request_stop()
                    done, _ = await asyncio.wait({task}, timeout=_STOP_TIMEOUT)
                    if not done:
                        _ = task.cancel()
                        done, _ = await asyncio.wait({task}, timeout=_STOP_TIMEOUT)
                    if not done:
                        message = "daemon did not stop after cancellation"
                        raise RuntimeError(message)
                    _ = task.result()
        _ = sys.stdout.write(f"cleanup={private.name}\n")
        _ = sys.stdout.flush()
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        _ = sys.stderr.write(f"managed conformance error: {exc}\n")
        _ = sys.stderr.flush()
        return 1
    else:
        return result


async def _main() -> int:
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(run_managed(root=Path.cwd()))
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    try:
        return await task
    except asyncio.CancelledError:
        return 130
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            _ = loop.remove_signal_handler(signum)


def main(argv: list[str]) -> int:
    if argv:
        _ = sys.stderr.write("usage: python -m aconsole.devcli.mcp_conformance\n")
        return 2
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
