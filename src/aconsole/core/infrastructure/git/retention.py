"""Delete only explicitly expired legacy refs. Session history has no automatic GC."""

import logging
from collections.abc import Callable
from pathlib import Path

from aconsole.core.infrastructure.git.checkpoints import CheckpointGitError, GitCheckpoints
from aconsole.core.infrastructure.persistence.repositories.ref_deletions import RefDeletionQueue

_LOG = logging.getLogger(__name__)
_REF_PARTS = 5


def drain_ref_deletions(
    queue: RefDeletionQueue, delete_ref: Callable[[Path, str], None] | None = None,
) -> tuple[str, ...]:
    store = GitCheckpoints()
    remove = store.delete if delete_ref is None else delete_ref
    deleted: list[str] = []
    for job in queue.pending():
        parts = job.ref.split("/")
        if (not job.repo.is_absolute() or len(parts) != _REF_PARTS or parts[:3] != ["refs", "aconsole", "checkpoints"]
                or not parts[-1].isdigit()):
            _LOG.error("ref deletion request is outside legacy checkpoints")
            continue
        if queue.protected(job):
            queue.complete(job)
            continue
        try:
            remove(job.repo, job.ref)
        except (OSError, CheckpointGitError):
            _LOG.warning("checkpoint ref deletion will be retried: %s", job.ref, exc_info=True)
            continue
        queue.complete(job)
        deleted.append(job.ref)
    return tuple(deleted)
