"""Vault notes round-trip byte for byte, and a repeated write leaves mtime alone."""

from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread
from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.vault.writer import EchoBook, UserSectionError, VaultWriter, append_under, render

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
    assert len(writer.echo) == 0


def test_vault_writer_refuses_a_user_section() -> None:
    with pytest.raises(UserSectionError):
        _ = append_under("# Title\n\n## Цель\nkeep\n", "Цель", "replaced")



def test_echo_prune_and_remember_are_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = EchoBook()
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    book.remember(first, "one", _WHEN)
    now = _WHEN + timedelta(seconds=11)
    entered, release, writing, written = Event(), Event(), Event(), Event()
    prune = EchoBook._prune

    def paused_prune(self: EchoBook, at: datetime) -> None:
        if current_thread().name.startswith("echo-reader"):
            entered.set()
            assert release.wait(3)
        prune(self, at)

    def remember() -> None:
        writing.set()
        book.remember(second, "two", now)
        written.set()

    monkeypatch.setattr(EchoBook, "_prune", paused_prune)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-reader") as reader,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-writer") as writer,
    ):
        observed = reader.submit(book.is_echo, first, "one", now)
        assert entered.wait(3)
        saved = writer.submit(remember)
        try:
            assert writing.wait(3)
            assert not written.wait(0.1)
        finally:
            release.set()
        assert observed.result(timeout=3) is False
        saved.result(timeout=3)
    assert len(book) == 1
    assert book.is_echo(second, "two", now)
