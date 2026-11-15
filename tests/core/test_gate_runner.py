"""Gate commands stay in the selected container and use trusted argv only."""

from pathlib import Path

import pytest

from aconsole.core.application.ports.gate_runner import GateRunnerUnavailableError
from aconsole.core.infrastructure.containers.podman import Podman
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner
from aconsole.core.infrastructure.gates.verdict import compact_verdict


def _runner(tmp_path: Path, *, code: int = 0, running: bool = True) -> ContainerGateRunner:
    binary = tmp_path / "podman"
    binary.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'inspect':\n"
        f" print(json.dumps([dict(Id='container', Name='container', State=dict(Running={running!r}))]))\n"
        "else:\n"
        " print(json.dumps(sys.argv[1:]))\n"
        " print('error: ' + 'x' * 1100000)\n"
        f" sys.exit({code})\n"
    )
    binary.chmod(0o700)
    return ContainerGateRunner(
        podman=Podman(binary), container="container", user="sas", worktree="/work tree",
        profile=(("pytest", "tests/core"),), state_home=tmp_path,
    )


def test_only_exact_profile_argv_can_run(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with pytest.raises(ValueError, match="frozen profile"):
        runner.run(("pytest", "--override-ini", "anything"), tier=1)
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
        _runner(tmp_path, running=False).run(("pytest", "tests/core"), tier=1)
    assert not (tmp_path / "aconsole/gates").exists()


def test_missing_podman_never_falls_back_to_host(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "podman").unlink()
    with pytest.raises(GateRunnerUnavailableError):
        runner.run(("pytest", "tests/core"), tier=1)
