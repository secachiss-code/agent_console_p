"""Podman fake and the distrobox.ini PATH cache."""

from __future__ import annotations

import os
import secrets
import stat
import subprocess
import time
from pathlib import Path

import pytest

from aconsole.core.infrastructure.containers.distrobox_env import DistroboxEnvCache, DistroboxError, path_from_dry_run
from aconsole.core.infrastructure.containers.podman import ContainerError, ExecRequest, Podman

_ROOT = Path(__file__).resolve().parents[2]
_PODMAN = _ROOT / "tests" / "fixtures" / "bin" / "podman"
_DISTROBOX = _ROOT / "tests" / "fixtures" / "bin" / "distrobox"
_ABSENT = 125
_SHELL = Path("/bin/sh")
_SLEEP = Path("/bin/sleep")
_WAIT_S = 5.0
_POLL_S = 0.01


def test_containers_fake_podman_inspect_start_exec_and_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "podman.log"
    box_log = tmp_path / "box.log"
    monkeypatch.setenv("PODMAN_FAKE_LOG", str(log))
    monkeypatch.setenv("PODMAN_FAKE_RUNNING", "false")
    monkeypatch.setenv("PODMAN_FAKE_ORPHANS", "42 7")
    monkeypatch.setenv("DISTROBOX_FAKE_LOG", str(box_log))
    monkeypatch.setenv("DISTROBOX_FAKE_PATH", "/opt/bin:/usr/bin")
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_text("[agent-claude]\n", encoding="utf-8")
    podman = Podman(_PODMAN)
    seen = podman.inspect("agent-claude")
    assert seen == type(seen)(name="agent-claude", ident="id-agent-claude", running=False)
    podman.start("agent-claude")
    cache = DistroboxEnvCache(ini, _DISTROBOX)
    path = cache.path_for("agent-claude")
    assert path == "/opt/bin:/usr/bin"
    assert cache.path_for("agent-claude") == path
    assert box_log.read_text(encoding="utf-8").splitlines() == ["agent-claude"]
    spec = podman.exec_spec(
        ExecRequest(
            name="agent-claude",
            command=("claude", "--session-id", "s1"),
            env={"PATH": path, "ACONSOLE_TAB": "tab-1"},
            workdir="/home/sas/Projects",
            user="sas",
        )
    )
    assert spec[0] == str(_PODMAN)
    assert "--detach-keys=" in spec
    assert spec[spec.index("--user") + 1] == "sas"
    assert spec[spec.index("--workdir") + 1] == "/home/sas/Projects"
    assert "ACONSOLE_TAB=tab-1" in spec
    assert "PATH=/opt/bin:/usr/bin" in spec
    assert spec[-5:] == ("agent-claude", "--", "claude", "--session-id", "s1")
    pids = podman.orphans("agent-claude", "tab-1")
    assert pids == (42, 7)
    recorded = log.read_text(encoding="utf-8")
    assert "start agent-claude\n" in recorded
    assert "ACONSOLE_ORPHAN_TAB=tab-1" in recorded
    assert "ACONSOLE_TAB=$ACONSOLE_ORPHAN_TAB" in recorded


def test_containers_env_cache_misses_when_the_ini_or_container_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box_log = tmp_path / "box.log"
    monkeypatch.setenv("DISTROBOX_FAKE_LOG", str(box_log))
    monkeypatch.setenv("DISTROBOX_FAKE_PATH", "/first")
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_text("one\n", encoding="utf-8")
    cache = DistroboxEnvCache(ini, _DISTROBOX)
    assert cache.path_for("agent-claude") == "/first"
    _ = ini.write_text("two\n", encoding="utf-8")
    monkeypatch.setenv("DISTROBOX_FAKE_PATH", "/second")
    assert cache.path_for("agent-claude") == "/second"
    assert cache.path_for("agent-codex") == "/second"
    assert cache.path_for("agent-codex") == "/second"
    assert box_log.read_text(encoding="utf-8").splitlines() == ["agent-claude", "agent-claude", "agent-codex"]


