"""Path port. The domain asks for the real path of a word; the adapter reads the disk."""

from typing import Protocol


class PathResolver(Protocol):
    def resolve(self, path: str, cwd: str) -> str:
        """The real absolute path of `path` relative to `cwd`, with every symbolic link followed."""
        ...
