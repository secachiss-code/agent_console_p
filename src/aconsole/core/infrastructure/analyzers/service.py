"""Shared management and diagnostics for approved analyzer extensions."""

from __future__ import annotations

import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Literal, final

import msgspec

from aconsole.contracts.app import AnalyzerItem, AnalyzerListResult
from aconsole.core.infrastructure.analyzers import runtime
from aconsole.core.infrastructure.analyzers.catalog import CATALOG, Artifact, get_analyzer
from aconsole.core.infrastructure.analyzers.host_install import HostInstaller, HostInstallError
from aconsole.core.infrastructure.analyzers.installer import (
    InstalledAnalyzer,
    InstallError,
    VsixInstaller,
    host_platform,
)
from aconsole.core.infrastructure.persistence.startup import default_database

Action = Literal["install", "update", "remove", "check"]
_PROBE_CODES = {"ruff": "F401", "pylint": "E0401"}
_ANALYZER_NAMES = {"ruff": "Ruff", "pylint": "Pylint"}
_MAX_READY = 256
_PROBE = {"aconsole_probe.py": "import aconsole_analyzer_probe_missing\n"}


def _matches(installed: InstalledAnalyzer, pin: Artifact) -> bool:
    return (installed.version, installed.platform, installed.sha256) == (pin.version, pin.platform, pin.sha256)


@final
class AnalyzerService:
    def __init__(self, root: Path | None = None) -> None:
        directory = default_database().parent / "analyzers" if root is None else root
        self._enabled_marker = directory / "enabled"
        self._extensions = directory / "extensions"
        self._host = HostInstaller(directory / "host")
        self._lock = threading.Lock()
        self._ready: OrderedDict[tuple[Path, str, str], tuple[str, str]] = OrderedDict()

    def listing(self, root: Path) -> AnalyzerListResult:
        return AnalyzerListResult(items=tuple(self.status(root, key) for key in CATALOG), enabled=self.enabled())

    def enabled(self) -> bool:
        return self._enabled_marker.is_file()

    def set_enabled(self, root: Path, *, enabled: bool) -> AnalyzerListResult:
        if not enabled:
            self._enabled_marker.unlink(missing_ok=True)
        else:
            directory = self._enabled_marker.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=directory) as scratch:
                marker = Path(scratch) / "enabled"
                marker.touch()
                _ = marker.replace(self._enabled_marker)
        return self.listing(root)

    def status(self, root: Path, key: str) -> AnalyzerItem:
        spec = get_analyzer(key)
        state, detail, installed_version = "available", "", None
        try:
            platform = host_platform()
            artifacts = (spec.artifact, *spec.dependencies)
            if any(artifact.platform not in {platform, "universal"} for artifact in artifacts):
                state, detail = "unsupported", f"No approved extension build for {platform}"
            elif (installed := VsixInstaller(self._extensions).current(key)) is not None:
                installed_version = installed.version
                if not _matches(installed, spec.artifact):
                    state = "update_available"
                else:
                    state, detail = self._ready.get((root, key, installed.version), ("installed", ""))
        except InstallError as exc:
            state, detail = "error", str(exc)
        return AnalyzerItem(id=key, name=_ANALYZER_NAMES[key], version=spec.artifact.version,
                            installed_version=installed_version, state=state, detail=detail)

    def manage(self, root: Path, key: str, action: Action) -> AnalyzerItem:
        status = self.status(root, key)
        if status.state == "unsupported":
            return status
        if not self._lock.acquire(blocking=False):
            return msgspec.structs.replace(status, detail="Another analyzer operation is running")
        try:
            self._manage(root, key, action)
        except (HostInstallError, InstallError, OSError, ValueError, msgspec.DecodeError) as exc:
            return msgspec.structs.replace(self.status(root, key), state="error", detail=str(exc)[:1000])
        finally:
            self._lock.release()
        return self.status(root, key)

    def _manage(self, root: Path, key: str, action: Action) -> None:
        installer = VsixInstaller(self._extensions)
        if action == "remove":
            _ = installer.remove(key)
            for marker in tuple(self._ready):
                if marker[1] == key:
                    del self._ready[marker]
            return
        installed = installer.current(key)
        if action in {"install", "update"}:
            _ = self._host.install()
            if installed is None or not _matches(installed, get_analyzer(key).artifact):
                installed = installer.install(key, validate=self._verify)
            else:
                self._verify(installed)
        else:
            if installed is None:
                return
            try:
                self._verify(installed)
            except HostInstallError as exc:
                self._remember(root, installed, "error", str(exc))
                raise
        detail = f"The extension reported {_PROBE_CODES[key]} on the probe file"
        if key == "pylint":
            detail += f" · Python {runtime.PYTHON}"
        self._remember(root, installed, "ready", detail)

    def _verify(self, installed: InstalledAnalyzer) -> None:
        report = self._run(installed, _PROBE)
        expected = _PROBE_CODES[installed.key]
        if report.error or not any((row.code or "").partition(":")[0] == expected for row in report.diagnostics):
            message = report.error or f"The extension did not report {expected} on the probe file"
            raise HostInstallError(message)

    def _remember(self, root: Path, installed: InstalledAnalyzer, state: str, detail: str) -> None:
        marker = (root, installed.key, installed.version)
        self._ready[marker] = (state, detail)
        self._ready.move_to_end(marker)
        if len(self._ready) > _MAX_READY:
            _ = self._ready.popitem(last=False)

    def diagnostics(self, key: str, files: dict[str, str]) -> tuple[str | None, runtime.BridgeReport]:
        _ = get_analyzer(key)
        version = None
        if not self.enabled():
            return version, runtime.BridgeReport(error="Python analyzers are disabled", failure="unavailable")
        if not self._lock.acquire(blocking=False):
            return version, runtime.BridgeReport(error="Another analyzer operation is running", failure="busy")
        try:
            installed = VsixInstaller(self._extensions).current(key)
            if installed is None:
                return version, runtime.BridgeReport(error="Analyzer is not installed", failure="unavailable")
            version = installed.version
            return version, self._run(installed, files)
        except (HostInstallError, InstallError, OSError, ValueError, msgspec.DecodeError) as exc:
            return version, runtime.BridgeReport(error=str(exc)[:1000])
        finally:
            self._lock.release()

    def _run(self, installed: InstalledAnalyzer, files: dict[str, str]) -> runtime.BridgeReport:
        if (binary := self._host.installed()) is None:
            return runtime.BridgeReport(error="Managed Code-OSS runtime is not installed", failure="unavailable")
        spec = get_analyzer(installed.key)
        name = spec.artifact.extension_id
        packages = {name: installed.path}
        packages.update(zip((dep.extension_id for dep in spec.dependencies), installed.dependencies, strict=True))
        return runtime.run(binary, name, (installed.key,), packages, files)
