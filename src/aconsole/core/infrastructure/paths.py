"""Filesystem path resolver. It is the production implementation of the `PathResolver` port."""

import os
from pathlib import Path
from typing import override

from aconsole.core.ports.paths import PathResolver


class OsPathResolver(PathResolver):
    @override
    def resolve(self, path: str, cwd: str) -> str:
        return os.path.realpath(Path(cwd) / path)
