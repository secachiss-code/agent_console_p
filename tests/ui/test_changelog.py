"""The About dialog ships release notes for both UI languages."""

from aconsole.ui.changelog import release_notes


def test_release_notes_describe_the_update() -> None:
    assert "Граф плана" in release_notes(lang="ru")
    assert "The plan graph" in release_notes(lang="en")
