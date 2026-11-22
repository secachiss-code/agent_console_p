import hashlib
import io
import json
import stat
import zipfile
lazy from pathlib import Path

import pytest

from aconsole.core.infrastructure.analyzers import installer
from aconsole.core.infrastructure.analyzers.catalog import CATALOG, Analyzer, Artifact, get_analyzer
from aconsole.core.infrastructure.analyzers.installer import InstallError, VsixInstaller


def package(*, publisher: str = "charliermarsh", extra: str | None = None, symlink: bool = False) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "extension/package.json", json.dumps({"publisher": publisher, "name": "ruff", "version": "1.2.3"})
        )
        archive.writestr("extension/bin/tool", "tool")
        if extra:
            entry = zipfile.ZipInfo(extra)
            if symlink:
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "bad")
    return data.getvalue()


def registry(monkeypatch: pytest.MonkeyPatch, data: bytes, *, checksum: str | None = None) -> None:
    pin = Artifact("charliermarsh", "ruff", "1.2.3", "linux-x64", checksum or hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(installer, "get_analyzer", lambda _key: Analyzer("ruff", pin))
    base = "https://open-vsx.org/api/charliermarsh/ruff/linux-x64/1.2.3/file/"
    metadata = {
        "namespace": "charliermarsh",
        "name": "ruff",
        "version": "1.2.3",
        "targetPlatform": "linux-x64",
        "files": {"download": base + "ruff.vsix", "sha256": base + "ruff.sha256"},
    }

    def fetch(url: str, _limit: int) -> bytes:
        if url.endswith("/1.2.3"):
            return json.dumps(metadata).encode()
        if url.endswith(".sha256"):
            return (checksum or hashlib.sha256(data).hexdigest()).encode()
        return data

    monkeypatch.setattr(installer, "_fetch", fetch)


def test_catalog_is_closed() -> None:
    assert set(CATALOG) == {"ruff", "pylint"}
    with pytest.raises(ValueError, match="Unknown analyzer"):
        get_analyzer("other.extension")


def test_install_update_and_failure_preserves_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = VsixInstaller(tmp_path, target_platform="linux-x64")
    assert manager.current("ruff") is None
    registry(monkeypatch, package())
    first = manager.install("ruff")
    assert first == manager.current("ruff")
    assert (first.path / "bin/tool").read_text() == "tool"
    registry(monkeypatch, package(), checksum="0" * 64)
    with pytest.raises(InstallError, match="checksum mismatch"):
        manager.install("ruff")
    assert manager.current("ruff") == first
    registry(monkeypatch, package())
    second = manager.install("ruff")
    assert second.path != first.path
    assert first.path.exists()
    assert manager.current("ruff") == second


@pytest.mark.parametrize(
    "data",
    [package(publisher="evil"), package(extra="extension/../../escape"), package(extra="extension/link", symlink=True)],
)
def test_untrusted_archives_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    registry(monkeypatch, data)
    manager = VsixInstaller(tmp_path, target_platform="linux-x64")
    with pytest.raises(InstallError):
        manager.install("ruff")
    assert manager.current("ruff") is None
    assert not (tmp_path / "escape").exists()


def test_untrusted_urls_rejected() -> None:
    for url in ("http://open-vsx.org/x", "https://open-vsx.org.evil.test/x", "https://user@open-vsx.org/x"):
        with pytest.raises(InstallError):
            installer._safe_url(url)


def test_remove_deactivates_without_disturbing_readers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, package())
    manager = VsixInstaller(tmp_path, target_platform="linux-x64")
    installed = manager.install("ruff")
    assert manager.remove("ruff")
    assert manager.current("ruff") is None
    assert installed.path.is_dir()
    assert not manager.remove("ruff")


def test_incomplete_closure_cannot_replace_active_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, package())
    manager = VsixInstaller(tmp_path, target_platform="linux-x64")
    first = manager.install("ruff")
    artifact = Artifact("charliermarsh", "ruff", "1.2.3", "linux-x64", "0" * 64, ("ms-python.python",))
    monkeypatch.setattr(installer, "get_analyzer", lambda _key: Analyzer("ruff", artifact))
    with pytest.raises(InstallError, match="dependency closure"):
        manager.install("ruff")
    assert manager.current("ruff") == first


def test_validate_before_activation_and_preserve_old_release_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(monkeypatch, package())
    manager = VsixInstaller(tmp_path, target_platform="linux-x64")
    first = manager.install("ruff")
    staged_paths: list[Path] = []

    def reject(staged: installer.InstalledAnalyzer) -> None:
        assert manager.current("ruff") == first
        assert (staged.path / "bin/tool").read_text() == "tool"
        staged_paths.append(staged.path)
        message = "smoke test failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="smoke test failed"):
        manager.install("ruff", validate=reject)
    assert manager.current("ruff") == first
    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()

    def accept(staged: installer.InstalledAnalyzer) -> None:
        assert manager.current("ruff") == first
        staged_paths.append(staged.path)

    second = manager.install("ruff", validate=accept)
    assert second.path == staged_paths[-1]
    assert manager.current("ruff") == second
