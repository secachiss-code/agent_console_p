"""Linux process identity and memory; PID reuse must never change the measured subject."""

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from aconsole.core.domain.errors import DomainError

_PROC = Path("/proc")
_KIB = 1024
_MEMORY_FIELDS = 3


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    boot_id: str


def process_identity(pid: int) -> ProcessIdentity:
    if pid <= 0:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    try:
        # comm may contain spaces and ')'; starttime is field 22 (index 19 after comm).
        tail = (_PROC / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
        return ProcessIdentity(pid, int(tail[19]), (_PROC / "sys/kernel/random/boot_id").read_text().strip())
    except (OSError, ValueError, IndexError) as exc:
        raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True) from exc


def memory_sample(identity: ProcessIdentity) -> dict[str, float]:
    """RSS/PSS/private memory in MiB, checked on both sides of the read."""
    if process_identity(identity.pid) != identity:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    try:
        values = {
            parts[0].removesuffix(":"): int(parts[1]) / _KIB
            for line in (_PROC / str(identity.pid) / "smaps_rollup").read_text().splitlines()
            if len(parts := line.split()) == _MEMORY_FIELDS and parts[2] == "kB"
        }
    except (OSError, ValueError) as exc:
        raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True) from exc
    if process_identity(identity.pid) != identity:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    return {"rss_mib": values["Rss"], "pss_mib": values["Pss"],
            "private_mib": sum(values.get(key, 0) for key in ("Private_Clean", "Private_Dirty", "Private_Hugetlb"))}


class UiProcesses:
    """UI peers registered from SO_PEERCRED, never from initialize.pid or CLI input."""

    def __init__(self) -> None:
        self._lock: RLock = RLock()
        self._peers: dict[ProcessIdentity, int] = {}

    def register(self, pid: int) -> ProcessIdentity:
        identity = process_identity(pid)
        with self._lock:
            self._peers[identity] = self._peers.get(identity, 0) + 1
        return identity

    def remove(self, identity: ProcessIdentity) -> None:
        with self._lock:
            count = self._peers.get(identity, 0)
            if count <= 1:
                _ = self._peers.pop(identity, None)
            else:
                self._peers[identity] = count - 1

    def require(self, pid: int) -> ProcessIdentity:
        identity = process_identity(pid)
        with self._lock:
            if identity not in self._peers:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        return identity
