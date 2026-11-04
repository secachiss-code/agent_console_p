"""When a request for a decision reaches the system's notifications. No GTK here.

An episode is the time a tab has at least one open request. It gets one notification at most: more
requests in the same episode do not ring again. A person who comes back to the window sees the card,
so the notification goes away and the episode does not ring again either.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aconsole.contracts.app import ApprovalView
from aconsole.contracts.common import TabId
from aconsole.ui.viewmodels.approvals_vm import safe_text

_TITLE_LIMIT = 80


@dataclass(frozen=True, slots=True, kw_only=True)
class Alert:
    id: str
    tab_id: TabId
    tab_title: str
    view: ApprovalView


@dataclass(slots=True, kw_only=True)
class _Episode:
    id: str
    rang: bool = False
    showing: bool = False


class ApprovalAlerts:
    def __init__(self, post: Callable[[Alert], None], withdraw: Callable[[str], None]) -> None:
        self._post = post
        self._withdraw = withdraw
        self._episodes: dict[TabId, _Episode] = {}
        self._count = 0

    def update(self, approvals: tuple[ApprovalView, ...], titles: Mapping[str, str], *, window_active: bool) -> None:
        first: dict[TabId, ApprovalView] = {}
        for view in approvals:
            _ = first.setdefault(view.tab_id, view)
        for tab_id in [tab_id for tab_id in self._episodes if tab_id not in first]:
            self._hide(self._episodes.pop(tab_id))
        for tab_id, view in first.items():
            episode = self._episodes.get(tab_id) or self._begin(tab_id)
            if window_active:
                self._hide(episode)
                continue
            if not episode.rang:
                episode.rang = True
                episode.showing = True
                title = safe_text(titles.get(str(tab_id), ""), _TITLE_LIMIT)
                self._post(Alert(id=episode.id, tab_id=tab_id, tab_title=title, view=view))

    def clear(self) -> None:
        """The window is going away, and a notification would lead nowhere."""
        for episode in self._episodes.values():
            self._hide(episode)
        self._episodes.clear()

    def _begin(self, tab_id: TabId) -> _Episode:
        self._count += 1
        episode = _Episode(id=f"approval-{tab_id}-{self._count}")
        self._episodes[tab_id] = episode
        return episode

    def _hide(self, episode: _Episode) -> None:
        if episode.showing:
            episode.showing = False
            self._withdraw(episode.id)
