"""How the core's Git reads hold up on a large repository: 10 000 commits, 20 000 files, 8 branches."""

from __future__ import annotations

import statistics
import subprocess
import time
from typing import TYPE_CHECKING, Final, override

import pytest

from aconsole.contracts.common import ProjectId
from aconsole.contracts.git import GitGraphParams
from aconsole.core.application.services.git_state import GitCatalog, GitProject, GitStateService, GitTab
from aconsole.core.infrastructure.git.history import GitHistory

if TYPE_CHECKING:
    from pathlib import Path

_GIT: Final = "/usr/bin/git"
_COMMITS: Final = 10_000
_FILES: Final = 20_000
_BRANCHES: Final = 8
_RUNS: Final = 5
_SNAPSHOT_LIMIT_MS: Final = 150.0
_PROBE_LIMIT_MS: Final = 20.0
# Not a promise, a tripwire: nine `git status` over 20 000 files measured 190 ms here, four at a time.
_LOOK_LIMIT_MS: Final = 400.0
_MS: Final = 1000.0
_PROJECT: Final = "p1"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [_GIT, "-C", str(repo), "-c", "user.name=s", "-c", "user.email=s@e", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _stream() -> str:
    """Every file in the first commit, then one changed per commit."""
    lines = ["commit refs/heads/develop", "committer s <s@e> 1700000000 +0000", "data 5", "root", ""]
    for number in range(_FILES):
        lines.extend((f"M 100644 inline src/d{number % 200}/f{number}.txt", "data 2", "x", ""))
    for number in range(1, _COMMITS):
        message = f"commit {number}"
        body = f"line {number}"
        lines.extend((
            "commit refs/heads/develop",
            f"committer s <s@e> {1_700_000_000 + number} +0000",
            f"data {len(message)}",
            message,
            f"M 100644 inline src/d{number % 200}/f{number % _FILES}.txt",
            f"data {len(body)}",
            body,
            "",
        ))
    return "\n".join(lines)


@pytest.fixture(scope="module")
def large_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("scale")
    repo = base / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    _ = subprocess.run(
        [_GIT, "-C", str(repo), "fast-import", "--quiet"], input=_stream(), text=True, check=True
    )
    _ = _git(repo, "checkout", "-qf", "develop")
    for number in range(_BRANCHES):
        tree = base / f"wt{number}"
        _ = _git(repo, "worktree", "add", "-q", "-b", f"ac/work-{number}", str(tree), "HEAD~50")
        changed = tree / "src" / f"d{number}" / f"f{number}.txt"
        _ = changed.write_text(f"work {number}\n", encoding="utf-8")
        _ = _git(tree, "commit", "-qam", f"work {number}")
    return repo


class _Catalog(GitCatalog):
    def __init__(self, root: Path) -> None:
        self._root: Path = root

    @override
    def git_project(self, project_id: str) -> GitProject | None:
        return GitProject(project_id=project_id, root=str(self._root), default_branch="develop")

    @override
    def git_tabs(self, project_id: str) -> tuple[GitTab, ...]:
        _ = project_id
        return ()


def _median_ms(work: list[float]) -> float:
    return statistics.median(work) * _MS


def test_a_graph_snapshot_and_a_look_at_every_worktree(large_repo: Path) -> None:
    catalog = _Catalog(large_repo)
    graphs: list[float] = []
    looks: list[float] = []
    for _ in range(_RUNS):
        # A new service each time: the probe cache must not hide the first look's cost.
        service = GitStateService(GitHistory(), lambda: catalog)
        started = time.perf_counter()
        state = service.state(_PROJECT)
        looked = time.perf_counter()
        graph = service.graph(GitGraphParams(project_id=ProjectId(_PROJECT)))
        looks.append(looked - started)
        graphs.append(time.perf_counter() - looked)
        assert len(state.worktrees) == _BRANCHES + 1
        assert graph.nodes
    assert _median_ms(graphs) <= _SNAPSHOT_LIMIT_MS, f"graph: {_median_ms(graphs):.0f} ms"
    assert _median_ms(looks) <= _LOOK_LIMIT_MS, f"state: {_median_ms(looks):.0f} ms"


def test_a_conflict_check(large_repo: Path) -> None:
    reader = GitHistory()
    target = _git(large_repo, "rev-parse", "develop")
    source = _git(large_repo, "rev-parse", "ac/work-0")
    times: list[float] = []
    for _ in range(_RUNS):
        started = time.perf_counter()
        probe = reader.merge_probe(large_repo, target, source)
        times.append(time.perf_counter() - started)
        assert probe.clean
    assert _median_ms(times) <= _PROBE_LIMIT_MS, f"{_median_ms(times):.0f} ms"
