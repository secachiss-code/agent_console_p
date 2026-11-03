"""Measure the Git page's costs on a generated repository, through a real core process.

Prints what the plan asks to be measured rather than promised: the first graph page, paging, how long another request
waits while Git runs, and how many git processes a watched and an unwatched project cost. Frame rate is not here: it
needs a real display, not this tool.

    PYTHONPATH=src .venv-core/bin/python tools/git_bench.py --commits 20000 --worktrees 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

from aconsole.contracts.app import EmptyParams, HealthResult, ProjectAddParams, ProjectView
from aconsole.contracts.git import GitGraphParams, GitGraphResult, GitStateParams, GitStateResult
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.daemon import core_python
from aconsole.ui.viewmodels.git_vm import GraphBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aconsole.contracts.common import ProjectId

_GIT: Final = "/usr/bin/git"
_MS: Final = 1000.0
_PAGE: Final = 200
_IDLE_S: Final = 15.0
_PING_GAP_S: Final = 0.005
_PERCENTILES: Final = 20
_P95: Final = 18
_TRACE_START: Final = '"event":"start"'


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [_GIT, "-C", str(repo), "-c", "user.name=b", "-c", "user.email=b@e", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _build(repo: Path, commits: int, worktrees: int) -> None:
    """A long line with a file changed per commit, and a branched worktree holding a little work each."""
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    lines: list[str] = []
    for number in range(1, commits + 1):
        body = f"line {number}\n"
        message = f"commit {number}\n"
        lines.extend((
            "commit refs/heads/develop",
            f"committer b <b@e> {1_700_000_000 + number} +0000",
            f"data {len(message)}",
            message,
            f"M 100644 inline f{number % 500}.txt",
            f"data {len(body)}",
            body,
        ))
    _ = subprocess.run(
        [_GIT, "-C", str(repo), "fast-import", "--quiet"], input="\n".join(lines), text=True, check=True
    )
    _ = _git(repo, "checkout", "-qf", "develop")
    start = _git(repo, "rev-parse", f"HEAD~{min(50, commits - 1)}")
    for number in range(worktrees):
        tree = repo.parent / f"wt{number}"
        _ = _git(repo, "worktree", "add", "-q", "-b", f"ac/work-{number}", str(tree), start)
        (tree / f"work{number}.txt").write_text("work\n", encoding="utf-8")
        _ = _git(tree, "add", ".")
        _ = _git(tree, "commit", "-qm", f"work {number}")


def _row(name: str, samples: list[float]) -> str:
    ordered = sorted(samples)
    p95 = statistics.quantiles(ordered, n=_PERCENTILES)[_P95] if len(ordered) > 1 else ordered[0]
    return f"| {name} | {len(ordered)} | {statistics.median(ordered):.1f} | {p95:.1f} | {ordered[-1]:.1f} |"


async def _timed(call: Callable[[], Awaitable[object]], times: int) -> list[float]:
    found: list[float] = []
    for _ in range(times):
        started = time.perf_counter()
        _ = await call()
        found.append((time.perf_counter() - started) * _MS)
    return found


def _spawned(trace: Path) -> int:
    if not trace.exists():
        return 0
    return trace.read_text(encoding="utf-8", errors="replace").count(_TRACE_START)


async def _measure(client: CoreClient, project: ProjectId, trace: Path, *, runs: int) -> list[str]:
    rows: list[str] = []

    async def state() -> GitStateResult:
        return await client.request("git/state", GitStateParams(project_id=project), GitStateResult)

    async def page(cursor: str | None = None) -> GitGraphResult:
        params = GitGraphParams(project_id=project, cursor=cursor, limit=_PAGE)
        return await client.request("git/graph", params, GitGraphResult)

    async def health() -> HealthResult:
        return await client.request("app/health", EmptyParams(), HealthResult)

    before = _spawned(trace)
    first_state = await _timed(state, 1)
    rows.append(_row("git/state, first", first_state))
    rows.append(f"| git processes for that state | {_spawned(trace) - before} | | | |")
    rows.append(_row("git/state, unchanged", await _timed(state, runs)))
    first_page = await _timed(page, 1)
    rows.append(_row("git/graph, first page", first_page))
    rows.append(_row("git/graph, first page again", await _timed(page, runs)))

    cursor = (await page()).next_cursor
    paging: list[float] = []
    nodes = list((await page()).nodes)
    for _ in range(runs):
        if cursor is None:
            break
        started = time.perf_counter()
        result = await page(cursor)
        paging.append((time.perf_counter() - started) * _MS)
        cursor = result.next_cursor
        nodes.extend(result.nodes)
    if paging:
        rows.append(_row(f"git/graph, next page ({_PAGE})", paging))

    started = time.perf_counter()
    builder = GraphBuilder(None)
    for node in nodes:
        _ = builder.commit(node)
    rows.append(_row(f"lanes for {len(nodes)} rows", [(time.perf_counter() - started) * _MS]))

    rows.append(_row("app/health, nothing else running", await _timed(health, runs)))
    load = [asyncio.create_task(_timed(page, runs)), asyncio.create_task(_timed(state, runs))]
    busy: list[float] = []
    while not all(task.done() for task in load):
        busy.extend(await _timed(health, 1))
        await asyncio.sleep(_PING_GAP_S)
    _ = await asyncio.gather(*load)
    rows.append(_row("app/health, for as long as Git requests run", busy))

    # The core keeps watching a project a client asked about, for a while after it asked.
    _ = await state()
    before = _spawned(trace)
    await asyncio.sleep(_IDLE_S)
    rate = (_spawned(trace) - before) * 60.0 / _IDLE_S
    rows.append(f"| git processes per minute, watched and idle | {rate:.0f} | | | |")
    await asyncio.sleep(35.0)
    before = _spawned(trace)
    await asyncio.sleep(_IDLE_S)
    rate = (_spawned(trace) - before) * 60.0 / _IDLE_S
    rows.append(f"| git processes per minute, nobody looking | {rate:.0f} | | | |")
    return rows


async def _main(arguments: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="ac-bench-") as folder:
        base = Path(folder)
        repo = base / "repo"
        started = time.perf_counter()
        _build(repo, arguments.commits, arguments.worktrees)
        print(f"repository: {arguments.commits} commits, {arguments.worktrees} worktrees, "  # noqa: T201 -- a report
              f"built in {time.perf_counter() - started:.1f} s")
        socket = base / "s" / "core.sock"
        socket.parent.mkdir(mode=0o700)
        trace = base / "trace.json"
        environment = dict(
            os.environ,
            XDG_DATA_HOME=str(base / "data"),
            XDG_STATE_HOME=str(base / "state"),
            GIT_TRACE2_EVENT=str(trace),
        )
        command = [str(core_python()), "-m", "aconsole.core", "--serve", "--socket", str(socket)]
        daemon = subprocess.Popen(command, env=environment)  # noqa: ASYNC220 -- it outlives this call
        client = CoreClient(str(socket))
        try:
            for _ in range(200):
                if socket.exists():
                    break
                await asyncio.sleep(0.05)
            _ = await client.connect()
            project = await client.request("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            rows = await _measure(client, project.id, trace, runs=arguments.runs)
        finally:
            await client.close()
            daemon.terminate()
            _ = daemon.wait(timeout=10)
        print("| what | n | median ms | p95 ms | max ms |\n|---|---|---|---|---|")  # noqa: T201
        print("\n".join(rows))  # noqa: T201
        if arguments.json:
            print(json.dumps(rows))  # noqa: T201


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--commits", type=int, default=20_000)
    _ = parser.add_argument("--worktrees", type=int, default=8)
    _ = parser.add_argument("--runs", type=int, default=20)
    _ = parser.add_argument("--json", action="store_true")
    asyncio.run(_main(parser.parse_args()))
