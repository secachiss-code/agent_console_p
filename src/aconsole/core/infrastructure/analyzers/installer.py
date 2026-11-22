"""Download and activate allowlisted VSIX packages from Open VSX.

Registry checksums provide transport integrity, not an independent publisher signature.
Installations are staged in private, immutable-by-convention directories; activation is
an atomic pointer replacement, so a failed update leaves the previous version usable.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPMessage
from pathlib import Path, PurePosixPath
from typing import IO, override
from urllib.parse import urlsplit

import msgspec

from .catalog import Analyzer, Artifact, get_analyzer

_MAX_METADATA = 1024 * 1024
_MAX_ARCHIVE = 200 * 1024 * 1024
_MAX_UNPACKED = 500 * 1024 * 1024
_MAX_FILES = 20000
_SAFE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")
_HOSTS = frozenset({"open-vsx.org", "openvsx.eclipsecontent.org"})


class InstallError(Exception):
    """The registry or package could not be safely installed."""


@dataclass(frozen=True, slots=True)
class InstalledAnalyzer:
    key: str
    version: str
    platform: str
    sha256: str
    path: Path
    dependencies: tuple[Path, ...] = ()


def host_platform() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "win32"}.get(platform.system())
    if not arch or not system:
        msg = "Unsupported operating system or architecture"
        raise InstallError(msg)
    return f"{system}-{arch}"


def _safe_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        msg = "Untrusted registry URL"
        raise InstallError(msg)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> urllib.request.Request | None:
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, limit: int) -> bytes:
    _safe_url(url)
    opener = urllib.request.build_opener(_SafeRedirect())
    # urllib typeshed exposes OpenerDirector.open as Any, although HTTPS returns a binary stream.
    response: IO[bytes] = opener.open(url, timeout=30)  # pyright: ignore[reportAny]
    with response:
        data = response.read(limit + 1)
    if len(data) > limit:
        msg = "Registry response exceeds size limit"
        raise InstallError(msg)
    return data


def _file_url(value: object, analyzer: Artifact, version: str, target: str, suffix: str) -> str:
    if not isinstance(value, str):
        msg = "Missing registry file URL"
        raise InstallError(msg)
    _safe_url(value)
    parsed = urlsplit(value)
    prefix = f"/api/{analyzer.namespace}/{analyzer.name}/"
    # Registry metadata must point to this exact extension, version and platform.
    expected = f"{target}/{version}/file/" if target != "universal" else f"{version}/file/"
    if (
        parsed.hostname != "open-vsx.org"
        or not parsed.path.startswith(prefix + expected)
        or not parsed.path.endswith(suffix)
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
        or ".." in parsed.path
    ):
        msg = "Unexpected registry file URL"
        raise InstallError(msg)
    return value


def _extract(archive: Path, dest: Path, analyzer: Artifact, version: str) -> None:
    total = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > _MAX_FILES:
                msg = "Too many archive entries"
                raise InstallError(msg)
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                mode = entry.external_attr >> 16
                if (
                    not parts
                    or name.startswith("/")
                    or "\\" in name
                    or ".." in parts
                    or ":" in name
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                ):
                    msg = "Unsafe archive entry"
                    raise InstallError(msg)
                normalized = "/".join(parts)
                if normalized in seen:
                    msg = "Duplicate archive entry"
                    raise InstallError(msg)
                seen.add(normalized)
                total += entry.file_size
                if total > _MAX_UNPACKED:
                    msg = "Archive exceeds extraction limit"
                    raise InstallError(msg)
                if parts[0] != "extension" or len(parts) == 1:
                    continue
                output = dest.joinpath(*parts[1:])
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, output.open("xb") as sink:
                        shutil.copyfileobj(source, sink)
                    output.chmod(0o755 if mode & 0o111 else 0o644)
        manifest = msgspec.json.decode((dest / "package.json").read_bytes(), type=dict[str, object])
        if (
            manifest.get("publisher") != analyzer.namespace
            or manifest.get("name") != analyzer.name
            or manifest.get("version") != version
            or manifest.get("extensionDependencies", []) != list(analyzer.dependencies)
        ):
            msg = "VSIX manifest identity mismatch"
            raise InstallError(msg)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        msg = "Invalid VSIX archive"
        raise InstallError(msg) from exc


class VsixInstaller:
    """Manage verified installations under a caller-owned private data directory."""

    def __init__(self, root: Path, *, target_platform: str | None = None) -> None:
        self.root: Path = Path(root)
        self.target_platform: str = target_platform or host_platform()
        if self.target_platform not in {
            f"{os_name}-{arch}" for os_name in ("linux", "darwin", "win32") for arch in ("x64", "arm64")
        }:
            msg = "Unsupported target platform"
            raise InstallError(msg)

    def install(self, key: str, *, validate: Callable[[InstalledAnalyzer], None] | None = None) -> InstalledAnalyzer:
        """Stage the pinned closure, optionally validate it, and atomically activate it.

        The callback receives the final release paths before activation. If it raises,
        the staged release is removed and the previous activation remains in place.
        """
        analyzer = get_analyzer(key)
        artifacts = (*analyzer.dependencies, analyzer.artifact)
        if any(a.platform not in (self.target_platform, "universal") for a in artifacts):
            msg = "No approved artifact for this platform"
            raise InstallError(msg)
        expected = {a.extension_id for a in analyzer.dependencies}
        if set(analyzer.artifact.dependencies) != expected or any(a.dependencies for a in analyzer.dependencies):
            msg = "Incomplete approved dependency closure"
            raise InstallError(msg)
        base = self.root / analyzer.key
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=base) as temporary:
                stage = Path(temporary)
                release = stage / "release"
                release.mkdir()
                for artifact in artifacts:
                    self._stage_artifact(artifact, release, stage)
                installed = Path(tempfile.mkdtemp(prefix="release-", dir=base))
                try:
                    for child in release.iterdir():
                        _ = shutil.move(str(child), str(installed / child.name))
                    if validate is not None:
                        validate(self._result(analyzer, installed))
                    record = {
                        "directory": installed.name,
                        "version": analyzer.artifact.version,
                        "sha256": analyzer.artifact.sha256,
                        "platform": analyzer.artifact.platform,
                    }
                    pointer = stage / "current.json"
                    _ = pointer.write_text(json.dumps(record), encoding="utf-8")
                    _ = pointer.replace(base / "current.json")
                except BaseException:
                    shutil.rmtree(installed, ignore_errors=True)
                    raise
        except (OSError, ValueError, UnicodeError) as exc:
            msg = "Unable to install analyzer from Open VSX"
            raise InstallError(msg) from exc
        return self._result(analyzer, installed)

    def _stage_artifact(self, artifact: Artifact, release: Path, stage: Path) -> None:
        platform_part = "" if artifact.platform == "universal" else f"/{artifact.platform}"
        url = f"https://open-vsx.org/api/{artifact.namespace}/{artifact.name}{platform_part}/{artifact.version}"
        metadata = msgspec.json.decode(_fetch(url, _MAX_METADATA), type=dict[str, object])
        if (
            metadata.get("namespace") != artifact.namespace
            or metadata.get("name") != artifact.name
            or metadata.get("version") != artifact.version
            or metadata.get("targetPlatform") != artifact.platform
        ):
            msg = "Registry metadata identity or platform mismatch"
            raise InstallError(msg)
        files = msgspec.convert(metadata.get("files"), type=dict[str, object])
        download = _file_url(files.get("download"), artifact, artifact.version, artifact.platform, ".vsix")
        data = _fetch(download, _MAX_ARCHIVE)
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            msg = "VSIX checksum mismatch"
            raise InstallError(msg)
        archive = stage / "package.vsix"
        _ = archive.write_bytes(data)
        dest = release / artifact.extension_id
        dest.mkdir()
        _extract(archive, dest, artifact, artifact.version)

    @staticmethod
    def _result(analyzer: Analyzer, release: Path) -> InstalledAnalyzer:
        artifact = analyzer.artifact
        return InstalledAnalyzer(
            analyzer.key,
            artifact.version,
            artifact.platform,
            artifact.sha256,
            release / artifact.extension_id,
            tuple(release / dep.extension_id for dep in analyzer.dependencies),
        )

    def current(self, key: str) -> InstalledAnalyzer | None:
        """Read the active release; older pinned releases remain visible after a catalog update."""
        analyzer = get_analyzer(key)
        base = self.root / analyzer.key
        try:
            record = msgspec.json.decode((base / "current.json").read_bytes(), type=dict[str, object])
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            msg = "Invalid installation record"
            raise InstallError(msg) from exc
        version = record.get("version")
        target = record.get("platform")
        digest = record.get("sha256")
        directory = record.get("directory")
        if (
            not isinstance(version, str)
            or not _SAFE_VERSION.fullmatch(version)
            or not isinstance(target, str)
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            or not isinstance(directory, str)
            or not re.fullmatch(r"release-[a-zA-Z0-9_-]+", directory)
        ):
            msg = "Invalid installation record"
            raise InstallError(msg)
        release = base / directory
        paths = (
            release / analyzer.artifact.extension_id,
            *(release / dep.extension_id for dep in analyzer.dependencies),
        )
        if release.is_symlink() or any(not p.is_dir() or p.is_symlink() for p in paths):
            msg = "Installed analyzer is missing"
            raise InstallError(msg)
        return InstalledAnalyzer(key, version, target, digest, paths[0], paths[1:])

    def remove(self, key: str) -> bool:
        """Deactivate an analyzer atomically; old releases remain for existing readers."""
        analyzer = get_analyzer(key)
        try:
            (self.root / analyzer.key / "current.json").unlink()
        except FileNotFoundError:
            return False
        return True