def test_containers_dry_run_without_path_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISTROBOX_FAKE_PATH", raising=False)
    monkeypatch.setenv("DISTROBOX_FAKE_LOG", str(tmp_path / "box.log"))
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_text("ini\n", encoding="utf-8")
    with pytest.raises(DistroboxError, match="no PATH"):
        _ = DistroboxEnvCache(ini, _DISTROBOX).path_for("agent-claude")
    assert path_from_dry_run("PATH=/bare") == "/bare"
    assert path_from_dry_run("podman --env=PATH=/eq name") == "/eq"
    assert path_from_dry_run("PATH=/old --env PATH=/new") == "/new"


def test_containers_podman_failures_and_rejected_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "podman.log"
    monkeypatch.setenv("PODMAN_FAKE_LOG", str(log))
    monkeypatch.delenv("PODMAN_FAKE_RUNNING", raising=False)
    podman = Podman(_PODMAN)
    with pytest.raises(ContainerError, match="podman command failed") as missing:
        _ = podman.inspect("absent")
    assert missing.value.code == _ABSENT
    with pytest.raises(ContainerError, match="podman command failed") as broken:
        podman.start("broken")
    assert broken.value.code == _ABSENT
    monkeypatch.setenv("PODMAN_FAKE_ORPHANS", "nope")
    with pytest.raises(ContainerError, match="orphan pid"):
        _ = podman.orphans("agent-claude", "tab-1")
    with pytest.raises(ValueError, match="tab id"):
        _ = podman.orphans("agent-claude", "tab/1")
    with pytest.raises(ValueError, match="container name"):
        _ = podman.inspect("../agent")
    request = ExecRequest(
        name="agent-claude",
        command=(),
        env={"PATH": "/bin"},
        workdir="/work",
        user="sas",
    )
    with pytest.raises(ValueError, match="command must not be empty"):
        _ = podman.exec_spec(request)
    with pytest.raises(ValueError, match="workdir"):
        _ = podman.exec_spec(ExecRequest(name="agent-claude", command=("true",), env={}, workdir="rel", user="sas"))
    with pytest.raises(ValueError, match="user"):
        _ = podman.exec_spec(ExecRequest(name="agent-claude", command=("true",), env={}, workdir="/work", user="sa s"))
    with pytest.raises(ValueError, match="env entry"):
        _ = podman.exec_spec(
            ExecRequest(name="agent-claude", command=("true",), env={"A": "bad\nvalue"}, workdir="/work", user="sas")
        )
    with pytest.raises(ValueError, match="absolute"):
        _ = Podman(Path("podman"))
    with pytest.raises(ValueError, match="positive"):
        _ = Podman(_PODMAN, timeout_s=0)
    with pytest.raises(ContainerError, match="binary missing"):
        _ = Podman(tmp_path / "missing-podman").inspect("agent-claude")


def test_containers_inspect_shapes_and_timeouts(tmp_path: Path) -> None:
    slash = _script(tmp_path, '#!/bin/sh\nprintf \'[{"Id":"i","Name":"/agent-claude","State":{"Running":true}}]\\n\'\n')
    seen = Podman(slash).inspect("agent-claude")
    assert seen.name == "agent-claude"
    assert seen.ident == "i"
    assert seen.running is True
    empty = _script(tmp_path, "#!/bin/sh\nprintf '[]\\n'\n")
    with pytest.raises(ContainerError, match="not a container"):
        _ = Podman(empty).inspect("agent-claude")
    junk = _script(tmp_path, "#!/bin/sh\nprintf 'nope\\n'\n")
    with pytest.raises(ContainerError, match="not a container"):
        _ = Podman(junk).inspect("agent-claude")
    sleeper = _script(tmp_path, "#!/bin/sh\nsleep 5\n")
    with pytest.raises(ContainerError, match="timed out"):
        _ = Podman(sleeper, timeout_s=0.05).inspect("agent-claude")
    missing_ini = tmp_path / "no-ini"
    with pytest.raises(DistroboxError, match="ini is missing"):
        _ = DistroboxEnvCache(missing_ini, _DISTROBOX).path_for("agent-claude")
    with pytest.raises(ValueError, match="absolute"):
        _ = DistroboxEnvCache(tmp_path / "ini", Path("distrobox"))
    with pytest.raises(ValueError, match="positive"):
        _ = DistroboxEnvCache(tmp_path / "ini", _DISTROBOX, timeout_s=0)
    _ = (tmp_path / "ini").write_text("x\n", encoding="utf-8")
    with pytest.raises(DistroboxError, match="binary missing"):
        _ = DistroboxEnvCache(tmp_path / "ini", tmp_path / "missing-distrobox").path_for("agent-claude")


