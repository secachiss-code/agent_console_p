"""A9.3 setup checks, preview/apply confirmation, production IPC, and installer tests.

The installer fixture supplies a recorder; never substitute real uv for it.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
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
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from tests.core.conftest import wait_for_socket

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
    "notifications",
)
_ROOT = Path(__file__).resolve().parents[3]
_INSTALLER = _ROOT / "install.sh"


class _Probe(SetupProbe):
    def __init__(self, checks: tuple[CheckResult, ...]) -> None:
        self.checks = checks
        self.calls = 0

    def run_checks(self) -> tuple[CheckResult, ...]:
        self.calls += 1
        return self.checks


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


@pytest.mark.parametrize("token", ("", "wrong", "échec", "\ud800"), ids=("missing", "wrong", "unicode", "surrogate"))
def test_invalid_tokens_fail_before_provider_or_filesystem_effects(tmp_path: Path, token: str) -> None:
    service, _probe, fixes = _service(tmp_path)
    preview = service.preview_fix("create_app_config", at=_AT)
    before = _filesystem_snapshot(tmp_path)

    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        service.apply_fix("create_app_config", token, at=_AT + timedelta(seconds=1))

    assert fixes.apply_calls == []
    assert _filesystem_snapshot(tmp_path) == before
    assert preview.confirm_token == "setup-token"


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
        tmp_path / "core.sock",
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


def _fake_tools(home: Path) -> tuple[Path, Path]:
    fake_bin = home / ".test-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    executable = fake_bin / "uv-recorder"
    source = "\n".join(
        (
            "#!/bin/sh",
            "set -eu",
            'printf \'%s|%s|%s\\n\' "$PWD" "${UV_PROJECT_ENVIRONMENT-}" "$*" >> "$ACONSOLE_TEST_UV_LOG"',
            'target="${UV_PROJECT_ENVIRONMENT-}"',
            'if [ -n "$target" ]; then',
            '  case "$target" in /*) ;; *) target="$PWD/$target" ;; esac',
            '  case "$target" in "$HOME"/*) mkdir -p "$target" ;; *)',
            '    [ "${ACONSOLE_TEST_ALLOW_OUTSIDE-0}" = 1 ] || exit 91',
            '    mkdir -p "$target" ;;',
            '  esac',
            'fi',
            "",
        )
    )
    _ = executable.write_text(source, encoding="utf-8")
    executable.chmod(0o755)
    fake_sudo = fake_bin / "sudo"
    sudo_source = "\n".join(
        (
            "#!/bin/sh",
            'printf \'called\\n\' >> "$ACONSOLE_TEST_SUDO_LOG"',
            "exit 90",
            "",
        )
    )
    _ = fake_sudo.write_text(sudo_source, encoding="utf-8")
    fake_sudo.chmod(0o755)
    return executable, fake_sudo
