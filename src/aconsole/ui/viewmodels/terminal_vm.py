"""Core requests for a terminal's lifecycle."""

import asyncio

from aconsole.contracts.app import EmptyResult, ExitedParams, StartedParams, TabIdParams, TerminalLaunchSpec
from aconsole.contracts.common import TabId
from aconsole.ui.client.core_client import CoreClient


class TerminalViewModel:
    """Send lifecycle requests without owning the terminal or its process."""

    def __init__(self, client: CoreClient, tab_id: TabId) -> None:
        self._client: CoreClient = client
        self._tab_id: TabId = tab_id

    async def launch_spec(self) -> TerminalLaunchSpec:
        return await self._client.request("tab/launchSpec", TabIdParams(tab_id=self._tab_id), TerminalLaunchSpec)

    async def report_started(self, pid: int) -> None:
        _ = await self._client.request("tab/started", StartedParams(tab_id=self._tab_id, pid=pid), EmptyResult)

    async def report_exited(self, wait_status: int) -> None:
        _ = await self._client.request(
            "tab/exited", ExitedParams(tab_id=self._tab_id, wait_status=wait_status), EmptyResult,
        )

    async def report_lifecycle(
        self,
        pid: int,
        exit_status: asyncio.Future[int],
        *,
        started_report_result: asyncio.Future[Exception | None] | None = None,
    ) -> tuple[Exception | None, Exception | None]:
        """Report a spawned child's lifecycle in order, retaining transport failures."""
        started_error: Exception | None = None
        exited_error: Exception | None = None
        try:
            await self.report_started(pid)
        except asyncio.CancelledError:
            if started_report_result is not None and not started_report_result.done():
                _ = started_report_result.cancel()
            raise
        except Exception as error:  # noqa: BLE001 -- retain the error and still report a real exit
            started_error = error
        if started_report_result is not None and not started_report_result.done():
            started_report_result.set_result(started_error)
        status = await asyncio.shield(exit_status)
        try:
            await self.report_exited(status)
        except Exception as error:  # noqa: BLE001 -- allow the caller to surface delivery failures
            exited_error = error
        return started_error, exited_error
