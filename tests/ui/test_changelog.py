"""The About dialog ships release notes for both UI languages."""

import pytest

from aconsole.ui import changelog


def test_release_notes_describe_the_update() -> None:
    assert "Граф плана" in changelog.release_notes(lang="ru")
    assert "The plan graph" in changelog.release_notes(lang="en")


@pytest.mark.parametrize("language", ["ru", "en"])
def test_release_notes_follow_the_desktop_language(monkeypatch: pytest.MonkeyPatch, language: str) -> None:
    monkeypatch.setattr(changelog, "language", lambda: language)
    assert changelog.release_notes() == changelog.release_notes(lang=language)
