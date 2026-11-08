"""A9.3 setup checks, preview/apply confirmation, production IPC, and installer tests.

The installer fixture supplies a recorder; never substitute real uv for it.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import msgspec
import pytest

from aconsole.contracts.app import (
    ApplyFixParams,
    CheckResult,
    ChecksResult,
    EmptyParams,
    FixIdParams,
    FixPreview,
    SettingsUpdateParams,
    SnapshotResult,
)
from aconsole.contracts.common import ClientRole
from aconsole.contracts.ipc import (
    Envelope,
    InitializeParams,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.contracts.setup_detail import SetupDetail
from aconsole.core.application.ports.setup import (
    SetupFixExecutionError,
    SetupFixPlan,
    SetupFixProvider,
    SetupProbe,
    SetupTokenSource,
)
from aconsole.core.application.services.setup import SetupService
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.containers.login import LOGIN_SHELL
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.setup_checks import (
    HostSetupFixes,
    HostSetupProbe,
    SetupEnvironment,
    SystemSetupEnvironment,
)
from tests.core.conftest import wait_for_socket
from tests.socket_paths import short_socket_path

_AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
_ENCODER = msgspec.json.Encoder()
_CHECK_IDS = (
    "host_dependencies",
    "agent_containers",
    "agent_login",
    "agent_versions",
    "storage",
    "worktree_storage_mounts",
    "devtools_graph_tools",
    "container_isolation",
    "notifications",
)
_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "install.sh"


_USER = "someone"


def _without_login(argv: tuple[str, ...]) -> tuple[str, ...]:
    """A check's call as the plain command it stands for."""
    prefix = ("podman", "exec", "--user", _USER)
    if argv[: len(prefix)] != prefix:
        return argv
    name = argv[len(prefix)]
    return ("podman", "exec", name, *argv[len(prefix) + 1 + len(LOGIN_SHELL) :])


class _Probe(SetupProbe):
    def __init__(self, checks: tuple[CheckResult, ...]) -> None:
        self.checks = checks
        self.calls = 0

    def run_checks(self) -> tuple[CheckResult, ...]:
        self.calls += 1
        return self.checks


class _FakeSetupEnvironment(SetupEnvironment):
    def __init__(self, home: Path) -> None:
        self._home = home
        self.available = {"podman", "distrobox", "notify-send"}
        self.raw_calls: list[tuple[str, ...]] = []
        self.directories = {home / "Obsidian" / "AConsole"}
        self.variables = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/fake-bus"}
        self.containers = ("agent-claude",)
        self.failed_argv: set[tuple[str, ...]] = set()
        self.calls: list[tuple[str, ...]] = []
        self.list_succeeds = True

    @property
    def home(self) -> Path:
        return self._home

    def which(self, name: str) -> str | None:
        return f"/fake/bin/{name}" if name in self.available else None

    @property
    def user(self) -> str:
        return _USER

    def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        self.raw_calls.append(argv)
        argv = _without_login(argv)
        self.calls.append(argv)
        if argv == ("podman", "ps", "-a", "--format", "{{.Names}}"):
            return self.list_succeeds, "\n".join(self.containers)
        if argv in self.failed_argv:
            return False, "simulated probe failure"
        if argv[:2] == ("podman", "inspect"):
            return True, "[]"
        if argv[-1:] == ("--version",):
            return True, f"{argv[-2]} 1.2.3"
        if argv[-3:] == ("claude", "auth", "status"):
            return True, '{"authMethod":"claude.ai"}'
        return True, "simulated probe evidence"

    def is_dir(self, path: Path) -> bool:
        return path in self.directories

    def environ(self, name: str) -> str | None:
        return self.variables.get(name)

    def fail_category(self, category: str) -> None:
        worktrees = self.home / ".local" / "share" / "aconsole" / "worktrees"
        vault = self.home / "Obsidian" / "AConsole"
        if category == "host_dependencies":
            self.available.discard("distrobox")
        elif category == "agent_containers":
            self.containers = ()
        elif category == "agent_login":
            self.failed_argv.add(("podman", "exec", "agent-claude", "claude", "auth", "status"))
        elif category == "agent_versions":
            self.failed_argv.add(("podman", "exec", "agent-claude", "claude", "--version"))
        elif category == "storage":
            self.directories.discard(vault)
        elif category == "worktree_storage_mounts":
            self.failed_argv.add(("podman", "exec", "agent-claude", "test", "-d", str(worktrees)))
        elif category == "devtools_graph_tools":
            self.failed_argv.add(("podman", "exec", "devtools", "dotnet", "--version"))
        elif category == "container_isolation":
            self.failed_argv.add(("podman", "inspect", "--format", "{{json .Mounts}}", "agent-claude"))
        elif category == "notifications":
            self.variables.pop("DBUS_SESSION_BUS_ADDRESS", None)
        else:
            pytest.fail(f"unknown setup check category: {category}")


