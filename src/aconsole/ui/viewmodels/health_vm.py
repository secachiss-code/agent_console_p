"""What the health dialog shows: the core's counters and how this client's connection has fared."""

from dataclasses import dataclass

from aconsole.contracts.app import EmptyParams, HealthResult
from aconsole.ui.client.core_client import CoreCallError, CoreClient, CoreTimeoutError


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthFacts:
    """``core`` is None when the core did not answer, which is itself the finding."""

    core: HealthResult | None
    resyncs: int
    reconnects: int


class HealthViewModel:
    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client

    async def load(self) -> HealthFacts:
        try:
            core = await self._client.request("app/health", EmptyParams(), HealthResult)
        except ConnectionError, CoreCallError, CoreTimeoutError, OSError:
            core = None
        return HealthFacts(
            core=core,
            resyncs=self._client.resync_count,
            reconnects=max(0, self._client.connects - 1),
        )
