"""The about dialog can read the release notes resource."""

from aconsole.ui.changelog import release_notes


def test_release_notes_describe_the_update() -> None:
    notes = release_notes()
    assert "MCP" in notes
    assert "Версия и изменения" in notes
