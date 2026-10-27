"""Merge quota observations from every tab of one account."""

from aconsole.core.domain.quota import Observation, QuotaBoard, QuotaKey


class QuotaAggregator:
    _board: QuotaBoard

    def __init__(self) -> None:
        self._board = QuotaBoard()

    def observe(self, observation: Observation) -> Observation:
        return self._board.observe(observation)

    def current(self, account_id: str, key: str) -> Observation | None:
        return self._board.get(account_id, QuotaKey(key))