class _TokenSource(SetupTokenSource):
    def __init__(self, *tokens: str) -> None:
        self.tokens = list(tokens)

    def new_token(self) -> str:
        if not self.tokens:
            pytest.fail("unexpected request for another setup token")
        return self.tokens.pop(0)


class _Fixes(SetupFixProvider):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.plan = _plan()
        self.plans: dict[str, SetupFixPlan] = {self.plan.fix_id: self.plan, "fix_b": _plan("fix_b")}
        self.apply_calls: list[SetupFixPlan] = []
        self.failure: SetupFixExecutionError | None = None
        self.source_drift_during_apply = False

    def preview(self, fix_id: str) -> SetupFixPlan | None:
        return self.plans.get(fix_id)

    def apply(self, plan: SetupFixPlan) -> CheckResult:
        self.apply_calls.append(plan)
        if self.failure is not None:
            raise self.failure
        if self.source_drift_during_apply:
            raise SetupFixExecutionError(step="revalidate", stderr_tail="source changed")
        target = self.home / ".local" / "share" / "aconsole" / "setup.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        _ = temporary.write_text(plan.target_fingerprint, encoding="utf-8")
        temporary.replace(target)
        return _check("fix_applied", ok=True, severity="info", detail="applied")


def _plan(fix_id: str = "create_app_config", *, source: str = "source-v1", target: str = "target-v1") -> SetupFixPlan:
    return SetupFixPlan(
        fix_id=fix_id,
        summary="Create the app-owned configuration file",
        diff="+ config = true\n",
        commands=("Create configuration under HOME",),
        reversible=True,
        source_fingerprint=source,
        target_fingerprint=target,
    )


def _check(
    check_id: str,
    *,
    ok: bool,
    severity: Literal["error", "warning", "info"],
    detail: str,
    fix_id: str | None = None,
    requires_confirmation: bool = False,
) -> CheckResult:
    return CheckResult(
        id=check_id,
        title=check_id.replace("_", " ").title(),
        ok=ok,
        severity=severity,
        detail=detail,
        fix_id=fix_id,
        requires_confirmation=requires_confirmation,
    )


def _checks(*, fail: frozenset[str] = frozenset()) -> tuple[CheckResult, ...]:
    return tuple(
        _check(
            check_id,
            ok=check_id not in fail,
            severity="error" if check_id in fail else "info",
            detail="probe evidence: missing" if check_id in fail else "probe evidence: present",
            fix_id="create_app_config" if check_id == "storage" and check_id in fail else None,
            requires_confirmation=check_id == "storage" and check_id in fail,
        )
        for check_id in _CHECK_IDS
    )


def test_host_setup_probe_reports_all_categories_from_fake_environment(tmp_path: Path) -> None:
    environment = _FakeSetupEnvironment(tmp_path)

    checks = HostSetupProbe(environment).run_checks()
    by_id = {check.id: check for check in checks}

    assert tuple(check.id for check in checks) == _CHECK_IDS
    assert all(check.ok and check.severity == "info" for check in checks)
    assert by_id["host_dependencies"].detail == "podman, distrobox"
    assert by_id["agent_login"].ok
    assert by_id["storage"].detail == str(tmp_path / "Obsidian" / "AConsole")
    assert ("podman", "exec", "agent-claude", "claude", "auth", "status") in environment.calls
    # A check sees what a tab will see: the same user, the same way of finding the agent.
    assert ("podman", "exec", "--user", _USER, "agent-claude", *LOGIN_SHELL, "claude", "--version") in (
        environment.raw_calls
    )
    assert (
        "podman",
        "exec",
        "agent-claude",
        "test",
        "-d",
        str(tmp_path / ".local" / "share" / "aconsole" / "worktrees"),
    ) in environment.calls
    assert all(isinstance(argv, tuple) and argv for argv in environment.calls)


