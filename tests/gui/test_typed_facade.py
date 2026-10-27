"""TypedFacade: generated file freshness, METHODS coverage, method names, subscriptions, result checks."""

import asyncio
import inspect
import typing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, final, override

import msgspec
import pytest
from tools.check_contracts import check_typed_facade

from aconsole.contracts.app import TabIdParams
from aconsole.contracts.common import TabId
from aconsole.contracts.events import TabRemoved
from aconsole.contracts.ipc import EventEnvelope
from aconsole.contracts.methods import METHODS
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.generate import TARGET, method_name, render
from aconsole.ui.client.methods import BINDINGS, Facade
from aconsole.ui.client.typed_facade import TypedFacade

if TYPE_CHECKING:
    from collections.abc import Callable

    from aconsole.ui.client.store import CoreStore

REPO = Path(__file__).resolve().parents[2]


class _EmptyStub(msgspec.Struct, frozen=True, kw_only=True):
    """A result type no typed method returns, to simulate a wrong core answer."""


@final
class _StubClient(CoreClient):
    """A CoreClient that never touches a socket: `request` returns a canned value and records the method."""

    def __init__(self, answer: msgspec.Struct) -> None:
        super().__init__("unused.sock")
        self._answer = answer
        self.calls: list[str] = []

    @override
    async def request[T: msgspec.Struct](
        self, method: str, params: msgspec.Struct, result_type: type[T], *, long: bool = False
    ) -> T:
        _ = (params, result_type, long)
        self.calls.append(method)
        return self._answer  # pyright: ignore[reportReturnType]  -- the wrong type is the point of the stub


def _envelope(seq: int) -> EventEnvelope:
    return EventEnvelope(seq=seq, at=datetime.now(UTC), event=TabRemoved(tab_id=TabId("tab-1")))


def _tab_params() -> TabIdParams:
    return TabIdParams(tab_id=TabId("tab-1"))


def test_render_matches_typed_facade_on_disk() -> None:
    assert TARGET.read_text(encoding="utf-8") == render()


def test_every_method_row_has_a_typed_method_with_matching_annotations() -> None:
    public = [name for name in vars(TypedFacade) if not name.startswith("_")]
    assert len(public) == len(METHODS)
    for row in METHODS:
        name = method_name(row.name)
        assert name in vars(TypedFacade), f"missing TypedFacade.{name} for {row.name}"
        method: Callable[..., object] = getattr(TypedFacade, name)  # pyright: ignore[reportAny] -- getattr on a class is typed Any; the name is checked just above
        hints = typing.get_type_hints(method)
        binding = BINDINGS[row.name]
        assert hints["params"] is binding.params, row.name
        assert hints["return"] is binding.result, row.name
        assert inspect.get_annotations(method, eval_str=True)["return"] is binding.result, row.name


def test_method_name_converts_rpc_names() -> None:
    assert method_name("tab/launchSpec") == "tab_launch_spec"
    assert method_name("app/snapshot") == "app_snapshot"
    assert method_name("shutdown") == "shutdown"
    assert not any("$" in row.name for row in METHODS)


def test_subscribe_handler_runs_after_apply_and_stops_on_close() -> None:
    client = _StubClient(_EmptyStub())
    facade = Facade(client)
    seen: list[int] = []

    def handler(_event: object, store: CoreStore) -> None:
        seen.append(store.seq)

    subscription = facade.subscribe(handler)
    assert subscription.active is True

    assert client.store.apply(_envelope(1)) is True
    assert seen == [1]

    subscription.close()
    assert subscription.active is False
    assert client.store.apply(_envelope(2)) is True
    assert seen == [1]

    subscription.close()
    assert subscription.active is False
    assert client.store.apply(_envelope(3)) is True
    assert seen == [1]


def test_typed_raises_type_error_on_wrong_result_type() -> None:
    client = _StubClient(_EmptyStub())
    facade = Facade(client)
    result_type = BINDINGS["tab/launchSpec"].result
    with pytest.raises(TypeError, match="tab/launchSpec returned _EmptyStub, not TerminalLaunchSpec"):
        _ = asyncio.run(facade.typed("tab/launchSpec", _tab_params(), result_type))
    assert client.calls == ["tab/launchSpec"]


def test_typed_facade_method_raises_on_wrong_result_type() -> None:
    facade = TypedFacade(_StubClient(_EmptyStub()))
    with pytest.raises(TypeError):
        _ = asyncio.run(facade.tab_launch_spec(_tab_params()))


def test_check_typed_facade_passes_on_repo() -> None:
    assert check_typed_facade(REPO) == []


def test_check_typed_facade_fails_on_stale_file(tmp_path: Path) -> None:
    stale = tmp_path / "src/aconsole/ui/client/typed_facade.py"
    stale.parent.mkdir(parents=True)
    _ = stale.write_text(render() + "# stale\n", encoding="utf-8")
    problems = check_typed_facade(tmp_path)
    assert len(problems) == 1
    assert "typed facade out of date" in problems[0]
