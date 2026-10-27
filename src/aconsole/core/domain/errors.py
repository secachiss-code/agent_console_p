"""Domain errors. Codes match the contract registry; this module does not import msgspec."""

from dataclasses import dataclass
from typing import override


@dataclass(frozen=True, kw_only=True)
class DomainError(Exception):
    code: int
    name: str
    retryable: bool
    extra: tuple[str, ...] = ()

    @override
    def __str__(self) -> str:
        return f"{self.name} ({self.code})"