@pytest.mark.parametrize("category", _CHECK_IDS)
def test_host_setup_probe_marks_missing_evidence_for_each_category(tmp_path: Path, category: str) -> None:
    environment = _FakeSetupEnvironment(tmp_path)
    environment.fail_category(category)

    checks = {check.id: check for check in HostSetupProbe(environment).run_checks()}

    assert checks[category].ok is False
    assert checks[category].severity == "warning"
    assert checks[category].detail
    assert all(isinstance(argv, tuple) and argv for argv in environment.calls)


def test_an_agent_that_cannot_report_its_login_is_not_a_failed_check(tmp_path: Path) -> None:
    environment = _FakeSetupEnvironment(tmp_path)
    environment.containers = ("agent-claude", "agent-codex")

    checks = {check.id: check for check in HostSetupProbe(environment).run_checks()}

    assert checks["agent_versions"].ok is True
    assert checks["agent_login"].ok is True
    assert checks["agent_login"].command is None


def test_no_containers_fails_one_check_and_says_what_to_run(tmp_path: Path) -> None:
    environment = _FakeSetupEnvironment(tmp_path)
    environment.fail_category("agent_containers")

    checks = {check.id: check for check in HostSetupProbe(environment).run_checks()}

    assert [check_id for check_id, check in checks.items() if not check.ok] == ["agent_containers"]
    assert checks["agent_containers"].command == "podman create --help"
    assert checks["agent_containers"].detail == "setup.detail.no_containers"


@pytest.mark.parametrize("category", _CHECK_IDS)
def test_every_failed_check_names_a_command_and_a_passed_one_none(tmp_path: Path, category: str) -> None:
    environment = _FakeSetupEnvironment(tmp_path)
    assert all(check.command is None for check in HostSetupProbe(environment).run_checks())

    environment.fail_category(category)

    check = next(item for item in HostSetupProbe(environment).run_checks() if item.id == category)
    assert check.command


def test_a_failed_check_names_what_is_missing(tmp_path: Path) -> None:
    environment = _FakeSetupEnvironment(tmp_path)
    environment.fail_category("host_dependencies")

    check = next(item for item in HostSetupProbe(environment).run_checks() if item.id == "host_dependencies")

    assert check.detail == "distrobox"
    assert check.command == "sudo apt install distrobox"


def test_a_missing_notes_folder_can_be_created_and_only_under_home(tmp_path: Path) -> None:
    fixes = HostSetupFixes(tmp_path)
    plan = fixes.preview("create_storage_directory")
    assert plan is not None

    result = fixes.apply(plan)

    assert result.ok
    assert (tmp_path / "Obsidian" / "AConsole").is_dir()
    assert fixes.preview("no_such_fix") is None
    assert fixes.preview("create_storage_directory") != plan


def _service(
    home: Path,
    *,
    checks: tuple[CheckResult, ...] | None = None,
    tokens: tuple[str, ...] = ("setup-token",),
) -> tuple[SetupService, _Probe, _Fixes]:
    probe = _Probe(_checks() if checks is None else checks)
    fixes = _Fixes(home)
    service = SetupService(probe=probe, fixes=fixes, tokens=_TokenSource(*tokens))
    return service, probe, fixes


def test_setup_checks_reports_all_eight_categories_from_explicit_probe_evidence(tmp_path: Path) -> None:
    checks = _checks(fail=frozenset({"host_dependencies", "storage"}))
    service, probe, fixes = _service(tmp_path, checks=checks)

    result = service.checks()

    assert isinstance(result, ChecksResult)
    assert tuple(item.id for item in result.checks) == _CHECK_IDS
    assert result.checks[0].ok is False
    assert result.checks[0].severity == "error"
    assert result.checks[4].fix_id == "create_app_config"
    assert result.checks[4].requires_confirmation is True
    assert probe.calls == 1
    assert fixes.apply_calls == []
    assert not (tmp_path / ".local").exists()


