"""Diff and apply `distrobox.ini`.

`apply` writes the file only after `preview` issued the same token for the same
proposed bytes and while the file still holds the bytes the user saw in the diff.
A missing or wrong token, or a file changed since the preview, is
`CONFIRM_TOKEN_INVALID`. Bytes are written as given (no newline translation).
`<name>.bak` keeps the original file: it is written by the first apply only.
This module does not run `distrobox assemble`: the host policy forbids that command.
"""

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.contracts.errors import error_by_name
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.containers.podman import check_container_name

_BEFORE: Final = "distrobox.ini"
_AFTER: Final = "distrobox.ini.next"
_MISSING: Final = "distrobox ini is missing"
_ABS_BINARY: Final = "distrobox binary path must be absolute"
_ABS_INI: Final = "distrobox ini path must be absolute"
_EMPTY: Final = ""


@final
class IniError(Exception):
    """The ini file could not be read."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True, kw_only=True)
class IniPreview:
    """The diff the user confirms, and the token `apply` requires."""

    diff: str
    confirm: str


def ini_diff(before: str, after: str) -> str:
    """Unified diff of two ini texts. The headers are stable names, not paths."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=_BEFORE,
        tofile=_AFTER,
    )
    return "".join(lines)


def assemble_argv(binary: Path, ini: Path, name: str) -> tuple[str, ...]:
    """Argv for `distrobox assemble create --replace`. This does not run it."""
    if not binary.is_absolute():
        raise ValueError(_ABS_BINARY)
    if not ini.is_absolute():
        raise ValueError(_ABS_INI)
    check_container_name(name)
    return (str(binary), "assemble", "create", "--file", str(ini), "--name", name, "--replace")


@final
class DistroboxIni:
    """One ini file. The pending token is kept in memory and used once."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._token = _EMPTY
        self._digest = _EMPTY
        self._source = _EMPTY

    def diff(self, proposed: str) -> str:
        """Diff the file against `proposed` without storing a token."""
        return ini_diff(self._read(), proposed)

    def preview(self, proposed: str, *, confirm: str) -> IniPreview:
        """Remember `confirm` for these exact bytes and return the diff."""
        if confirm == _EMPTY:
            raise _invalid()
        raw = self._read_bytes()
        text = ini_diff(raw.decode("utf-8"), proposed)
        self._token = confirm
        self._digest = hashlib.sha256(proposed.encode()).hexdigest()
        self._source = hashlib.sha256(raw).hexdigest()
        return IniPreview(diff=text, confirm=confirm)

    def apply(self, proposed: str, confirm: str) -> Path:
        """Replace the ini. The first apply copies the original bytes to `<name>.bak`; later applies keep it."""
        digest = hashlib.sha256(proposed.encode()).hexdigest()
        if confirm == _EMPTY or confirm != self._token or digest != self._digest:
            raise _invalid()
        current = self._read_bytes()
        if hashlib.sha256(current).hexdigest() != self._source:
            # The file changed after the user saw the diff: the confirmed diff no longer describes this write.
            self._forget()
            raise _invalid()
        backup = self._path.with_name(self._path.name + ".bak")
        if not backup.exists():
            _ = backup.write_bytes(current)
        _ = self._path.write_bytes(proposed.encode("utf-8"))
        self._forget()
        return backup

    def _forget(self) -> None:
        self._token = _EMPTY
        self._digest = _EMPTY
        self._source = _EMPTY

    def _read(self) -> str:
        return self._read_bytes().decode("utf-8")

    def _read_bytes(self) -> bytes:
        try:
            return self._path.read_bytes()
        except FileNotFoundError as exc:
            raise IniError(_MISSING) from exc


def _invalid() -> DomainError:
    spec = error_by_name("CONFIRM_TOKEN_INVALID")
    return DomainError(code=spec.code, name=spec.name, retryable=spec.retryable)
