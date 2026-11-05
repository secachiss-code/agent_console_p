"""Private, persistent clipboard attachments inside the tab's shared worktree."""

import contextlib
import os
import struct
import uuid
import zlib
from pathlib import Path

from aconsole.core.infrastructure.git.runner import GitRunner

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1024 * 1024
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_BYTES = 13
_CONTROL_BOUNDARY = 32
_EXCLUDE = "/.aconsole/clipboard/"


class InvalidImageError(ValueError):
    """A malformed or oversized clipboard image."""


def validate_png(png: bytes) -> None:
    """Bound encoded size and dimensions; reject truncated or corrupted chunks."""
    if len(png) > MAX_IMAGE_BYTES or not png.startswith(_SIGNATURE):
        msg = "Invalid PNG or image exceeds 8 MiB"
        raise InvalidImageError(msg)
    offset = len(_SIGNATURE)
    first = True
    data_seen = False
    while offset + 12 <= len(png):
        length = int.from_bytes(png[offset:offset + 4])
        end = offset + 12 + length
        if end > len(png):
            msg = "Truncated PNG"
            raise InvalidImageError(msg)
        kind = png[offset + 4:offset + 8]
        body = png[offset + 8:end - 4]
        crc = int.from_bytes(png[end - 4:end])
        if zlib.crc32(png[offset + 4:end - 4]) != crc:
            msg = "Corrupted PNG"
            raise InvalidImageError(msg)
        if first:
            if kind != b"IHDR" or length != _IHDR_BYTES:
                msg = "Missing PNG header"
                raise InvalidImageError(msg)
            width, height = struct.unpack(">II", body[:8])
            if not width or not height or width * height > MAX_IMAGE_PIXELS:
                msg = "Image exceeds 32 megapixels"
                raise InvalidImageError(msg)
            first = False
        data_seen |= kind == b"IDAT"
        if kind == b"IEND":
            if length or end != len(png) or not data_seen:
                msg = "Invalid PNG end"
                raise InvalidImageError(msg)
            return
        offset = end
    msg = "Incomplete PNG"
    raise InvalidImageError(msg)


def _exclude(worktree: Path, git: Path) -> None:
    result = GitRunner(git=git, timeout_s=10).read(
        worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude",
    )
    path = Path(result.text.strip())
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
        if _EXCLUDE not in stream.read().splitlines():
            _ = stream.write(f"\n{_EXCLUDE}\n")


def store_image(worktree: str, png: bytes, *, git: Path) -> Path:
    """Never follow attachment-directory symlinks or reuse a previous filename."""
    validate_png(png)
    root = Path(worktree)
    if not root.is_absolute() or any(ord(char) < _CONTROL_BOUNDARY for char in worktree):
        msg = "Invalid worktree path"
        raise InvalidImageError(msg)
    _exclude(root, git)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in (".aconsole", "clipboard"):
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, mode=0o700, dir_fd=directory)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        os.fchmod(directory, 0o700)
        name = f"{uuid.uuid4().hex}.png"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(png)
    finally:
        os.close(directory)
    return root / ".aconsole" / "clipboard" / name
