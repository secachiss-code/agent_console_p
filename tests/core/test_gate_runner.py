"""Gate commands stay in the selected container and use trusted argv only."""

# pyright: reportPrivateUsage=false
# The timeout probe intentionally overrides the runner's internal deadline.

import os
from pathlib import Path

import msgspec
import pytest

from aconsole.core.application.ports.gate_runner import GateRunnerUnavailableError
from aconsole.core.infrastructure.containers.podman import Podman
from aconsole.core.infrastructure.gates import runner as runner_module
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner
from aconsole.core.infrastructure.gates.verdict import compact_verdict


class _LiveReport(msgspec.Struct, frozen=True):
    pid: int
    child: int
    cwd: str
    secret: str | None


def _runner(tmp_path: Path, *, code: int = 0, running: bool = True) -> ContainerGateRunner:
    binary = tmp_path / "podman"
    _ = binary.write_text(
        "\n".join(
            (
                "#!/usr/bin/python3",
                "import json, sys",
                "if '{{.Image}}' in sys.argv:",
                " print('sha256:' + 'a' * 64)",
                "elif sys.argv[1] == 'inspect':",
                f" print(json.dumps([dict(Id='container', Name='container', State=dict(Running={running!r}))]))",
                "else:",
                " print(json.dumps(sys.argv[1:]))",
                " print('error: ' + 'x' * 1100000)",
                f" sys.exit({code})",
                "",
            )
        )
    )
    binary.chmod(0o700)
    return ContainerGateRunner(
        podman=Podman(binary),
        container="container",
        user="sas",
        worktree="/work tree",
        profile=(("pytest", "tests/core"),),
        state_home=tmp_path,
    )


