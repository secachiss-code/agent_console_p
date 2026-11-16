"""One argv contract for planning, freezing and executing micro gates."""

from collections.abc import Mapping
from typing import TypeGuard

from aconsole.core.domain.errors import DomainError

MICRO_COMMANDS = ("format", "fix", "lint", "types", "tests")


def micro_commands(profile: Mapping[str, object], *, location: str = "gate_profile") -> dict[str, tuple[str, ...]]:
    """Accept the nested profile and the original flat representation, without defaults."""
    raw = profile.get("micro", profile)
    if not isinstance(raw, Mapping):
        detail = f"{location}.micro must be an object"
        raise _invalid(detail)
    missing = [key for key in MICRO_COMMANDS if key not in raw]
    if missing:
        detail = f"{location}.micro missing commands: {', '.join(missing)}"
        raise _invalid(detail)
    commands: dict[str, tuple[str, ...]] = {}
    for key in MICRO_COMMANDS:
        argv = raw[key]
        if not _argv_sequence(argv) or not argv:
            detail = f"{location}.micro.{key} must be a nonempty argv of nonempty strings without NUL"
            raise _invalid(detail)
        parts: list[str] = []
        for arg in argv:
            if not isinstance(arg, str) or not arg.strip() or "\x00" in arg:
                detail = f"{location}.micro.{key} must be a nonempty argv of nonempty strings without NUL"
                raise _invalid(detail)
            parts.append(arg)
        commands[key] = tuple(parts)
    return commands


def _invalid(detail: str) -> DomainError:
    return DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(detail,))


def _argv_sequence(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))