def test_preview_apply_is_preview_bound_one_shot_and_writes_only_under_home(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path)

    preview = service.preview_fix("create_app_config", at=_AT)
    assert isinstance(preview, FixPreview)
    assert preview.commands == ("Create configuration under HOME",)
    assert preview.reversible is True
    assert preview.expires_at == _AT + timedelta(minutes=5)

    result = service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=1))

    assert result.id == "fix_applied"
    assert len(fixes.apply_calls) == 1
    target = tmp_path / ".local" / "share" / "aconsole" / "setup.json"
    assert target.read_text(encoding="utf-8") == "target-v1"
    assert target.is_relative_to(tmp_path)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=2))
    assert len(fixes.apply_calls) == 1


@pytest.mark.parametrize("token", ["", "wrong", "échec", "\ud800"], ids=("missing", "wrong", "unicode", "surrogate"))
def test_invalid_tokens_fail_before_provider_or_filesystem_effects(tmp_path: Path, token: str) -> None:
    service, _probe, fixes = _service(tmp_path)
    preview = service.preview_fix("create_app_config", at=_AT)
    before = _filesystem_snapshot(tmp_path)

    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", token, at=_AT + timedelta(seconds=1))

    assert fixes.apply_calls == []
    assert _filesystem_snapshot(tmp_path) == before
    assert preview.confirm_token


def test_expired_mismatched_fix_and_changed_fingerprints_have_no_effect(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path, tokens=("expired", "mismatch", "changed"))
    expired = service.preview_fix("create_app_config", at=_AT)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", expired.confirm_token, at=expired.expires_at)

    mismatch = service.preview_fix("create_app_config", at=_AT)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("fix_b", mismatch.confirm_token, at=_AT + timedelta(seconds=1))

    changed = service.preview_fix("create_app_config", at=_AT)
    fixes.plans["create_app_config"] = _plan(source="source-v2", target="target-v2")
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", changed.confirm_token, at=_AT + timedelta(seconds=1))

    assert fixes.apply_calls == []
    assert _filesystem_snapshot(tmp_path) == ()


def test_expired_previews_are_pruned_and_newest_token_remains_usable(tmp_path: Path) -> None:
    count = 64
    tokens = tuple(f"setup-token-{index}" for index in range(count))
    service, _probe, fixes = _service(tmp_path, tokens=tokens)
    previews = tuple(
        service.preview_fix("create_app_config", at=_AT + index * timedelta(minutes=6))
        for index in range(count)
    )
    latest_at = _AT + (count - 1) * timedelta(minutes=6)
    before = _filesystem_snapshot(tmp_path)

    assert len(service._pending) <= 1
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", previews[0].confirm_token, at=latest_at)

    assert fixes.apply_calls == []
    assert _filesystem_snapshot(tmp_path) == before

    result = service.apply_fix(
        "create_app_config",
        previews[-1].confirm_token,
        at=latest_at + timedelta(seconds=1),
    )

    assert result.id == "fix_applied"
    assert len(fixes.apply_calls) == 1


def test_unknown_fix_is_rejected_without_runner_call(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path)

    with pytest.raises(DomainError, match="SETUP_FIX_UNKNOWN"):
        service.preview_fix("not-supported", at=_AT)
    with pytest.raises(DomainError, match="SETUP_FIX_UNKNOWN"):
        service.apply_fix("not-supported", "anything", at=_AT)

    assert fixes.apply_calls == []


def test_provider_failure_burns_token_and_never_retries_side_effect(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path)
    fixes.failure = SetupFixExecutionError(step="write", stderr_tail="simulated failure")
    preview = service.preview_fix("create_app_config", at=_AT)

    with pytest.raises(DomainError, match="SETUP_FIX_FAILED"):
        service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=1))
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=2))

    assert len(fixes.apply_calls) == 1
    assert _filesystem_snapshot(tmp_path) == ()


def test_provider_source_recheck_prevents_write_and_burns_token(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path)
    preview = service.preview_fix("create_app_config", at=_AT)
    fixes.source_drift_during_apply = True

    with pytest.raises(DomainError, match="SETUP_FIX_FAILED"):
        service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=1))
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=2))

    assert len(fixes.apply_calls) == 1
    assert _filesystem_snapshot(tmp_path) == ()


