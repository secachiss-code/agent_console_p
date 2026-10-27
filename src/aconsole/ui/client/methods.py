"""Typed calls generated from ``METHODS``. ``Facade.call`` is the invoker."""

from collections.abc import Callable
from dataclasses import dataclass

import msgspec

from aconsole.contracts.app import (
    AppliedResult,
    ApplyFixParams,
    AppSnapshot,
    CheckpointIdParams,
    CheckpointListParams,
    CheckpointListResult,
    CheckResult,
    ChecksResult,
    CloseTabParams,
    CloseTabResult,
    CommandResult,
    DecideParams,
    DiffFileParams,
    DiffFileResult,
    DiffSummaryParams,
    DiffSummaryResult,
    EmptyParams,
    EmptyResult,
    ExitedParams,
    FixIdParams,
    FixPreview,
    FocusParams,
    ImpactParams,
    ImpactView,
    NoteUriParams,
    NoteUriResult,
    OpenTabParams,
    PaletteExecuteParams,
    PaletteItemsResult,
    PaletteQuery,
    PlanIdParams,
    PlanListParams,
    PlanListResult,
    PlanNodeView,
    PlanView,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
    QuitCheckResult,
    ReleasedResult,
    ReleaseParams,
    RenameParams,
    ReorderParams,
    ResetRankingParams,
    RestartParams,
    RestoreParams,
    RestorePreviewResult,
    RestoreResult,
    ScreenTextReplyParams,
    SendCommentParams,
    SessionListParams,
    SessionListResult,
    SetNodeStatusParams,
    SettingsUpdateParams,
    SnapshotResult,
    StartedParams,
    SubscribeParams,
    SubscribeResult,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.events import UiEvent
from aconsole.contracts.ipc import InitializeParams, InitializeResult
from aconsole.contracts.methods import METHODS, method_by_name
from aconsole.contracts.settings import Settings
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.store import CoreStore, Subscription


@dataclass(frozen=True, kw_only=True)
class Binding:
    name: str
    params: type[msgspec.Struct]
    result: type[msgspec.Struct]


def _bindings() -> dict[str, Binding]:
    rows: tuple[tuple[str, type[msgspec.Struct], type[msgspec.Struct]], ...] = (
        ("initialize", InitializeParams, InitializeResult),
        ("shutdown", EmptyParams, EmptyResult),
        ("app/snapshot", EmptyParams, SnapshotResult),
        ("events/subscribe", SubscribeParams, SubscribeResult),
        ("events/unsubscribe", EmptyParams, EmptyResult),
        ("project/add", ProjectAddParams, ProjectView),
        ("project/remove", ProjectIdParams, EmptyResult),
        ("project/setActive", ProjectIdParams, EmptyResult),
        ("tab/open", OpenTabParams, TabView),
        ("tab/launchSpec", TabIdParams, TerminalLaunchSpec),
        ("tab/started", StartedParams, EmptyResult),
        ("tab/exited", ExitedParams, EmptyResult),
        ("tab/close", CloseTabParams, CloseTabResult),
        ("tab/restart", RestartParams, TabView),
        ("tab/fork", TabIdParams, TabView),
        ("tab/rename", RenameParams, EmptyResult),
        ("tab/reorder", ReorderParams, EmptyResult),
        ("tab/focus", FocusParams, EmptyResult),
        ("approval/decide", DecideParams, AppliedResult),
        ("approval/releaseToNative", ReleaseParams, ReleasedResult),
        ("palette/items", PaletteQuery, PaletteItemsResult),
        ("palette/execute", PaletteExecuteParams, CommandResult),
        ("palette/resetRanking", ResetRankingParams, EmptyResult),
        ("session/list", SessionListParams, SessionListResult),
        ("plan/list", PlanListParams, PlanListResult),
        ("plan/get", PlanIdParams, PlanView),
        ("plan/setNodeStatus", SetNodeStatusParams, PlanNodeView),
        ("knowledge/impact", ImpactParams, ImpactView),
        ("knowledge/noteUri", NoteUriParams, NoteUriResult),
        ("checkpoint/list", CheckpointListParams, CheckpointListResult),
        ("checkpoint/restorePreview", CheckpointIdParams, RestorePreviewResult),
        ("checkpoint/restore", RestoreParams, RestoreResult),
        ("diff/summary", DiffSummaryParams, DiffSummaryResult),
        ("diff/file", DiffFileParams, DiffFileResult),
        ("diff/sendComment", SendCommentParams, CommandResult),
        ("setup/checks", EmptyParams, ChecksResult),
        ("setup/previewFix", FixIdParams, FixPreview),
        ("setup/applyFix", ApplyFixParams, CheckResult),
        ("settings/get", EmptyParams, Settings),
        ("settings/update", SettingsUpdateParams, Settings),
        ("app/quitCheck", EmptyParams, QuitCheckResult),
        ("app/stopAgents", EmptyParams, EmptyResult),
        ("terminal/screenTextReply", ScreenTextReplyParams, EmptyResult),
    )
    return {name: Binding(name=name, params=params, result=result) for name, params, result in rows}


BINDINGS = _bindings()


def binding_names() -> frozenset[str]:
    return frozenset(BINDINGS)


class Facade:
    """The UI-facing methods. Every ``METHODS`` row is a ``Binding``."""

    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client

    def subscribe(self, handler: Callable[[UiEvent, CoreStore], None]) -> Subscription:
        """APP_API §9. `handler(event, store)` runs after the store applied the event."""
        return self._client.store.listen(handler)

    async def typed[T: msgspec.Struct](self, name: str, params: msgspec.Struct, result: type[T]) -> T:
        """`call` with the result type checked. The generated methods of `TypedFacade` use this."""
        found = await self.call(name, params)
        if not isinstance(found, result):
            message = f"{name} returned {type(found).__name__}, not {result.__name__}"
            raise TypeError(message)
        return found

    async def call(self, name: str, params: msgspec.Struct) -> msgspec.Struct:
        binding = BINDINGS[name]
        spec = method_by_name(name)
        if not isinstance(params, binding.params):
            message = f"{name} params are not {binding.params.__name__}"
            raise TypeError(message)
        return await self._client.request(name, params, binding.result, long=spec.long)

    async def snapshot(self) -> tuple[AppSnapshot, int]:
        found = await self.call("app/snapshot", EmptyParams())
        if not isinstance(found, SnapshotResult):
            message = "app/snapshot returned the wrong result"
            raise TypeError(message)
        return found.snapshot, found.seq

    async def open_tab(self, params: OpenTabParams) -> TabView:
        found = await self.call("tab/open", params)
        if not isinstance(found, TabView):
            message = "tab/open returned the wrong result"
            raise TypeError(message)
        return found

    async def launch_spec(self, params: TabIdParams) -> TerminalLaunchSpec:
        found = await self.call("tab/launchSpec", params)
        if not isinstance(found, TerminalLaunchSpec):
            message = "tab/launchSpec returned the wrong result"
            raise TypeError(message)
        return found


def methods_cover_the_table() -> bool:
    return binding_names() == frozenset(method.name for method in METHODS)
