"""Apply project trust before handing an analysis run to its confinement adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


class Profile(StrEnum):
    STATIC = "static"
    BUILD = "build"


@dataclass(frozen=True, slots=True, kw_only=True)
class Refusal:
    code: int
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectTrust:
    trusted: bool = False


class TrustStore(Protocol):
    def get(self, project_id: str) -> ProjectTrust | None:
        """Return None for an unregistered project."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSpec:
    profile: Profile
    project: Path
    scratch: Path
    command: tuple[str, ...]
    tool_env: Mapping[str, str]


def begin_analysis(
    store: TrustStore,
    project_id: str,
    spec: RunSpec,
    run: Callable[[RunSpec, Callable[[], bool]], int | Refusal],
    cancel: Callable[[], bool],
) -> int | Refusal:
    """Require stored trust for a build before probing or executing anything."""
    trust = store.get(project_id)
    if trust is None:
        return Refusal(code=2001, name="PROJECT_NOT_FOUND")
    if spec.profile is Profile.BUILD and not trust.trusted:
        return Refusal(code=2016, name="BUILD_UNTRUSTED")
    return run(spec, cancel)
