"""Notification port. The UI adapter shows the message."""

from typing import Protocol


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> None: ...


class RecordingNotifier:
    """Keeps the messages a test sent."""

    messages: list[tuple[str, str]]

    def __init__(self) -> None:
        self.messages = []

    def notify(self, title: str, body: str) -> None:
        self.messages.append((title, body))