def test_containers_distrobox_failure_and_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _script(tmp_path, "#!/bin/sh\necho no >&2\nexit 9\n")
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_text("ini\n", encoding="utf-8")
    with pytest.raises(DistroboxError, match="dry-run failed") as failed:
        _ = DistroboxEnvCache(ini, failing).path_for("agent-claude")
    assert failed.value.code == 9
    sleeper = _script(tmp_path, "#!/bin/sh\nsleep 5\n")
    with pytest.raises(DistroboxError, match="timed out"):
        _ = DistroboxEnvCache(ini, sleeper, timeout_s=0.05).path_for("agent-codex")
    blank = podman_blank_orphan(tmp_path)
    monkeypatch.setenv("PODMAN_FAKE_ORPHANS", "\n")
    assert Podman(blank).orphans("agent-claude", "tab-1") == ()


def test_containers_orphan_scan_runs_on_the_real_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan script is sent as `/bin/sh -c`; here the real shell runs it against a real process."""
    recorded = tmp_path / "scan.sh"
    recorder = _script(
        tmp_path, '#!/bin/sh\nfor arg in "$@"; do last=$arg; done\nprintf \'%s\' "$last" >"$SCAN_RECORD"\n'
    )
    monkeypatch.setenv("SCAN_RECORD", str(recorded))
    assert Podman(recorder).orphans("agentbox", "tab-real") == ()
    script = recorded.read_text(encoding="utf-8")
    assert "ACONSOLE_ORPHAN_TAB" in script
    token = secrets.token_hex(8)
    sleeper = subprocess.Popen([str(_SLEEP), "5"], env={"ACONSOLE_TAB": token, "PATH": _path()})
    try:
        _wait_for_environ(sleeper.pid, token)
        found = _scan(script, token)
        assert found.returncode == 0
        assert found.stdout.splitlines() == [str(sleeper.pid)]
        absent = _scan(script, secrets.token_hex(8))
        assert absent.returncode == 0
        assert absent.stdout == ""
    finally:
        sleeper.kill()
        _ = sleeper.wait()


def _scan(script: str, token: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SHELL), "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={"ACONSOLE_ORPHAN_TAB": token, "PATH": _path()},
    )


def _wait_for_environ(pid: int, token: str) -> None:
    """Before exec a child shows its parent's environment, so wait for the marker to appear."""
    needle = f"ACONSOLE_TAB={token}".encode()
    deadline = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        if needle in Path(f"/proc/{pid}/environ").read_bytes():
            return
        time.sleep(_POLL_S)
    message = "child never exec'd with its environment"
    raise AssertionError(message)


def _path() -> str:
    return os.environ.get("PATH", "/usr/bin:/bin")


def _script(directory: Path, body: str) -> Path:
    path = directory / f"bin-{len(list(directory.iterdir()))}"
    _ = path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def podman_blank_orphan(directory: Path) -> Path:
    """A podman that prints whatever PODMAN_FAKE_ORPHANS contains, including a blank line."""
    return _script(
        directory,
        "#!/bin/sh\nprintf '%s\\n' \"${PODMAN_FAKE_ORPHANS-}\"\n",
    )
