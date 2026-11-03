"""What a person sees before deciding must be what the agent asked for."""

from datetime import UTC, datetime, timedelta

from aconsole.ui.viewmodels.approvals_vm import safe_text, time_left

_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_escapes_and_direction_overrides_are_shown_as_code_points() -> None:
    assert safe_text("ls\x1b[2K") == "ls[U+001B][2K"
    override = chr(0x202E)
    assert safe_text(f"rm {override}gnp.tac") == "rm [U+202E]gnp.tac"
    assert safe_text("a\n\tb") == "a\n\tb"
    assert safe_text("Изменить файл") == "Изменить файл"


def test_a_cut_says_how_much_is_gone() -> None:
    assert safe_text("x" * 12, limit=10) == f"{'x' * 10}… (+2)"
    assert safe_text("x" * 10, limit=10) == "x" * 10


def test_time_left() -> None:
    assert time_left(_NOW + timedelta(seconds=125), _NOW) == "2:05"
    assert time_left(_NOW, _NOW) is None
    assert time_left(_NOW - timedelta(seconds=5), _NOW) is None
