"""A read-only image is required; a container name alone never seals tools."""

import json
lazy from pathlib import Path

import pytest

from aconsole.core.application.ports.gate_runner import GateRunnerUnavailableError
from aconsole.core.infrastructure.containers.podman import ContainerError, Podman
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner


def _podman(tmp_path: Path, *, readonly: bool = True, privileged: bool = False,
            image: str = "a" * 64, source: str = "/workspace", extra_mount: bool = False,
            worktree: str = "/workspace", writable_checkout: bool = False) -> Podman:
    mounts: list[dict[str, object]] = [{"Type": "bind", "Source": source,
                                      "Destination": worktree, "RW": writable_checkout}]
    row: dict[str, object] = {"Id": "container", "Name": "gate", "Image": image, "State": {"Running": True},
           "HostConfig": {"ReadonlyRootfs": readonly, "Privileged": privileged},
           "Mounts": mounts}
    if extra_mount:
        mounts.append({"Type": "bind", "Source": "/host/tools", "Destination": "/opt/tools", "RW": False})
    binary = tmp_path / "podman"
    (tmp_path / "inspect.json").write_text(json.dumps([row]))
    binary.write_text(f"#!/usr/bin/python3\nprint({json.dumps([row])!r})\n")
    binary.chmod(0o700)
    return Podman(binary)


def test_image_identity_is_stable_and_image_change_invalidates_it(tmp_path: Path) -> None:
    podman = _podman(tmp_path)
    before = podman.gate_environment("gate", "/workspace")
    assert podman.gate_environment("gate", "/workspace") == before
    _podman(tmp_path, image="b" * 64)
    assert podman.gate_environment("gate", "/workspace") != before
    with pytest.raises(ContainerError):
        podman.gate_environment("gate", "/workspace", expected_image="a" * 64)


@pytest.mark.parametrize("case",
                         ["writable", "privileged", "tag", "external-tools", "wrong-checkout", "writable-checkout"])
def test_mutable_or_unpinned_environment_is_refused(tmp_path: Path, case: str) -> None:
    podman = _podman(tmp_path, readonly=case != "writable", privileged=case == "privileged",
                     image="latest" if case == "tag" else "a" * 64,
                     extra_mount=case == "external-tools",
                     source="/other" if case == "wrong-checkout" else "/workspace",
                     writable_checkout=case == "writable-checkout")
    with pytest.raises(ContainerError):
        podman.gate_environment("gate", "/workspace")


@pytest.mark.parametrize("case", ["directory", "escape", "image-link"])
def test_virtualenv_must_come_from_the_image(tmp_path: Path, case: str) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    venv = checkout / ".venv-core"
    if case == "directory":
        venv.mkdir()
    else:
        venv.symlink_to("/opt/aconsole/../../tmp/tools" if case == "escape" else "/opt/aconsole/core")
    podman = _podman(tmp_path, source=str(checkout), worktree=str(checkout))
    if case == "image-link":
        assert podman.gate_environment("gate", str(checkout))
    else:
        with pytest.raises(ContainerError):
            podman.gate_environment("gate", str(checkout))


@pytest.mark.parametrize("readonly", [True, False])
def test_tier3_exec_uses_the_validated_container_id(tmp_path: Path, *, readonly: bool) -> None:
    podman = _podman(tmp_path, readonly=readonly)
    calls = tmp_path / "calls.jsonl"
    binary = tmp_path / "podman"
    binary.write_text(
        "#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n"
        f"with Path({str(calls)!r}).open('a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"print(Path({str(tmp_path / 'inspect.json')!r}).read_text() if sys.argv[1]=='inspect' else 'ok')\n"
    )
    runner = ContainerGateRunner(podman=podman, container="gate", user="sas", worktree="/workspace",
                                 profile=(("/usr/bin/check",),), state_home=tmp_path)
    if readonly:
        assert runner.run(("/usr/bin/check",), tier=3).returncode == 0
    else:
        with pytest.raises(GateRunnerUnavailableError):
            runner.run(("/usr/bin/check",), tier=3)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    if readonly:
        assert [argv[-1] for argv in recorded if argv[0] == "inspect"] == ["gate", "container", "container"]
        executed = next(argv for argv in recorded if argv[0] == "exec")
        assert "container" in executed
        assert "gate" not in executed
    else:
        assert all(argv[0] == "inspect" for argv in recorded)
