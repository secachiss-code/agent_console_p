"""Filesystem path resolver. It is the production implementation of the `PathResolver` port."""

import os
from pathlib import Path
from typing import override

from aconsole.core.ports.paths import PathResolver


class OsPathResolver(PathResolver):
    @override
    def resolve(self, path: str, cwd: str) -> str:
        return os.path.realpath(Path(cwd) / path)


def runtime_cache_path() -> Path | None:
    """Never fall back to persistent storage when the runtime directory is absent."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) / "aconsole" / "cache.db" if runtime and Path(runtime).is_absolute() else None
