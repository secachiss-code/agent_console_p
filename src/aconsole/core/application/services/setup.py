"""Setup checks and one-shot, preview-bound fix confirmations."""

import secrets
import threading
from datetime import datetime, timedelta

from aconsole.contracts.app import CheckResult, ChecksResult, FixPreview
from aconsole.contracts.errors import error_by_name
from aconsole.core.application.ports.setup import (
    SetupFixExecutionError,
    SetupFixPlan,
    SetupFixProvider,
    SetupProbe,
    SetupTokenSource,
)
from aconsole.core.domain.errors import DomainError

_TTL = timedelta(minutes=5)
_UNKNOWN = "SETUP_FIX_UNKNOWN"
_INVALID = "CONFIRM_TOKEN_INVALID"
_FAILED = "SETUP_FIX_FAILED"


class SetupService:
    _probe: SetupProbe
    _fixes: SetupFixProvider
    _tokens: SetupTokenSource
    _ttl: timedelta
    _lock: threading.Lock

    def __init__(self, *, probe: SetupProbe, fixes: SetupFixProvider, tokens: SetupTokenSource,
                 ttl: timedelta = _TTL) -> None:
        self._probe = probe
        self._fixes = fixes
        self._tokens = tokens
        self._ttl = ttl
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[SetupFixPlan, datetime]] = {}

    def _prune_expired(self, at: datetime) -> None:
        """Discard expired confirmations while the caller holds the lock."""
        for token in tuple(self._pending):
            if at >= self._pending[token][1]:
                del self._pending[token]

    def checks(self) -> ChecksResult:
        return ChecksResult(checks=self._probe.run_checks())

    def preview_fix(self, fix_id: str, *, at: datetime) -> FixPreview:
        plan = self._fixes.preview(fix_id)
        if plan is None:
            raise _error(_UNKNOWN)
        token = self._tokens.new_token()
        expires = at + self._ttl
        with self._lock:
            self._prune_expired(at)
            self._pending[token] = (plan, expires)
        return FixPreview(fix_id=fix_id, summary=plan.summary, diff=plan.diff, commands=plan.commands,
                          reversible=plan.reversible, confirm_token=token, expires_at=expires)

    def apply_fix(self, fix_id: str, confirm_token: str, *, at: datetime) -> CheckResult:
        try:
            supplied = confirm_token.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _error(_INVALID) from exc
        with self._lock:
            self._prune_expired(at)
            plan = self._fixes.preview(fix_id)
            if plan is None:
                raise _error(_UNKNOWN)
            key = next((key for key in self._pending if secrets.compare_digest(key.encode("utf-8"), supplied)), None)
            if key is None:
                raise _error(_INVALID)
            approved, expires = self._pending[key]
            if at >= expires or approved != plan:
                raise _error(_INVALID)
            del self._pending[key]
        try:
            return self._fixes.apply(plan)
        except SetupFixExecutionError as exc:
            raise _error(_FAILED) from exc


def _error(name: str) -> DomainError:
    spec = error_by_name(name)
    return DomainError(code=spec.code, name=spec.name, retryable=spec.retryable)
