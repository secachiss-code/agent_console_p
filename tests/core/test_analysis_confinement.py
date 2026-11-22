"""Project trust, bubblewrap preflight, confinement and cancellation."""

from __future__ import annotations

import ast
import os
import signal
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from aconsole.core.application.services.project_analysis import (
    Profile,
    ProjectTrust,
    Refusal,
    RunSpec,
    begin_analysis,
)
from aconsole.core.infrastructure.analyzers.confinement import Bubblewrap
from aconsole.core.infrastructure.persistence.engine import MIGRATIONS, migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Project
from aconsole.core.infrastructure.persistence.repositories.analysis_trust import AnalysisTrustRepository

_UNAVAILABLE = Refusal(code=2015, name="CONFINEMENT_UNAVAILABLE")
_PACKAGING = Path(__file__).resolve().parents[2] / "packaging" / "build_deb.py"


class _MemoryTrust:
    def __init__(self, trust: ProjectTrust | None) -> None:
        self.trust: ProjectTrust | None = trust

    def get(self, project_id: str) -> ProjectTrust | None:
        _ = project_id
        return self.trust


def _spec(tmp_path: Path, command: tuple[str, ...], *, profile: Profile = Profile.STATIC) -> RunSpec:
    project = tmp_path / "project"
    project.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return RunSpec(profile=profile, project=project, scratch=scratch, command=command, tool_env={"LC_ALL": "C"})


@pytest.mark.parametrize(
    ("trust", "refusal"),
    [
        (None, Refusal(code=2001, name="PROJECT_NOT_FOUND")),
        (ProjectTrust(), Refusal(code=2016, name="BUILD_UNTRUSTED")),
    ],
)
def test_build_refusal_happens_before_probe(tmp_path: Path, trust: ProjectTrust | None, refusal: Refusal) -> None:
    spec = _spec(tmp_path, ("/usr/bin/true",), profile=Profile.BUILD)
    assert begin_analysis(_MemoryTrust(trust), "p", spec, _unexpected_run, lambda: False) == refusal


def _unexpected_run(*_args: object) -> int:
    pytest.fail("project trust must be checked before calling the adapter")


def _binary(path: Path, script: str) -> Path:
    _ = path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("mode", ["missing", "non-executable", "invalid-executable", "userns-denied"])
def test_unavailable_confinement_never_executes_tool(tmp_path: Path, mode: str) -> None:
    marker = tmp_path / "ran"
    spec = _spec(tmp_path, ("/usr/bin/touch", str(marker)))
    binary = tmp_path / "bwrap"
    if mode == "non-executable":
        _ = binary.write_text("", encoding="utf-8")
    elif mode == "invalid-executable":
        _ = _binary(binary, "invalid executable")
    elif mode == "userns-denied":
        _ = _binary(binary, "#!/bin/sh\nexit 1\n")
    adapter = Bubblewrap(binary=binary)
    assert (
        begin_analysis(_MemoryTrust(ProjectTrust(trusted=True)), "p", spec, adapter.run, lambda: False) == _UNAVAILABLE
    )
    assert not marker.exists()


def test_argv_applies_confinement_to_the_probe_and_tool(tmp_path: Path) -> None:
    binary = tmp_path / "bwrap"
    log = tmp_path / "argv"
    _ = _binary(
        binary,
        f"""#!{sys.executable}
import json, pathlib, sys
with pathlib.Path({str(log)!r}).open('a') as stream:
    print(json.dumps(sys.argv[1:]), file=stream)
""",
    )
    spec = _spec(tmp_path, ("/usr/bin/echo", "analysis"))
    result = begin_analysis(_MemoryTrust(ProjectTrust()), "p", spec, Bubblewrap(binary=binary).run, lambda: False)
    assert result == 0
    probe, tool = (msgspec.json.decode(line, type=list[str]) for line in log.read_text(encoding="utf-8").splitlines())
    assert probe[:-1] == tool[:-2]
    assert probe[-1] == "/usr/bin/true"
    assert tool[-2:] == ["/usr/bin/echo", "analysis"]
    assert {"--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-net", "--disable-userns"} <= set(probe)
    assert probe[probe.index(str(spec.project)) - 1] == "--ro-bind"
    assert [probe[index + 1] for index, item in enumerate(probe) if item == "--bind"] == [str(spec.scratch)]
    assert probe[probe.index("HOME") + 1] == str(spec.scratch)
    assert str(Path.home()) not in probe