def test_only_exact_profile_argv_can_run(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with pytest.raises(ValueError, match="frozen profile"):
        _ = runner.run(("pytest", "--override-ini", "anything"), tier=1)
    assert not (tmp_path / "aconsole/gates").exists()


@pytest.mark.parametrize(("tier", "seconds"), [(1, "120s"), (2, "900s")])
def test_container_argv_clean_environment_and_bounded_output(tmp_path: Path, tier: int, seconds: str) -> None:
    result = _runner(tmp_path).run(("pytest", "tests/core"), tier=tier)
    assert result.returncode == 0
    assert len(result.output.encode()) <= 1024 * 1024
    assert Path(result.log_path).stat().st_size > 1024 * 1024
    assert '"--workdir", "/work tree"' in result.output
    assert '"/usr/bin/env", "-i"' in result.output
    assert f'"--kill-after=5s", "{seconds}"' in result.output
    assert result.output.splitlines()[0].endswith('"pytest", "tests/core"]')


@pytest.mark.parametrize("code", [124, 137])
def test_timeout_verdict_is_compact(tmp_path: Path, code: int) -> None:
    result = _runner(tmp_path, code=code).run(("pytest", "tests/core"), tier=1)
    assert result.timed_out
    assert "timeout" in compact_verdict([result])
    assert len(compact_verdict([result])) <= 500


def test_stopped_container_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(GateRunnerUnavailableError, match="GATE_RUNNER_UNAVAILABLE"):
        _ = _runner(tmp_path, running=False).run(("pytest", "tests/core"), tier=1)
    assert not (tmp_path / "aconsole/gates").exists()


def test_missing_podman_never_falls_back_to_host(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "podman").unlink()
    with pytest.raises(GateRunnerUnavailableError):
        _ = runner.run(("pytest", "tests/core"), tier=1)


def test_invalid_utf8_still_obeys_output_byte_limit(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    binary = tmp_path / "podman"
    _ = binary.write_text(
        binary.read_text().replace(
            "print('error: ' + 'x' * 1100000)", "sys.stdout.flush(); sys.stdout.buffer.write(b'\\xff' * 1100000)"
        )
    )
    result = runner.run(("pytest", "tests/core"), tier=1)
    assert "\ufffd" in result.output
    assert len(result.output.encode("utf-8")) <= 1024 * 1024
    assert Path(result.log_path).stat().st_size > 1024 * 1024


def test_invalid_tier_is_rejected_before_execution(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="gate tier"):
        _ = _runner(tmp_path).run(("pytest", "tests/core"), tier=0)
    assert not (tmp_path / "aconsole/gates").exists()


def test_live_container_timeout_kills_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt in with ACONSOLE_TEST_PODMAN, ACONSOLE_TEST_CONTAINER and ACONSOLE_TEST_USER."""
    binary = os.environ.get("ACONSOLE_TEST_PODMAN")
    container = os.environ.get("ACONSOLE_TEST_CONTAINER")
    user = os.environ.get("ACONSOLE_TEST_USER")
    if not binary or not container or not user:
        pytest.skip("live Podman context was not supplied")
    podman = Podman(Path(binary))
    # Shorten only the test deadline. Production Tier 1/2 remain 120/900 seconds.
    monkeypatch.setitem(runner_module._TIMEOUTS, 1, 1)
    monkeypatch.setenv("ACONSOLE_GATE_SECRET", "must not reach command")
    script = (
        "import json, os, signal, subprocess, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child = subprocess.Popen(['/usr/bin/python3', '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); "
        "print(json.dumps(dict(pid=os.getpid(), child=child.pid, cwd=os.getcwd(), "
        "secret=os.environ.get('ACONSOLE_GATE_SECRET'))), flush=True); time.sleep(30)"
    )
    argv = ("/usr/bin/python3", "-c", script)
    runner = ContainerGateRunner(
        podman=podman,
        container=container,
        user=user,
        worktree="/tmp",  # noqa: S108 -- existing container cwd; no shared files are created
        profile=(argv,),
        state_home=tmp_path,
    )
    result = runner.run(argv, tier=1)
    assert result.timed_out
    assert result.duration_ms < 15000
    report = msgspec.json.decode(result.output.splitlines()[0], type=_LiveReport)
    assert report.cwd == "/tmp"  # noqa: S108 -- only checks cwd
    assert report.secret is None
    # A killed child may briefly be a zombie awaiting the container init's reap.
    probe = (
        "from pathlib import Path; "
        f"pids={report.pid!r}, {report.child!r}; "
        "print([pid for pid in pids if (p:=Path(f'/proc/{pid}/stat')).exists() "
        "and p.read_text().split(') ')[1][0] != 'Z'])"
    )
    assert podman.run_in(container, user, ("/usr/bin/python3", "-c", probe)).strip() == "[]"


def test_preflight_binds_only_scratch_and_uses_image(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    source = root / "copy"
    source.mkdir(parents=True)
    results = _runner(tmp_path).preflight(source, root, (("pytest", "tests/core"),))
    argv = msgspec.json.decode(results[0].output.splitlines()[0], type=list[str])
    assert argv[0] == "run"
    assert argv.count("--mount") == 1
    assert argv[argv.index("--mount") + 1] == f"type=bind,src={source},dst=/tmp/aconsole-preflight,rw"
    assert "--read-only" in argv
    assert "--network=none" in argv
    assert "--pull=never" in argv
    assert "sha256:" + "a" * 64 in argv
    assert "/work tree" not in argv


@pytest.mark.parametrize("kind", ["parent", "outside", "symlink", "comma"])
def test_preflight_rejects_invalid_mount_before_execution(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    source = root / "copy"
    source.mkdir()
    if kind == "parent":
        source = root / "copy/../copy"
    elif kind == "outside":
        source = tmp_path
    elif kind == "symlink":
        link = root / "link"
        link.symlink_to(source, target_is_directory=True)
        source = link
    else:
        source = root / "with,comma"
        source.mkdir()
    with pytest.raises(ValueError, match="mount"):
        _ = _runner(tmp_path).preflight(source, root, (("pytest", "tests/core"),))
    assert not (tmp_path / "aconsole/gates").exists()


def test_preflight_stopped_container_has_no_host_fallback(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    source = root / "copy"
    source.mkdir(parents=True)
    with pytest.raises(GateRunnerUnavailableError):
        _ = _runner(tmp_path, running=False).preflight(source, root, (("pytest", "tests/core"),))
    assert not (tmp_path / "aconsole/gates").exists()
