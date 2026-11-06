"""Descriptor-relative launcher writes: no symlink component is followed."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

def directory(path: Path) -> int:
    """Return an owned directory descriptor reached from / without following links."""
    if not path.is_absolute() or ".." in path.parts:
        message = "directory must be absolute without parent traversal"
        raise ValueError(message)
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            following = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor

def check_home(path: Path) -> None:
    os.close(directory(path))

def private_dir(path: Path) -> None:
    parent = directory(path.parent)
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(existing.st_mode):
                message = "refusing to follow a symlink"
                raise ValueError(message) from None
            if not stat.S_ISDIR(existing.st_mode):
                message = "path exists and is not a directory"
                raise ValueError(message) from None
        child = os.open(path.name, _DIR_FLAGS, dir_fd=parent)
        try:
            os.fchmod(child, 0o700)
        finally:
            os.close(child)
    finally:
        os.close(parent)

def read_bytes(path: Path) -> bytes:
    parent = directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()
    finally:
        os.close(parent)

def atomic_write(path: Path, payload: bytes, mode: int) -> None:
    parent = directory(path.parent)
    temporary = f".aconsole-{secrets.token_hex(16)}"
    created = False
    try:
        try:
            existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            message = "refusing to follow a symlink"
            raise ValueError(message)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             mode, dir_fd=parent)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        created = False
        os.fsync(parent)
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=parent)
        finally:
            os.close(parent)


def remove_tree(path: Path) -> None:
    try:
        parent = directory(path.parent)
    except FileNotFoundError:
        return
    try:
        try:
            existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(existing.st_mode):
            message = "refusing to follow a symlink"
            raise ValueError(message)
        if not stat.S_ISDIR(existing.st_mode):
            message = "path exists and is not a directory"
            raise ValueError(message)
        shutil.rmtree(path.name, dir_fd=parent)
    finally:
        os.close(parent)