def test_cancel_reaps_analysis_process(tmp_path: Path) -> None:
    marker = tmp_path / "pid"
    binary = _binary(
        tmp_path / "bwrap",
        f"#!{sys.executable}\nimport os, sys\ncmd = sys.argv[sys.argv.index('--') + 1:]\nos.execv(cmd[0], cmd)\n",
    )
    argv = (
        sys.executable,
        "-c",
        "import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)",
        str(marker),
    )
    spec = _spec(tmp_path, argv)
    result = begin_analysis(_MemoryTrust(ProjectTrust()), "p", spec, Bubblewrap(binary=binary).run, marker.exists)
    assert result == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_overlapping_scratch_and_project_fail_closed(tmp_path: Path) -> None:
    binary = _binary(tmp_path / "bwrap", "#!/bin/sh\nexit 0\n")
    spec = _spec(tmp_path, ("/usr/bin/true",))
    nested = spec.project / "scratch"
    nested.mkdir()
    assert Bubblewrap(binary=binary).run(replace(spec, scratch=nested), lambda: False) == _UNAVAILABLE


def test_live_bubblewrap(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        (
            "/bin/sh",
            "-ec",
            (
                'test "$(pwd)" = "$HOME" && test ! -e "$1" && test -r "$2" && '
                '! touch "$2" && ! touch /outside && touch "$HOME/inside" && '
                'test "$(readlink /proc/self/ns/net)" != "$3"'
            ),
            "probe",
            str(Path.home() / ".local"),
            str(tmp_path / "project" / "tracked"),
            str(Path("/proc/self/ns/net").readlink()),
        ),
    )
    (spec.project / "tracked").touch()
    binary = os.environ.get("ACONSOLE_TEST_BWRAP")
    adapter = Bubblewrap(binary=Path(binary)) if binary else Bubblewrap()
    result = adapter.run(spec, lambda: False)
    if result == _UNAVAILABLE and binary is None:
        pytest.skip("bubblewrap or required namespaces unavailable on this host")
    assert result == 0
    assert (spec.scratch / "inside").is_file()

    token = f"aconsole-an1-{tmp_path.parent.name}-{os.getpid()}"
    child_argv = (
        "/bin/sh",
        "-c",
        """setsid /bin/bash -c 'exec -a "$1" /usr/bin/sleep 30' child "$1" & wait""",
        "parent",
        token,
    )
    deadline = time.monotonic() + 5
    child_seen = False

    def cancel_child() -> bool:
        nonlocal child_seen
        child_seen = _process_exists(token)
        return child_seen or time.monotonic() >= deadline

    assert adapter.run(replace(spec, command=child_argv), cancel_child) == -signal.SIGKILL
    assert child_seen
    deadline = time.monotonic() + 2
    while _process_exists(token) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_exists(token)


def _process_exists(token: str) -> bool:
    argv = token.encode() + b"\0" + b"30\0"
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if cmdline.read_bytes() == argv:
                return True
        except OSError:
            continue
    return False


def test_package_depends_on_bubblewrap() -> None:
    module = ast.parse(_PACKAGING.read_text(encoding="utf-8"))
    depends = [
        node
        for node in module.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_DEPENDS"
    ]
    assert len(depends) == 1
    value = depends[0].value
    assert isinstance(value, ast.Tuple)
    assert any(isinstance(item, ast.Constant) and item.value == "bubblewrap" for item in value.elts)


def test_existing_project_defaults_to_untrusted_and_survives_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'trust.db'}"
    previous = "0013_note_fts"
    migrate(url, previous)
    engine = write_engine(url)

    def create_project(project_id: str) -> None:
        with Session(engine) as session:
            session.add(
                Project(
                    id=project_id,
                    name=project_id,
                    root_path=str(tmp_path / project_id),
                    default_branch="main",
                    languages=[],
                    created_at=datetime(2026, 11, 22, tzinfo=UTC),
                ),
            )
            session.commit()

    create_project("existing")
    migrate(url, "head")
    with Session(engine) as session:
        repository = AnalysisTrustRepository(session)
        assert repository.get("missing") is None
        assert repository.save("missing", ProjectTrust(trusted=True)) is False
        assert repository.get("existing") == ProjectTrust()
        assert repository.save("existing", ProjectTrust(trusted=True))
        session.commit()
    with Session(engine) as session:
        repository = AnalysisTrustRepository(session)
        assert repository.get("existing") == ProjectTrust(trusted=True)
        assert repository.save("existing", ProjectTrust())
        assert repository.get("existing") == ProjectTrust()
        session.commit()
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, previous)
    create_project("after-downgrade")
    engine.dispose()
