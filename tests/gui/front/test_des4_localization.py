"""Core presentation codes are translated at the final UI boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import CommandResult
from aconsole.contracts.messages import UiMessage
from aconsole.ui import i18n
from aconsole.ui.viewmodels.palette_vm import _localized
from tests.gui.front.test_b7_palette import _item
from tests.gui.front.test_review_page import _on_page

if TYPE_CHECKING:
    from aconsole.ui.views.review.page import ReviewPage


@pytest.mark.parametrize(("language", "expected"), [
    ("en_US", "Start the agent in this tab first"),
    ("ru_RU", "Сначала запустите агента во вкладке"),
])
def test_refused_comment_is_localized_and_keeps_draft(
    running_app: str, monkeypatch: pytest.MonkeyPatch, language: str, expected: str,
) -> None:
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (language, "UTF-8"))

    async def check(page: ReviewPage, toasts: list[str]) -> None:
        async def comment(_path: str, _hunk: str, _text: str) -> CommandResult:
            return CommandResult(ok=False, toast=UiMessage.AGENT_NOT_STARTED)

        monkeypatch.setattr(page.viewmodel, "comment", comment)
        await page.send_comment("file", "hunk", "user draft")
        assert toasts == [expected]
        assert page._comment_drafts == {("file", "hunk"): "user draft"}

    _on_page(running_app, check)


@pytest.mark.parametrize(("language", "prefix"), [("en_US", "Open plan: "), ("ru_RU", "Открыть план: ")])
def test_palette_caption_preserves_user_title_and_identity(
    monkeypatch: pytest.MonkeyPatch, language: str, prefix: str,
) -> None:
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (language, "UTF-8"))
    title = "User {untrusted} title"
    item = _item("command:plan:p1", title)
    localized = _localized(item)
    assert localized.title == prefix + title
    assert localized.id == item.id
    assert item.title == title
    ordinary = msgspec.structs.replace(item, id="tab:user")
    assert _localized(ordinary) is ordinary
