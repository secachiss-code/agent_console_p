"""Provision AConsole's private, pinned Code-OSS distribution."""

from __future__ import annotations

import hashlib
import http.client
import platform
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import final

import msgspec

_VERSION = "1.143.17232"
_RELEASE = f"https://github.com/VSCodium/vscodium/releases/download/{_VERSION}"
_ASSETS = {
    "x86_64": ("x64", "ca1895aa040bd91a09c709d79eac7862d8c1aa0237fdc25b503deeb8bcd87efd"),
    "aarch64": ("arm64", "9f270ae242561812acd02be9cf5c13c848da359b356327adfe44b815a9532309"),
}
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


class HostInstallError(RuntimeError):
    """The managed editor runtime cannot be installed on this machine."""


@final
class HostInstaller:
    def __init__(self, root: Path) -> None:
        self._root = root

    def installed(self) -> Path | None:
        binary = self._root / _VERSION / "codium"
        return binary if binary.is_file() else None

    def install(self) -> Path:
        asset = _ASSETS.get(platform.machine()) if platform.system() == "Linux" else None
        if asset is None:
            msg = "No managed Code-OSS build for this operating system and architecture"
            raise HostInstallError(msg)
        if (binary := self.installed()) is not None:
            return binary

        architecture, digest = asset
        url = f"{_RELEASE}/VSCodium-linux-{architecture}-{_VERSION}.tar.gz"
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".host-", dir=self._root) as temporary:
            scratch = Path(temporary)
            archive = scratch / "host.tar.gz"
            _download(url, archive, digest)
            staged = scratch / "runtime"
            staged.mkdir()
            with tarfile.open(archive, "r:gz") as package:
                package.extractall(staged, filter="data")
            if not (staged / "codium").is_file():
                msg = "Code-OSS archive has no launcher"
                raise HostInstallError(msg)
            product = staged / "resources" / "app" / "product.json"
            settings = msgspec.json.decode(product.read_bytes(), type=dict[str, object])
            _ = settings.pop("extensionsGallery", None)
            _ = product.write_bytes(msgspec.json.encode(settings))
            destination = self._root / _VERSION
            try:
                _ = staged.rename(destination)
            except OSError:
                if self.installed() is None:
                    raise
        return destination / "codium"


def _download(url: str, destination: Path, expected: str) -> None:
    digest = hashlib.sha256()
    size = 0
    # urllib's return annotation is Any; HTTPS connections return an HTTPResponse.
    response: object = urllib.request.urlopen(url, timeout=30)  # noqa: S310  # pyright: ignore[reportAny]
    if not isinstance(response, http.client.HTTPResponse):
        msg = "Unexpected response downloading Code-OSS"
        raise HostInstallError(msg)
    with response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_ARCHIVE_BYTES:
                msg = "Code-OSS archive exceeds the download limit"
                raise HostInstallError(msg)
            digest.update(chunk)
            _ = output.write(chunk)
    if digest.hexdigest() != expected:
        msg = "Code-OSS archive checksum mismatch"
        raise HostInstallError(msg)