def test_concurrent_replay_consumes_token_once_under_free_threaded_service(tmp_path: Path) -> None:
    service, _probe, fixes = _service(tmp_path)
    preview = service.preview_fix("create_app_config", at=_AT)
    barrier = threading.Barrier(3)

    def apply_at_barrier() -> str:
        barrier.wait(timeout=5)
        try:
            service.apply_fix("create_app_config", preview.confirm_token, at=_AT + timedelta(seconds=1))
        except DomainError as exc:
            return exc.name
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply_at_barrier)
        second = pool.submit(apply_at_barrier)
        barrier.wait(timeout=5)
        outcomes = (first.result(timeout=5), second.result(timeout=5))

    assert sorted(outcomes) == ["CONFIRM_TOKEN_INVALID", "applied"]
    assert len(fixes.apply_calls) == 1
    assert (tmp_path / ".local" / "share" / "aconsole" / "setup.json").is_file()


def test_setup_methods_use_production_ipc_and_keep_completion_separate(tmp_path: Path) -> None:
    asyncio.run(_setup_methods_use_production_ipc_and_keep_completion_separate(tmp_path))


async def _setup_methods_use_production_ipc_and_keep_completion_separate(tmp_path: Path) -> None:
    service, probe, fixes = _service(tmp_path, checks=_checks(fail=frozenset({"storage"})), tokens=("ipc-token",))
    daemon = Daemon(
        short_socket_path(tmp_path / "core.sock"),
        idle_s=30,
        database=tmp_path / "aconsole.db",
        setup_service=service,
    )
    serving = asyncio.create_task(daemon.serve())
    await wait_for_socket(daemon.socket)
    try:
        reader, writer = await asyncio.open_unix_connection(daemon.socket)
        try:
            initialized = await _rpc(
                reader,
                writer,
                1,
                "initialize",
                InitializeParams(
                    protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
                    role=ClientRole.UI,
                    client_name="setup-test",
                    client_version="0.0.0",
                    pid=1,
                    capabilities=frozenset({"events.v1"}),
                ),
            )
            assert isinstance(initialized, ResponseOk)
            settings = await _rpc(reader, writer, 2, "settings/get", EmptyParams())
            assert isinstance(settings, ResponseOk)
            loaded = msgspec.json.decode(settings.result, type=Settings)
            updated = await _rpc(
                reader,
                writer,
                3,
                "settings/update",
                SettingsUpdateParams(patch=SettingsPatch(setup_completed=True), rev=loaded.rev),
            )
            assert isinstance(updated, ResponseOk)

            checks = await _rpc(reader, writer, 4, "setup/checks", EmptyParams())
            assert isinstance(checks, ResponseOk)
            report = msgspec.json.decode(checks.result, type=ChecksResult)
            assert tuple(item.id for item in report.checks) == _CHECK_IDS
            assert next(item for item in report.checks if item.id == "storage").ok is False

            preview_frame = await _rpc(reader, writer, 5, "setup/previewFix", FixIdParams(fix_id="create_app_config"))
            assert isinstance(preview_frame, ResponseOk)
            preview = msgspec.json.decode(preview_frame.result, type=FixPreview)
            applied = await _rpc(
                reader,
                writer,
                6,
                "setup/applyFix",
                ApplyFixParams(fix_id="create_app_config", confirm_token=preview.confirm_token),
            )
            assert isinstance(applied, ResponseOk)
            assert msgspec.json.decode(applied.result, type=CheckResult).id == "fix_applied"

            repeated = await _rpc(
                reader,
                writer,
                7,
                "setup/applyFix",
                ApplyFixParams(fix_id="create_app_config", confirm_token=preview.confirm_token),
            )
            assert isinstance(repeated, ResponseErr)
            assert repeated.error.message == "CONFIRM_TOKEN_INVALID"

            snapshot_frame = await _rpc(reader, writer, 8, "app/snapshot", EmptyParams())
            assert isinstance(snapshot_frame, ResponseOk)
            snapshot = msgspec.json.decode(snapshot_frame.result, type=SnapshotResult).snapshot
            assert snapshot.setup.required is False
            assert probe.calls == 1
            assert len(fixes.apply_calls) == 1
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(serving, timeout=15)


async def _rpc(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: msgspec.Struct,
) -> Envelope:
    request = Request(id=request_id, method=method, params=msgspec.Raw(_ENCODER.encode(params)))
    writer.write(encode_line(_ENCODER, request))
    await writer.drain()
    return decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), timeout=5))


