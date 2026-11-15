"""File-scope decisions shared by pre-write hooks and post-write verification."""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from aconsole.core.domain.errors import DomainError


@dataclass(frozen=True, kw_only=True)
class ScopeCheck:
    violations: tuple[str, ...]
    review_required: bool

    def require_allowed(self) -> None:
        if self.violations:
            raise DomainError(code=4015, name="SCOPE_VIOLATION", retryable=False, extra=self.violations)


def check_scope(paths: tuple[str, ...], changed: tuple[str, ...]) -> ScopeCheck:
    """Empty explicit scope grants no writes; callers distinguish it from no active node.

    A single star matches one path segment, and ** matches zero or more segments.
    Changed names are literal, including any brackets or question marks in filenames.
    """
    violations = tuple(
        sorted(
            {
                path
                for path in changed
                if not _safe(path) or not any(_safe(pattern) and _matches(pattern, path) for pattern in paths)
            }
        )
    )
    return ScopeCheck(violations=violations, review_required=any(path.startswith("tests/") for path in violations))


def _safe(path: str) -> bool:
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and "\x00" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def _matches(pattern: str, path: str) -> bool:
    parts = pattern.split("/")
    states: set[int] = {0}
    for segment in (*path.split("/"), None):
        # Epsilon closure: ** can also consume no directory components.
        for index in range(len(parts)):
            if index in states and parts[index] == "**":
                states.add(index + 1)
        if segment is None:
            return len(parts) in states
        following: set[int] = set()
        for index in states:
            if index < len(parts):
                if parts[index] == "**":
                    following.add(index)
                elif fnmatchcase(segment, parts[index]):
                    following.add(index + 1)
        states = following
    return False
