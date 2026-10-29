"""Vault notes round-trip byte for byte, and a repeated write leaves mtime alone."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.vault.writer import UserSectionError, VaultWriter, append_under, render

if TYPE_CHECKING:
    from pathlib import Path

_SAMPLE = "tests/fixtures/vault/sample"
_WHEN = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.moment = _WHEN

    def __call__(self) -> datetime:
        return self.moment


def test_vault_writer_round_trip_is_byte_for_byte(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    shutil.copytree(_SAMPLE, root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        original = path.read_bytes()
        rendered = render(original.decode("utf-8"))
        assert rendered.encode("utf-8") == original


def test_vault_writer_repeat_does_not_change_mtime(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    shutil.copyfile(f"{_SAMPLE}/sample/plans/sample-plan/N-01-vault-sample.md", source)
    clock = _Clock()
    writer = VaultWriter(now=clock)
    text = source.read_text(encoding="utf-8")
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    before = source.stat().st_mtime_ns
    assert writer.write(source, render(text)) is False
    assert source.stat().st_mtime_ns == before
    updated = append_under(text, "Журнал", "- 2026-10-29 12:00 app: still here")
    assert "Sample task for the vault spike." in updated
    assert writer.write(source, updated) is True
    assert source.stat().st_mtime_ns != before
    assert writer.echo.is_echo(source, updated, clock.moment) is True
    clock.moment += timedelta(seconds=11)
    assert writer.echo.is_echo(source, updated, clock.moment) is False


def test_vault_writer_refuses_a_user_section() -> None:
    with pytest.raises(UserSectionError):
        _ = append_under("# Title\n\n## Цель\nkeep\n", "Цель", "replaced")
