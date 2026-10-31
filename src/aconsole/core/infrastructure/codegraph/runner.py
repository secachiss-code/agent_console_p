"""Run one extractor through `hostrun devtools <language> <path>` and parse its stdout.

A non-zero exit, a timeout, or stdout that is not version 1 becomes an `ErrorObject`.
This module does not install packages and does not open a network connection of its own.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.contracts.errors import (
    CodegraphExtractorFailedDetails,
    ErrorData,
    ErrorObject,
    error_by_name,
)
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, ExtractorProtocolError, decode_graph
from aconsole.core.infrastructure.codegraph.rust import from_cargo_metadata

_HOSTRUN: Final = Path("/home/sas/.containers/host-bins/hostrun")
_TIMEOUT_S: Final = 60.0
_TAIL: Final = 400
_OK: Final = 0
_NAME: Final = "CODEGRAPH_EXTRACTOR_FAILED"
_LANGUAGES: Final = frozenset({"python", "ts", "rust-metadata", "csharp"})
_UNKNOWN: Final = "unknown extractor language"
_BAD_PATH: Final = "extractor path must stay inside the worktree"
_TIMED_OUT: Final = "extractor timed out"
_MISSING: Final = "hostrun is missing"
_ABS: Final = "hostrun path must be absolute"
_POSITIVE: Final = "timeout must be positive"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractorRun:
    """A parsed graph, or the error object the caller returns to the client."""

    graph: ExtractorGraph | None
    error: ErrorObject | None


@final
class ExtractorRunner:
    """Build and run the devtools argv. Tests pass a stand-in for `hostrun`."""

    _hostrun: Path
    _timeout_s: float

    def __init__(self, hostrun: Path = _HOSTRUN, *, timeout_s: float = _TIMEOUT_S) -> None:
        if not hostrun.is_absolute():
            raise ValueError(_ABS)
        if timeout_s <= 0:
            raise ValueError(_POSITIVE)
        self._hostrun = hostrun
        self._timeout_s = timeout_s

    def argv(self, language: str, path: str) -> tuple[str, ...]:
        """`hostrun devtools <language> <path>`."""
        return (str(self._hostrun), "devtools", language, path)

    def run(self, language: str, path: str) -> ExtractorRun:
        refused = _refuse(language, path)
        if refused is not None:
            return ExtractorRun(graph=None, error=refused)
        completed = self._spawn(language, path)
        if isinstance(completed, ErrorObject):
            return ExtractorRun(graph=None, error=completed)
        return _parse(language, completed)

    def _spawn(self, language: str, path: str) -> subprocess.CompletedProcess[str] | ErrorObject:
        try:
            return subprocess.run(  # noqa: S603 -- argv is a list; the hostrun path is absolute
                self.argv(language, path),
                check=False,
                capture_output=True,
                timeout=self._timeout_s,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return _failure(language, _TIMED_OUT)
        except FileNotFoundError:
            return _failure(language, _MISSING)


def _refuse(language: str, path: str) -> ErrorObject | None:
    if language not in _LANGUAGES:
        return _failure(language, _UNKNOWN)
    if not _inside_worktree(path):
        return _failure(language, _BAD_PATH)
    return None


def _inside_worktree(path: str) -> bool:
    if path == "" or path.startswith("/"):
        return False
    return ".." not in path.split("/")


def _parse(language: str, completed: subprocess.CompletedProcess[str]) -> ExtractorRun:
    if completed.returncode != _OK:
        return ExtractorRun(graph=None, error=_failure(language, _tail(completed.stderr)))
    try:
        raw = completed.stdout.encode("utf-8")
        graph = from_cargo_metadata(raw) if language == "rust-metadata" else decode_graph(raw)
    except ExtractorProtocolError as exc:
        return ExtractorRun(graph=None, error=_failure(language, exc.detail))
    except ValueError as exc:
        return ExtractorRun(graph=None, error=_failure(language, str(exc)))
    return ExtractorRun(graph=graph, error=None)


def _tail(text: str) -> str:
    stripped = text.strip()
    if stripped == "":
        return "extractor failed"
    return stripped[-_TAIL:]


def _failure(language: str, stderr_tail: str) -> ErrorObject:
    spec = error_by_name(_NAME)
    return ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.core",
            retryable=spec.retryable,
            user_message_key="error.codegraph_extractor_failed",
            details=CodegraphExtractorFailedDetails(language=language, stderr_tail=stderr_tail[-_TAIL:]),
        ),
    )
