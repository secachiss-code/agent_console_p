"""MCP server for the twelve plan tools, served on ``/mcp`` beside ingest.

Missing ``Authorization`` is allowed so a protocol client can list tools.
A bearer that is not a live tab token is rejected. A known token selects that tab.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from aconsole.core.infrastructure.http.auth import bearer_token
from aconsole.core.infrastructure.mcp.tools import (
    Added,
    Attached,
    Claimed,
    CtxResult,
    HandoverResult,
    Impact,
    KbAdded,
    NodeIn,
    PlanBoard,
    PlanCreated,
    PlanGot,
    PlanList,
    PlanReady,
    TabContext,
    Updated,
    active,
    bind_tab,
    publish,
)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_MCP = "/mcp"
_PAIR = 2


class _Receive(Protocol):
    async def __call__(self) -> Mapping[str, object]: ...


class _Send(Protocol):
    async def __call__(self, message: Mapping[str, object]) -> None: ...


class _App(Protocol):
    async def __call__(self, scope: Mapping[str, object], receive: _Receive, send: _Send) -> None: ...


class TabTokens:
    """Bearer tokens the daemon already issued for live tabs."""

    def __init__(self) -> None:
        self._by_token: dict[str, TabContext] = {}
        self._by_tab: dict[str, str] = {}

    def register(self, tab_id: str, token: str, context: TabContext) -> None:
        """Remember ``token`` as the bearer for ``tab_id``. A new token replaces the old one."""
        previous = self._by_tab.get(tab_id)
        if previous is not None and previous in self._by_token:
            del self._by_token[previous]
        self._by_token[token] = context
        self._by_tab[tab_id] = token

    def drop(self, tab_id: str) -> None:
        """Forget the tab. An unknown tab is ignored."""
        token = self._by_tab.pop(tab_id, None)
        if token is not None and token in self._by_token:
            del self._by_token[token]

    def resolve(self, header: str | None) -> TabContext | Literal["absent"] | None:
        """The tab for a bearer, ``absent`` when the header was omitted, or None when it is unknown."""
        token = bearer_token(header)
        if token is None:
            return "absent"
        return self._by_token.get(token)


def build_server(board: PlanBoard, context: TabContext) -> MCPServer:
    """An in-process server. ``context`` is the tab used when the request has no bearer."""
    server = MCPServer(name="aconsole", instructions="Plan, claim, and record knowledge for the current tab.")
    _register(server, board, context)
    return server


def http_app(server: MCPServer) -> _App:
    """Streamable HTTP on ``/mcp``, JSON responses, bound to loopback by the caller."""
    return server.streamable_http_app(  # pyright: ignore[reportReturnType] -- Starlette is the ASGI app beside ingest
        streamable_http_path=_MCP,
        json_response=True,
        stateless_http=False,
        host="127.0.0.1",
    )


def beside_ingest(ingest: _App, mcp: _App, tokens: TabTokens) -> _App:
    """Serve MCP at ``/mcp`` and leave every other path on the ingest app."""
    return _Beside(ingest, mcp, tokens)


class _Beside:
    """One ASGI app: lifespan starts the MCP session manager; ``/mcp`` is the tool server."""

    def __init__(self, ingest: _App, mcp: _App, tokens: TabTokens) -> None:
        self._ingest: _App = ingest
        self._mcp: _App = mcp
        self._tokens: TabTokens = tokens

    async def __call__(self, scope: Mapping[str, object], receive: _Receive, send: _Send) -> None:
        if scope.get("type") == "lifespan":
            await self._mcp(scope, receive, send)
            return
        path = scope.get("path")
        if isinstance(path, str) and (path == _MCP or path.startswith(f"{_MCP}/")):
            await self._mcp_request(scope, receive, send)
            return
        await self._ingest(scope, receive, send)

    async def _mcp_request(self, scope: Mapping[str, object], receive: _Receive, send: _Send) -> None:
        header = _authorization(scope.get("headers"))
        resolved = self._tokens.resolve(header)
        if resolved is None:
            await _unauthorized(send)
            return
        if resolved == "absent":
            await self._mcp(scope, receive, send)
            return
        _token, reset = bind_tab(resolved)
        try:
            await self._mcp(scope, receive, send)
        finally:
            reset()


def _register(server: MCPServer, board: PlanBoard, context: TabContext) -> None:  # noqa: C901 -- one nested function per MCP tool
    def plan_create(title: str, goal: str, nodes: list[NodeIn] | None = None) -> PlanCreated:
        """Create a plan for this project. The same title returns the same plan."""
        return board.create(active(context), title, goal, [] if nodes is None else nodes)

    def plan_add_nodes(plan_id: str, nodes: list[NodeIn]) -> Added:
        """Add nodes. An existing key is left unchanged. A cycle adds nothing."""
        return board.add_nodes(active(context), plan_id, nodes)

    def plan_get(plan_id: str, response_format: Literal["concise", "detailed"] = "concise") -> PlanGot:
        """Read one plan. Detailed adds objective, acceptance, paths, and the lease."""
        return board.get(active(context), plan_id, detailed=response_format == "detailed")

    def plan_ready(plan_id: str | None = None, limit: Annotated[int, Field(ge=1, le=20)] = 5) -> PlanReady:
        """Claimable nodes, highest priority first."""
        return board.ready(active(context), plan_id, limit)

    def node_claim(node_id: str) -> Claimed:
        """Take the lease. Returns the fence later calls must send."""
        return board.claim(active(context), node_id)

    def node_update(
        node_id: str,
        fence: int,
        status: str | None = None,
        note: Annotated[str, Field(max_length=2000)] | None = None,
        evidence: Annotated[str, Field(max_length=4000)] | None = None,
    ) -> Updated:
        """Move a node, or send a heartbeat when status is omitted."""
        return board.update(active(context), node_id, fence, status, note, evidence)

    def node_attach(
        node_id: str,
        fence: int,
        kind: Literal["commit", "file", "test_report", "url", "note"],
        ref: str,
        title: str | None = None,
    ) -> Attached:
        """Attach one artifact. The same kind and ref return the same id."""
        return board.attach(active(context), node_id, fence, kind, ref, title)

    def ctx_get(
        node_id: str | None = None,
        query: str | None = None,
        max_items: Annotated[int, Field(ge=1, le=8)] = 6,
        response_format: Literal["concise", "detailed"] = "concise",
    ) -> CtxResult:
        """Knowledge for a node or a query. Summaries stay short."""
        _ = response_format
        return board.ctx(active(context), node_id, query, max_items)

    def kb_add(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        type: Literal["fact", "decision", "requirement"],  # noqa: A002 -- MCP field name is type
        title: str,
        body: Annotated[str, Field(max_length=8000)],
        source: str,
        code_refs: list[str] | None = None,
        supersedes: str | None = None,
    ) -> KbAdded:
        """Record a fact, decision, or requirement. A fact's source must exist."""
        return board.kb_add(
            active(context), type, title, body, source, [] if code_refs is None else code_refs, supersedes
        )

    def kb_impact(
        target: str,
        hops: Annotated[int, Field(ge=1, le=3)] = 3,
        limit: Annotated[int, Field(ge=1, le=30)] = 20,
        kinds: list[str] | None = None,
    ) -> Impact:
        """Neighbors of a file, module, or knowledge id, one list per hop."""
        return board.impact(active(context), target, hops, limit, kinds)

    def handover(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        action: Literal["write", "read"],
        goal: str | None = None,
        done: list[str] | None = None,
        decisions: list[str] | None = None,
        open_questions: list[str] | None = None,
        next_steps: list[str] | None = None,
        failing_checks: list[str] | None = None,
    ) -> HandoverResult:
        """Write this tab's handover, or read the last one plus git diff --stat."""
        return board.handover(
            active(context),
            action,
            goal,
            [] if done is None else done,
            [] if decisions is None else decisions,
            [] if open_questions is None else open_questions,
            [] if next_steps is None else next_steps,
            [] if failing_checks is None else failing_checks,
        )

    def plan_list(status: Literal["active", "all"] = "active") -> PlanList:
        """Plans for this project. Active means something is still open."""
        return board.list_plans(active(context), status)

    specs = (
        (plan_create, "Create a plan", _IDEMPOTENT),
        (plan_add_nodes, "Add plan nodes", _IDEMPOTENT),
        (plan_get, "Read a plan", _READ),
        (plan_ready, "List claimable nodes", _READ),
        (node_claim, "Claim a node", _WRITE),
        (node_update, "Update a node or extend its lease", _WRITE),
        (node_attach, "Attach an artifact", _IDEMPOTENT),
        (ctx_get, "Read knowledge", _READ),
        (kb_add, "Add knowledge", _WRITE),
        (kb_impact, "Impact of a target", _READ),
        (handover, "Write or read a handover", _WRITE),
        (plan_list, "List plans", _READ),
    )
    for fn, description, marks in specs:
        _ = server.tool(name=fn.__name__, description=description, annotations=marks, structured_output=True)(
            publish(fn)
        )


def _authorization(headers: object) -> str | None:
    if not isinstance(headers, list):
        return None
    for item in headers:  # pyright: ignore[reportUnknownVariableType] -- isinstance(list) yields Unknown
        pair = _header_pair(item)  # pyright: ignore[reportUnknownArgumentType] -- isinstance(list) yields Unknown
        if pair is None:
            continue
        name, value = pair
        if name.lower() == b"authorization":
            return value.decode("latin-1")
    return None


def _header_pair(item: object) -> tuple[bytes, bytes] | None:
    if not isinstance(item, tuple) or len(item) != _PAIR:  # pyright: ignore[reportUnknownArgumentType] -- tuple items are Unknown
        return None
    name: object = item[0]  # pyright: ignore[reportUnknownVariableType] -- tuple items are Unknown
    value: object = item[1]  # pyright: ignore[reportUnknownVariableType] -- tuple items are Unknown
    if not isinstance(name, (bytes, bytearray)) or not isinstance(value, (bytes, bytearray)):
        return None
    return bytes(name), bytes(value)


async def _unauthorized(send: _Send) -> None:
    body = b'{"reason":"INGEST_UNAUTHORIZED"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body})
