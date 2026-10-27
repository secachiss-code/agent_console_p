"""Cache the PATH that `distrobox enter -d` would put inside a container.

The cache key is the sha256 of `distrobox.ini`. A new hash drops every container.
The same hash and the same container do not run distrobox again. The cache is shared by
threads: a probe started under an older hash does not store its answer under the new one.
"""

import hashlib
import subprocess
import threading
from pathlib import Path
from typing import Final, final

from aconsole.core.infrastructure.containers.podman import check_container_name

_TIMEOUT_S: Final = 10.0
_OK: Final = 0
_PATH_PREFIX: Final = "PATH="
_ENV_EQ_PATH: Final = "--env=PATH="
_NO_PATH: Final = "distrobox dry-run has no PATH"
_FAILED: Final = "distrobox dry-run failed"
_TIMED_OUT: Final = "distrobox timed out"
_MISSING: Final = "distrobox binary missing"
_INI_MISSING: Final = "distrobox ini is missing"


@final
class DistroboxError(Exception):
    """The dry-run did not yield a PATH, or distrobox did not run."""

    def __init__(self, message: str, *, code: int = 1, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def path_from_dry_run(text: str) -> str:
    """Take the last `PATH=` assignment from a `distrobox enter -d` line."""
    found = ""
    for token in text.split():
        if token.startswith(_ENV_EQ_PATH):
            found = token.removeprefix(_ENV_EQ_PATH)
        elif token.startswith(_PATH_PREFIX):
            found = token.removeprefix(_PATH_PREFIX)
    if found == "":
        raise DistroboxError(_NO_PATH, detail=text)
    return found


@final
class DistroboxEnvCache:
    """Remember PATH per container until the ini bytes change."""

    def __init__(self, ini: Path, binary: Path, *, timeout_s: float = _TIMEOUT_S) -> None:
        if not binary.is_absolute():
            message = "distrobox binary path must be absolute"
            raise ValueError(message)
        if timeout_s <= 0:
            message = "distrobox timeout must be positive"
            raise ValueError(message)
        self._ini = ini
        self._binary = binary
        self._timeout_s = timeout_s
        self._digest = ""
        self._paths: dict[str, str] = {}
        self._lock = threading.Lock()

    def path_for(self, container: str) -> str:
        """Return the cached PATH, probing `distrobox enter -d` only on a miss."""
        check_container_name(container)
        digest = self._hash()
        with self._lock:
            if digest != self._digest:
                self._paths.clear()
                self._digest = digest
            cached = self._paths.get(container)
        if cached is not None:
            return cached
        # The probe can take seconds, so it runs outside the lock.
        path = path_from_dry_run(self._dry_run(container))
        with self._lock:
            if self._digest == digest:
                self._paths[container] = path
        return path

    def _hash(self) -> str:
        try:
            body = self._ini.read_bytes()
        except FileNotFoundError as exc:
            raise DistroboxError(_INI_MISSING) from exc
        return hashlib.sha256(body).hexdigest()

    def _dry_run(self, container: str) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the binary path is absolute
                [str(self._binary), "enter", "-d", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise DistroboxError(_TIMED_OUT) from exc
        except FileNotFoundError as exc:
            raise DistroboxError(_MISSING) from exc
        if completed.returncode != _OK:
            raise DistroboxError(_FAILED, code=completed.returncode, detail=completed.stderr)
        return completed.stdout
