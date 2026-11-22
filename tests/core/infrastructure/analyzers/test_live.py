"""Opt-in acceptance against actual VSCodium and Open VSX releases."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
from mcp import Client

from aconsole.core.infrastructure.analyzers.service import AnalyzerService
from tests.core.test_mcp_tools import _call, _Clock, _server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(os.getenv("ACONSOLE_ANALYZER_LIVE") != "1", reason="downloads the pinned host and VSIX files")
def test_clean_install_to_mcp_and_new_worktree_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    worktree = tmp_path / "workspace"
    worktree.mkdir()
    probe = worktree / "example.py"
    _ = probe.write_text("import aconsole_missing_module\n")
    service = AnalyzerService()
    assert service.set_enabled(worktree, enabled=True).enabled
    for key in ("ruff", "pylint"):
        installed = service.manage(worktree, key, "install")
        assert installed.installed_version == installed.version
        assert installed.state == "ready", installed.detail
    with pytest.raises(ValueError, match="Unknown analyzer"):
        _ = service.manage(worktree, "arbitrary.vsix", "install")

    _, server = _server(_Clock(), "analyzer-live", worktree=worktree)

    async def scenario() -> None:
        async with Client(server) as client:
            listing = await _call(client, "analyzer_list", {})
            assert {item["id"] for item in listing["items"] if item.get("installed_version")} == {"ruff", "pylint"}
            versions: set[str] = set()
            for key, expected in (("ruff", "F401"), ("pylint", "E0401")):
                report = await _call(client, "analyzer_diagnostics", {"analyzer": key, "targets": ["example.py"]})
                assert report["observed"] == ["example.py"]
                assert any(item["code"].partition(":")[0] == expected for item in report["diagnostics"])
                versions.add(report["revision"])
            assert len(versions) == 1
            legacy = await _call(client, "index_lint", {"scope": "targets", "targets": ["example.py"]})
            assert legacy["checked"] == 1
            assert {item["code"] for item in legacy["diagnostics"]} == {"F401"}
            _ = probe.write_text("print(unknown_name)\n")
            report = await _call(client, "analyzer_diagnostics", {"analyzer": "ruff", "targets": ["example.py"]})
            assert report["revision"] not in versions
            assert {item["code"] for item in report["diagnostics"]} == {"F821"}

    asyncio.run(scenario())


def test_enablement_persists_and_blocks_diagnostics(tmp_path: Path) -> None:
    service = AnalyzerService(tmp_path)
    assert not service.listing(tmp_path).enabled
    _, disabled = service.diagnostics("ruff", {"probe.py": "import os\n"})
    assert disabled.error == "Python analyzers are disabled"
    assert service.set_enabled(tmp_path, enabled=True).enabled
    assert AnalyzerService(tmp_path).enabled()
    assert not service.set_enabled(tmp_path, enabled=False).enabled
    assert not AnalyzerService(tmp_path).enabled()