def _filesystem_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    if not root.exists():
        return ()
    return tuple(
        sorted(
            (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_installer_uses_fake_uv_and_only_writes_to_fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    checkout = home / "Projects" / "repo"
    checkout.mkdir(parents=True)
    script = checkout / "install.sh"
    _ = shutil.copyfile(_INSTALLER, script)
    fake_uv, _fake_sudo = _fake_tools(home)
    log = home / ".test" / "uv.log"
    log.parent.mkdir()
    sudo_log = home / ".test" / "sudo.log"
    _ = log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACONSOLE_UV", str(fake_uv))
    monkeypatch.setenv("ACONSOLE_TEST_UV_LOG", str(log))
    monkeypatch.setenv("ACONSOLE_TEST_SUDO_LOG", str(sudo_log))
    monkeypatch.setenv("ACONSOLE_TEST_ALLOW_OUTSIDE", "0")
    monkeypatch.setenv("PATH", f"{fake_uv.parent}:/usr/bin:/bin")

    completed = subprocess.run(
        ("/bin/sh", str(script)),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    sync_calls = tuple(line for line in calls if "|sync " in line)
    assert sync_calls
    assert all("--locked" in line for line in sync_calls)
    assert all("pip install" not in line for line in calls)
    assert not sudo_log.exists()
    assert (checkout / ".venv-core").is_dir()
    assert (checkout / ".venv-ui").is_dir()
    assert all(path.is_relative_to(home) for path in home.rglob("*"))


def test_installer_dry_run_within_home_has_no_writes_or_uv_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    checkout = home / "Projects" / "repo"
    checkout.mkdir(parents=True)
    script = checkout / "install.sh"
    _ = shutil.copyfile(_INSTALLER, script)
    fake_uv, _fake_sudo = _fake_tools(home)
    log = home / "uv.log"
    _ = log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACONSOLE_UV", str(fake_uv))
    monkeypatch.setenv("ACONSOLE_TEST_UV_LOG", str(log))
    monkeypatch.setenv("ACONSOLE_TEST_SUDO_LOG", str(home / "sudo.log"))
    monkeypatch.setenv("ACONSOLE_TEST_ALLOW_OUTSIDE", "0")
    monkeypatch.setenv("PATH", f"{fake_uv.parent}:/usr/bin:/bin")

    before = _filesystem_snapshot(tmp_path)
    completed = subprocess.run(
        ("/bin/sh", str(script), "--dry-run"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert log.read_text(encoding="utf-8") == ""
    assert _filesystem_snapshot(tmp_path) == before


def test_installer_outside_home_refuses_and_dry_run_has_no_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout-outside-home"
    checkout.mkdir()
    script = checkout / "install.sh"
    _ = shutil.copyfile(_INSTALLER, script)
    fake_uv, _fake_sudo = _fake_tools(home)
    log = home / "uv.log"
    _ = log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACONSOLE_UV", str(fake_uv))
    monkeypatch.setenv("ACONSOLE_TEST_UV_LOG", str(log))
    monkeypatch.setenv("ACONSOLE_TEST_SUDO_LOG", str(home / "sudo.log"))
    monkeypatch.setenv("ACONSOLE_TEST_ALLOW_OUTSIDE", "0")
    monkeypatch.setenv("PATH", f"{fake_uv.parent}:/usr/bin:/bin")

    before = _filesystem_snapshot(tmp_path)
    dry_run = subprocess.run(
        ("/bin/sh", str(script), "--dry-run"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert dry_run.returncode != 0
    assert log.read_text(encoding="utf-8") == ""
    assert _filesystem_snapshot(tmp_path) == before

    refused = subprocess.run(
        ("/bin/sh", str(script)),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert refused.returncode != 0
    assert log.read_text(encoding="utf-8") == ""
    assert not (checkout / ".venv-core").exists()
    assert not (checkout / ".venv-ui").exists()


def test_installer_explicit_outside_home_flag_uses_only_fake_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout-outside-home"
    checkout.mkdir()
    script = checkout / "install.sh"
    _ = shutil.copyfile(_INSTALLER, script)
    fake_uv, _fake_sudo = _fake_tools(home)
    log = home / "uv.log"
    _ = log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACONSOLE_UV", str(fake_uv))
    monkeypatch.setenv("ACONSOLE_TEST_UV_LOG", str(log))
    monkeypatch.setenv("ACONSOLE_TEST_SUDO_LOG", str(home / "sudo.log"))
    monkeypatch.setenv("ACONSOLE_TEST_ALLOW_OUTSIDE", "1")
    monkeypatch.setenv("PATH", f"{fake_uv.parent}:/usr/bin:/bin")

    completed = subprocess.run(
        ("/bin/sh", str(script), "--allow-outside-home"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert (checkout / ".venv-core").is_dir()
    assert (checkout / ".venv-ui").is_dir()
    assert not (home / "sudo.log").exists()
    assert log.read_text(encoding="utf-8")


def test_installer_stops_on_a_wrong_python_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--python 3.15` could pick the free-threaded build, and the window then fails much later."""
    home = tmp_path / "home"
    checkout = home / "Projects" / "repo"
    checkout.mkdir(parents=True)
    script = checkout / "install.sh"
    _ = shutil.copyfile(_INSTALLER, script)
    fake_uv, _fake_sudo = _fake_tools(home)
    log = home / ".test" / "uv.log"
    log.parent.mkdir()
    _ = log.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACONSOLE_UV", str(fake_uv))
    monkeypatch.setenv("ACONSOLE_TEST_UV_LOG", str(log))
    monkeypatch.setenv("ACONSOLE_TEST_WRONG_PYTHON", "1")
    monkeypatch.setenv("PATH", f"{fake_uv.parent}:/usr/bin:/bin")
    completed = subprocess.run(
        ("/bin/sh", str(script)), capture_output=True, text=True, check=False, timeout=30,
    )
    assert completed.returncode != 0
    assert "not a GIL build" in completed.stderr
    assert "--python 3.15+gil" in log.read_text(encoding="utf-8")


def _fake_tools(home: Path) -> tuple[Path, Path]:
    fake_bin = home / ".test-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    executable = fake_bin / "uv-recorder"
    source = textwrap.dedent(
        """\
        #!/bin/sh
        set -eu
        printf '%s|%s|%s\\n' "$PWD" "${UV_PROJECT_ENVIRONMENT-}" "$*" >> "$ACONSOLE_TEST_UV_LOG"
        target="${UV_PROJECT_ENVIRONMENT-}"
        if [ -n "$target" ]; then
          case "$target" in /*) ;; *) target="$PWD/$target" ;; esac
          case "$target" in "$HOME"/*) mkdir -p "$target" ;; *)
            [ "${ACONSOLE_TEST_ALLOW_OUTSIDE-0}" = 1 ] || exit 91
            mkdir -p "$target" ;;
          esac
          mkdir -p "$target/bin"
          printf '#!/bin/sh\nexit %s\n' "${ACONSOLE_TEST_WRONG_PYTHON-0}" > "$target/bin/python"
          chmod +x "$target/bin/python"
        fi
        """
    )
    _ = executable.write_text(source, encoding="utf-8")
    executable.chmod(0o755)
    fake_sudo = fake_bin / "sudo"
    sudo_source = textwrap.dedent(
        """\
        #!/bin/sh
        printf 'called\\n' >> "$ACONSOLE_TEST_SUDO_LOG"
        exit 90
        """
    )
    _ = fake_sudo.write_text(sudo_source, encoding="utf-8")
    fake_sudo.chmod(0o755)
    return executable, fake_sudo


@pytest.mark.parametrize("relative", [".", ".bashrc", ".config/systemd/user", ".local/share/aconsole/aconsole.db",
                                      ".local/share/aconsole/aconsole.db-wal", ".containers/hostrun", ".aws"])
@pytest.mark.parametrize("writable", [False, True])
def test_isolation_warns_on_sensitive_host_mounts(tmp_path: Path, relative: str, *, writable: bool) -> None:
    class Mounted(_FakeSetupEnvironment):
        def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
            if argv[:2] == ("podman", "inspect"):
                return True, msgspec.json.encode([{
                    "Source": str(tmp_path / relative), "Destination": "/exposed", "RW": writable,
                }]).decode()
            return super().run(argv)

    check = HostSetupProbe(Mounted(tmp_path)).isolation_check()
    assert not check.ok
    assert check.severity == "warning"
    assert "devtools" in check.detail
    assert "agent-claude" in check.detail
    assert "setup_detail.v1" in check.detail
    assert check.fix_id is None
    assert check.command


@pytest.mark.parametrize("raw", ["{", "null", "{}", '[{"Source":"relative","Destination":"/x","RW":true}]'])
def test_isolation_does_not_claim_safety_without_valid_metadata(tmp_path: Path, raw: str) -> None:
    class Unknown(_FakeSetupEnvironment):
        def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
            return (True, raw) if argv[:2] == ("podman", "inspect") else super().run(argv)

    check = HostSetupProbe(Unknown(tmp_path)).isolation_check()
    assert not check.ok
    detail = msgspec.json.decode(check.detail, type=SetupDetail)
    expected = "setup.isolation.mount_invalid" if raw.startswith("[") else "setup.isolation.mount_unknown"
    assert all(fact.code == expected for fact in detail.facts)


def test_isolation_allows_only_worktree_and_vault_binds(tmp_path: Path) -> None:
    class Isolated(_FakeSetupEnvironment):
        def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
            if argv[:2] == ("podman", "inspect"):
                return True, msgspec.json.encode([
                    {"Source": str(tmp_path / part), "Destination": str(tmp_path / part), "RW": True}
                    for part in (".local/share/aconsole/worktrees", "Obsidian/AConsole")
                ]).decode()
            return super().run(argv)

    check = HostSetupProbe(Isolated(tmp_path)).isolation_check()
    assert check.ok
    assert "setup.isolation.metadata_only" in check.detail


def test_isolation_uses_custom_xdg_database_path(tmp_path: Path) -> None:
    class Custom(_FakeSetupEnvironment):
        def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
            if argv[:2] == ("podman", "inspect"):
                return True, msgspec.json.encode([{
                    "Source": str(tmp_path / "custom/aconsole/aconsole.db"), "Destination": "/db", "RW": True,
                }]).decode()
            return super().run(argv)

    env = Custom(tmp_path)
    env.variables["XDG_DATA_HOME"] = str(tmp_path / "custom")
    assert not HostSetupProbe(env).isolation_check().ok


def test_isolation_probe_never_executes_a_container_profile(tmp_path: Path) -> None:
    env = _FakeSetupEnvironment(tmp_path)
    assert HostSetupProbe(env).isolation_check().ok
    assert all(call[:2] in {("podman", "inspect"), ("podman", "ps")} for call in env.raw_calls)


def test_isolation_detects_a_bind_source_symlink(tmp_path: Path) -> None:
    (tmp_path / ".config").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / ".config", target_is_directory=True)

    class Alias(_FakeSetupEnvironment):
        def run(self, argv: tuple[str, ...]) -> tuple[bool, str]:
            if argv[:2] == ("podman", "inspect"):
                return True, msgspec.json.encode([{
                    "Source": str(alias), "Destination": "/elsewhere", "RW": True,
                }]).decode()
            return super().run(argv)

    assert not HostSetupProbe(Alias(tmp_path)).isolation_check().ok


@pytest.mark.parametrize("command", ["inspect", "ps"])
def test_large_container_metadata_is_unknown_instead_of_truncated(
    command: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("podman", command), returncode=0,
                                           stdout="agent-safe\n" * 7000, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    ok, detail = SystemSetupEnvironment().run(("podman", command))
    assert not ok
    assert detail == "setup.detail.metadata_limit"



def test_missing_notification_bus_does_not_recommend_reinstalling_notify_send(tmp_path: Path) -> None:
    env = _FakeSetupEnvironment(tmp_path)
    env.variables.pop("DBUS_SESSION_BUS_ADDRESS")
    check = next(item for item in HostSetupProbe(env).run_checks() if item.id == "notifications")
    assert not check.ok
    assert check.command == "systemctl --user status dbus.service"
    assert check.detail == "setup.detail.bus_missing"


def test_missing_notify_send_names_the_install_command(tmp_path: Path) -> None:
    env = _FakeSetupEnvironment(tmp_path)
    env.available.discard("notify-send")
    check = next(item for item in HostSetupProbe(env).run_checks() if item.id == "notifications")
    assert not check.ok
    assert check.command == "sudo apt install libnotify-bin"
