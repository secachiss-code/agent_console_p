"""Held C<n> acceptance tests; copy into the active suite only after root approval."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.gate import done_when

_BODY = "- First criterion is copied verbatim.\n- Second criterion is copied verbatim.\n"


def _request(
    *,
    task: str = "C1",
    status: str = "accepted",
    sections: str | None = None,
) -> str:
    if sections is None:
        sections = f"## Acceptance\n{_BODY}"
    return f"Request: example\nTask: {task}\nStatus: {status}\n\n{sections}"


@pytest.mark.parametrize("status", ["accepted", "accepted (G)", "accepted (MAIN)", "done(2026-10-31)"])
def test_contract_request_accepts_documented_statuses(status: str) -> None:
    criteria, hits = done_when._contract_request_criteria({"request.md": _request(status=status)}, "C1")

    assert hits == []
    assert criteria == "- First criterion is copied verbatim. - Second criterion is copied verbatim."


@pytest.mark.parametrize("status", ["new", "waiting", "rejected", "accepted later", "not accepted"])
def test_contract_request_rejects_ineligible_status(status: str) -> None:
    criteria, hits = done_when._contract_request_criteria({"request.md": _request(status=status)}, "C1")

    assert criteria is None
    assert hits


def test_contract_request_task_match_is_anchored_and_exact() -> None:
    requests = {
        "other.md": _request(task="C10"),
        "target.md": _request(task="C1"),
        "substring.md": "Explanation: Task: C1 is mentioned here.\nStatus: accepted\n\n## Acceptance\nwrong\n",
    }

    criteria, hits = done_when._contract_request_criteria(requests, "C1")

    assert hits == []
    assert criteria == "- First criterion is copied verbatim. - Second criterion is copied verbatim."


@pytest.mark.parametrize(
    "requests",
    [
        {},
        {"wrong-task.md": _request(task="C2")},
        {"missing-task.md": "Status: accepted\n\n## Acceptance\ncriteria\n"},
        {"duplicate-a.md": _request(), "duplicate-b.md": _request()},
        {"duplicate-line.md": "Task: C1\nTask: C1\nStatus: accepted\n\n## Acceptance\ncriteria\n"},
    ],
)
def test_contract_request_requires_one_unique_task_line(requests: dict[str, str]) -> None:
    criteria, hits = done_when._contract_request_criteria(requests, "C1")

    assert criteria is None
    assert hits


@pytest.mark.parametrize(
    "sections",
    [
        "## Acceptance\n\n",
        "## Готово, когда\n\n",
        "## Scope\nnot an acceptance section\n",
        "## Acceptance\n- One criterion.\n\n## Готово, когда\n- Another criterion.\n",
        "## Acceptance\n- One criterion.\n\n## Acceptance\n- Duplicate section.\n",
    ],
)
def test_contract_request_requires_exactly_one_nonempty_acceptance_section(sections: str) -> None:
    criteria, hits = done_when._contract_request_criteria({"request.md": _request(sections=sections)}, "C1")

    assert criteria is None
    assert hits


def test_contract_request_accepts_the_legacy_done_when_section() -> None:
    source = _request(sections="## Готово, когда\n- First criterion.\n")

    criteria, hits = done_when._contract_request_criteria({"request.md": source}, "C1")

    assert hits == []
    assert criteria == "- First criterion."


def test_contract_check_reuses_verbatim_and_backticked_command_rules() -> None:
    criteria = "- first criterion is satisfied\n- run `hostrun test ui tests/gui/test_core_client.py`\n"
    matching = "## Done when\n- [x] first criterion is satisfied\n- [x] run `hostrun test ui tests/gui/test_core_client.py`\n"
    missing_command = "## Done when\n- [x] first criterion is satisfied\n"
    rewritten = "## Done when\n- [x] first requirement completed\n- [x] run `hostrun test ui tests/gui/test_core_client.py`\n"

    assert done_when.check_contract(matching, criteria, "C1") == []
    assert any(hit["rule"] == "done-when-dropped" for hit in done_when.check_contract(missing_command, criteria, "C1"))
    assert any(hit["rule"] == "done-when-rewritten" for hit in done_when.check_contract(rewritten, criteria, "C1"))


def test_core_task_brief_lookup_remains_on_core_md() -> None:
    phase = "A7.1"
    criterion = "hostrun gate A7.1 returns zero."
    source = f"**A7.1 · Writer** **Готово, когда:** {criterion} **Запрещено:** baseline."
    progress = f"## Done when\n- [x] {criterion}\n"

    assert done_when.check(progress, source, phase) == []


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def test_request_sources_use_the_task_base_when_file_is_tracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "gate test")
    _git(repo, "config", "user.email", "gate-test@example.invalid")
    path = repo / "docs/contracts/requests/client.md"
    path.parent.mkdir(parents=True)
    original = _request()
    path.write_text(original, encoding="utf-8")
    _git(repo, "add", "docs/contracts/requests/client.md")
    _git(repo, "commit", "-qm", "accepted request at base")
    base = _git(repo, "rev-parse", "HEAD").decode().strip()
    path.write_text(_request(status="rejected"), encoding="utf-8")

    sources = done_when._contract_request_sources(repo, base)

    assert sources == {"docs/contracts/requests/client.md": original}


def test_request_sources_read_ignored_untracked_contract_docs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "gate test")
    _git(repo, "config", "user.email", "gate-test@example.invalid")
    _git(repo, "commit", "--allow-empty", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").decode().strip()
    exclude = repo / ".git/info/exclude"
    exclude.write_text("/docs/contracts/requests/*.md\n", encoding="utf-8")
    path = repo / "docs/contracts/requests/client.md"
    path.parent.mkdir(parents=True)
    source = _request()
    path.write_text(source, encoding="utf-8")

    sources = done_when._contract_request_sources(repo, base)

    assert sources == {"docs/contracts/requests/client.md": source}
