"""MCP tools served on ``/mcp`` beside ingest.

Missing ``Authorization`` is allowed so a protocol client can list tools.
A bearer that is not a live tab token is rejected. A known token selects that tab.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Literal, Protocol, override

import msgspec
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool, ToolAnnotations
from pydantic import BaseModel, Field

from aconsole import MCP_VERSION
from aconsole.contracts.common import NodeId, PlanId, ProjectId, TabId
from aconsole.contracts.mcp_catalog import TOOL_INFO
from aconsole.contracts.primitives import (
    GateRunParams,
    HandoverReadParams,
    HandoverWriteParams,
    KnowledgeAddParams,
    KnowledgeGetParams,
    NodeAttachParams,
    NodeClaimParams,
    NodeEscalateParams,
    NodeStepParams,
    NodeUpdateParams,
    PlanReadyParams,
)
from aconsole.core.application.services.plan_graph import (
    PlanEdgeKind,
    PlanGraphQuery,
    PlanGraphView,
    PlanNodeDetails,
    PlanSection,
)
from aconsole.core.infrastructure.codegraph.project_lint import LintResult
from aconsole.core.infrastructure.codegraph.project_search import SearchDirection, SearchIntent, SearchKind, SourceRef
from aconsole.core.infrastructure.http.auth import bearer_token, host_allowed, origin_allowed
from aconsole.core.infrastructure.knowledge import run_primitive
from aconsole.core.infrastructure.mcp.graph_pages import CURSOR_PATTERN
from aconsole.core.infrastructure.mcp.project_index import (
    AnalyzerDiagnostics,
    AnalyzerStatuses,
    IndexPage,
    IndexRead,
    LintScope,
    PageRequest,
    ProjectIndex,
    TaskContext,
)
from aconsole.core.infrastructure.mcp.skills import register_skills
from aconsole.core.infrastructure.mcp.tools import (
    Added,
    Attached,
    Claimed,
    CtxResult,
    HandoverResult,
    Impact,
    KbAdded,
    NodeIn,
    PipelineReply,
    PlanBoard,
    PlanCreated,
    PlanDeleted,
    PlanGot,
    PlanList,
    PlanReady,
    PlanSaved,
    TabContext,
    Updated,
    active,
    bind_tab,
    publish,
)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
_MCP = "/mcp"
_PAIR = 2
_REQUEST_BYTES = 1024 * 1024
_SESSION_LIMIT = 2048
type _Id = Annotated[str, Field(max_length=128)]
type _Text = Annotated[str, Field(max_length=2000)]
type _Items = Annotated[list[_Text], Field(max_length=100)]
type _GraphCursor = Annotated[str, Field(pattern=CURSOR_PATTERN)]
type _GraphLimit = Annotated[int, Field(ge=1, le=50)]
type _IndexPath = Annotated[str, Field(min_length=1, max_length=4096)]
type _IndexDepth = Annotated[int, Field(ge=1, le=3)]


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

    def context(self, tab_id: str) -> TabContext | None:
        token = self._by_tab.get(tab_id)
        return None if token is None else self._by_token.get(token)

    def resolve(self, header: str | None) -> TabContext | Literal["absent"] | None:
        """The tab for a bearer, ``absent`` when no header was sent. None for a malformed or unknown one."""
        if header is None:
            return "absent"
        token = bearer_token(header)
        return None if token is None else self._by_token.get(token)


class _LeanServer(MCPServer):
    """Lists the tools with argument schemas an agent needs and nothing else.

    The tool list is read into the agent's context at the start of every session. Pydantic's generated titles
    ("Depends On" for ``depends_on``), ``null`` alternatives and ``null`` defaults say nothing an agent can use.
    """

    def __init__(self, context: TabContext) -> None:
        super().__init__(
            name="aconsole",
            version=MCP_VERSION,
            instructions=(
                "Plan: plan_ready, node_claim, one step. Code: index_find, index_read. "
                "Workflow skills at aconsole://skills/."
            ),
        )
        self.tab_context: TabContext = context

    @override
    async def list_tools(self) -> list[Tool]:
        listed = await super().list_tools()
        mode = active(self.tab_context).mode
        return [
            tool.model_copy(update={"input_schema": lean_schema(tool.input_schema)})
            for tool in listed
            if mode is None or TOOL_INFO[tool.name].mode in {None, mode}
        ]


def lean_schema(schema: dict[str, object]) -> dict[str, object]:
    """``schema`` without generated titles, ``null`` alternatives of optional fields, or ``null`` defaults."""
    lean: dict[str, object] = {}
    for key, value in schema.items():
        if (key == "title" and isinstance(value, str)) or (key == "default" and value is None):
            continue
        if key in {"properties", "$defs"} and isinstance(value, dict):
            lean[key] = {name: _lean_any(item) for name, item in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] -- JSON schema
        elif key in {"anyOf", "oneOf"} and isinstance(value, list):
            kept = [_lean_any(item) for item in value if item != {"type": "null"}]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] -- JSON schema
            if len(kept) == 1 and isinstance(kept[0], dict):
                lean.update(kept[0])  # pyright: ignore[reportUnknownArgumentType]
            else:
                lean[key] = kept
        else:
            lean[key] = _lean_any(value)
    return lean


def _lean_any(value: object) -> object:
    if isinstance(value, dict):
        return lean_schema(value)  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, list):
        return [_lean_any(item) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] -- JSON schema
    return value


def build_server(
    board: PlanBoard, context: TabContext, sent: Callable[[str, int], None] | None = None,
    *, project_index: ProjectIndex | None = None,
) -> MCPServer:
    """An in-process server. ``context`` serves a request with no bearer: it can list tools and call none.

    ``sent`` hears, per tab, the bytes of text each answer puts in that tab's agent's context.
    """
    server = _LeanServer(context)
    _register(server, board, context, sent, ProjectIndex(board) if project_index is None else project_index)
    register_skills(server)
    return server


def http_app(server: MCPServer) -> _App:
    """Streamable HTTP on ``/mcp``, JSON responses, bound to loopback by the caller."""
    return server.streamable_http_app(  # pyright: ignore[reportReturnType] -- Starlette is the ASGI app beside ingest
        streamable_http_path=_MCP,
        json_response=True,
        stateless_http=False,
        host="127.0.0.1",
    )


def beside_ingest(ingest: _App, mcp: _App, tokens: TabTokens, api: _App | None = None) -> _App:
    """Serve MCP at ``/mcp``, the method API at ``/api``, and leave every other path on ingest."""
    return _Beside(ingest, mcp, tokens, api)


class _Beside:
    """One ASGI app: lifespan starts the MCP session manager; ``/mcp`` is the tool server."""

    def __init__(self, ingest: _App, mcp: _App, tokens: TabTokens, api: _App | None = None) -> None:
        self._ingest: _App = ingest
        self._mcp: _App = mcp
        self._api: _App | None = api
        self._tokens: TabTokens = tokens
        self._sessions: dict[str, str] = {}

    async def __call__(self, scope: Mapping[str, object], receive: _Receive, send: _Send) -> None:
        if scope.get("type") == "lifespan":
            await self._mcp(scope, receive, send)
            return
        path = scope.get("path")
        if isinstance(path, str) and (path == _MCP or path.startswith(f"{_MCP}/")):
            await self._mcp_request(scope, receive, send)
            return
        if self._api is not None and isinstance(path, str) and (path == "/api" or path.startswith("/api/")):
            await self._api(scope, receive, send)
            return
        await self._ingest(scope, receive, send)

    async def _mcp_request(self, scope: Mapping[str, object], receive: _Receive, send: _Send) -> None:
        if not _local_headers(scope.get("headers")):
            await _forbidden(send)
            return
        header = _authorization(scope.get("headers"))
        resolved = self._tokens.resolve(header)
        if resolved is None:
            await _unauthorized(send)
            return
        owner = "absent" if resolved == "absent" else resolved.tab_id
        session = _session_header(scope.get("headers"))
        if session is not None and self._sessions.get(session) != owner:
            await _forbidden(send)
            return

        async def bound_send(message: Mapping[str, object]) -> None:
            issued = _session_header(message.get("headers"))
            if issued is not None:
                if issued not in self._sessions and len(self._sessions) >= _SESSION_LIMIT:
                    del self._sessions[next(iter(self._sessions))]
                self._sessions[issued] = owner
            await send(message)

        bounded = await _bounded_request(scope, receive, send)
        if bounded is None:
            return
        if resolved == "absent":
            await self._mcp(scope, bounded, bound_send)
            return
        _token, reset = bind_tab(resolved)
        try:
            await self._mcp(scope, bounded, bound_send)
        finally:
            reset()


def _session_header(headers: object) -> str | None:
    """A transport session is pinned to the tab that initialized it, never another bearer."""
    if not isinstance(headers, list):
        return None
    for item in headers:  # pyright: ignore[reportUnknownVariableType] -- raw ASGI headers
        pair = _header_pair(item)  # pyright: ignore[reportUnknownArgumentType] -- validated ASGI pair
        if pair is not None and pair[0].lower() == b"mcp-session-id":
            return pair[1].decode("latin-1")
    return None


async def _bounded_request(scope: Mapping[str, object], receive: _Receive, send: _Send) -> _Receive | None:
    """Reject large POST bodies before the MCP transport parses JSON, including chunked requests."""
    if scope.get("method") != "POST":
        return receive
    body = bytearray()
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        if isinstance(chunk, bytes):
            if len(body) + len(chunk) > _REQUEST_BYTES:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"reason":"REQUEST_TOO_LARGE"}'})
                return None
            body.extend(chunk)
        if not message.get("more_body", False):
            break
    pending = True

    async def replay() -> Mapping[str, object]:
        nonlocal pending
        if pending:
            pending = False
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await receive()

    return replay


def _primitive[T: BaseModel](
    board: PlanBoard, ctx: TabContext, method: str, params: msgspec.Struct, kind: type[T]
) -> T:
    """One REST primitive, answered with the tab that owns this MCP session."""
    found = run_primitive(board, method, msgspec.json.encode(params), ctx, translate=False)
    if not isinstance(found, kind):
        message = f"{method} returned {type(found).__name__}"
        raise TypeError(message)
    return found


def _register(  # noqa: C901, PLR0915 -- one nested function per MCP tool
    server: MCPServer, board: PlanBoard, context: TabContext, sent: Callable[[str, int], None] | None,
    index: ProjectIndex,
) -> None:
    def plan_create(
        title: Annotated[str, Field(max_length=120)],
        goal: Annotated[str, Field(max_length=8000)],
        nodes: Annotated[list[NodeIn], Field(max_length=100)] | None = None,
    ) -> PlanCreated:
        """Create a plan for this project. The same title returns the same plan."""
        return board.create(active(context), title, goal, [] if nodes is None else nodes)

    def plan_add_nodes(plan_id: _Id, nodes: Annotated[list[NodeIn], Field(max_length=100)]) -> Added:
        """Add nodes. An existing key is left unchanged. A cycle adds nothing."""
        return board.add_nodes(active(context), plan_id, nodes)

    def plan_save(  # noqa: PLR0913, PLR0917 -- explicit document and repair arguments
        title: Annotated[str, Field(min_length=1, max_length=120)],
        goal: Annotated[str, Field(max_length=8000)] = "",
        plan_id: _Id | None = None,
        nodes: Annotated[list[NodeIn], Field(max_length=100)] | None = None,
        repair_gate_profiles: dict[str, dict[str, object]] | None = None,
        repair_paths: dict[str, list[str]] | None = None,
        repair_contracts: dict[str, str] | None = None,
        repair_note: Annotated[str, Field(max_length=2000)] | None = None,
    ) -> PlanSaved:
        """Create or replace this project's draft in one call. The same title returns the same plan.

        Omit nodes to change only the title and goal. A nodes list replaces the whole draft set.
        For an invalid legacy frozen plan, pass plan_id and repair_gate_profiles keyed by L0 key,
        with unchanged title/goal and omitted nodes. Requires no leases or pipeline attempts.
        repair_paths revises open frozen scopes with matching repair_contracts and a repair_note;
        document identity stays unchanged, leases must be absent, and old attempts remain historical.
        """
        return board.save_for_tab(
            active(context),
            title,
            goal,
            plan_id,
            nodes,
            repair_profiles=repair_gate_profiles,
            repair_paths=None if repair_paths is None else {key: tuple(paths) for key, paths in repair_paths.items()},
            repair_contracts=repair_contracts,
            repair_note=repair_note,
        )

    def plan_delete(plan_id: _Id) -> PlanDeleted:
        """Delete one plan of this project. A node someone is working keeps the plan."""
        return board.delete_for_tab(active(context), plan_id)

    def plan_get(
        plan_id: _Id, response_format: Literal["concise", "detailed"] = "concise", since: _Id | None = None
    ) -> PlanGot:
        """Read phase, node pipeline mode and contract hashes.

        Detailed adds dependencies, objective, acceptance, paths, and the lease.

        Pass the revision of your last read as since to get only the nodes that changed (changed_only is true).
        """
        return board.get(active(context), plan_id, detailed=response_format == "detailed", since=since)

    def plan_ready(plan_id: _Id | None = None, limit: Annotated[int, Field(ge=1, le=20)] = 5) -> PlanReady:
        """Claimable nodes, highest priority first. Pipeline nodes require a frozen contract."""
        ctx = active(context)
        return _primitive(board, ctx, "plan/ready", PlanReadyParams(
            project_id=ProjectId(ctx.project_id),
            plan_id=None if plan_id is None else PlanId(plan_id),
            limit=limit,
        ), PlanReady)

    def plan_graph(  # noqa: PLR0913, PLR0917 -- public MCP signature
        plan_id: _Id,
        mode: Literal["overview", "local"] = "overview",
        anchor: _Id | None = None,
        depth: Annotated[int, Field(ge=1, le=3)] = 1,
        edge_kinds: Annotated[tuple[PlanEdgeKind, ...], Field(max_length=3)] = (),
        limit: _GraphLimit = 12,
        cursor: _GraphCursor | None = None,
    ) -> PlanGraphView:
        query = PlanGraphQuery(
            plan_id=plan_id, mode=mode, anchor=anchor, depth=depth, edge_kinds=edge_kinds, limit=limit, cursor=cursor,
        )
        return board.graph(active(context), query)

    def plan_node_get(
        node_id: _Id,
        sections: Annotated[tuple[PlanSection, ...], Field(max_length=5)] = (),
        limit: _GraphLimit = 12,
        cursor: _GraphCursor | None = None,
    ) -> PlanNodeDetails:
        return board.node_details(active(context), node_id, sections, limit, cursor)

    def index_overview(
        scope: _IndexPath = ".", depth: Annotated[int, Field(ge=0, le=3)] = 1,
        limit: _GraphLimit = 12, cursor: _GraphCursor | None = None,
    ) -> IndexPage:
        page = PageRequest(method="index_overview", params=(scope, depth), limit=limit, cursor=cursor)
        return index.overview(active(context), page, scope, depth)

    def index_find(  # noqa: PLR0913, PLR0917 -- public MCP signature
        query: Annotated[str, Field(min_length=1, max_length=300)],
        intent: SearchIntent = "understand",
        scope: _IndexPath = ".",
        kinds: Annotated[tuple[SearchKind, ...], Field(max_length=5)] = (),
        limit: _GraphLimit = 12,
        cursor: _GraphCursor | None = None,
    ) -> IndexPage:
        page = PageRequest(method="index_find", params=(query, intent, scope, kinds), limit=limit, cursor=cursor)
        return index.find(active(context), page, query=query, scope=scope, intent=intent, kinds=frozenset(kinds))

    def index_neighbors(  # noqa: PLR0913, PLR0917 -- public MCP signature
        targets: Annotated[tuple[_IndexPath, ...], Field(min_length=1, max_length=8)],
        direction: SearchDirection = "both",
        edge_kinds: Annotated[tuple[_Id, ...], Field(max_length=10)] = (),
        depth: _IndexDepth = 1,
        limit: _GraphLimit = 12,
        cursor: _GraphCursor | None = None,
    ) -> IndexPage:
        page = PageRequest(
            method="index_neighbors", params=(targets, direction, edge_kinds, depth), limit=limit, cursor=cursor,
        )
        return index.neighbors(
            active(context), page, targets=targets, depth=depth, direction=direction, kinds=frozenset(edge_kinds),
        )

    def index_read(
        refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=8)],
        expected_revision: _Id | None = None,
        max_bytes: Annotated[int, Field(ge=1024, le=24 * 1024)] = 12 * 1024,
    ) -> IndexRead:
        return index.read(active(context), refs, expected_revision, max_bytes)

    def index_task_context(
        node_id: _Id, intent: SearchIntent = "change", since_digest: _Id | None = None,
        limit: Annotated[int, Field(ge=1, le=5)] = 5,
    ) -> TaskContext:
        return index.task(active(context), node_id, intent, since_digest, limit)

    def analyzer_list() -> AnalyzerStatuses:
        return index.analyzer_status(active(context))

    def analyzer_diagnostics(
        analyzer: Literal["ruff", "pylint"],
        targets: Annotated[tuple[_IndexPath, ...], Field(min_length=1, max_length=8)],
        expected_revision: _Id | None = None,
    ) -> AnalyzerDiagnostics:
        return index.analyzer_diagnostics(active(context), analyzer, targets, expected_revision)

    def index_lint(  # noqa: PLR0913, PLR0917 -- public MCP signature
        scope: LintScope = "changed",
        node_id: _Id | None = None,
        targets: Annotated[tuple[_IndexPath, ...], Field(max_length=8)] = (),
        expected_revision: _Id | None = None,
        limit: _GraphLimit = 12,
        cursor: _GraphCursor | None = None,
    ) -> LintResult:
        page = PageRequest(method="index_lint", params=(scope, node_id, targets), limit=limit, cursor=cursor)
        return index.lint(
            active(context), page, scope=scope, node_id=node_id, targets=targets, expected_revision=expected_revision,
        )

    def node_claim(node_id: _Id) -> Claimed:
        """Take the lease. Returns the fence later calls must send."""
        ctx = active(context)
        return _primitive(board, ctx, "node/claim", NodeClaimParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
        ), Claimed)

    def step_begin(node_id: _Id, fence: int, *, full_context: bool = False) -> PipelineReply:
        """Begin or resume L2 with stable context, handoff and lease tail."""
        return board.pipeline_step(active(context), "begin", node_id, fence, full_context=full_context)

    def step_submit(  # noqa: PLR0913, PLR0917 -- public MCP signature
        node_id: _Id,
        fence: int,
        step: Literal["spec", "patch", "test", "verify"],
        affected_symbols: Annotated[list[_Text], Field(max_length=12)] | None = None,
        note: _Text | None = None,
        patch: Annotated[str, Field(max_length=256 * 1024)] | None = None,
    ) -> PipelineReply:
        """Record a scoped L2 block. Verify executes the kernel micro gate."""
        ctx = active(context)
        return _primitive(board, ctx, "node/step", NodeStepParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, step=step, affected_symbols=tuple(affected_symbols or ()), note=note, patch=patch,
        ), PipelineReply)

    def step_reset(node_id: _Id, fence: int) -> PipelineReply:
        """Restore scope files to the last verified snapshot; keep other files."""
        ctx = active(context)
        return _primitive(board, ctx, "node/step", NodeStepParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, step="reset",
        ), PipelineReply)

    def gate_run(node_id: _Id, fence: int, tier: Literal["micro", "blast"]) -> PipelineReply:
        """Run a kernel gate or reuse its fresh receipt."""
        ctx = active(context)
        return _primitive(board, ctx, "gate/run", GateRunParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, tier=tier,
        ), PipelineReply)

    def node_escalate(
        node_id: _Id,
        fence: int,
        reason: Annotated[str, Field(min_length=1, max_length=2000)],
        kind: Annotated[str, Field(min_length=1, max_length=32)] = "replan",
    ) -> PipelineReply:
        """Block a node and request a human decision without adding vertices."""
        ctx = active(context)
        return _primitive(board, ctx, "node/escalate", NodeEscalateParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, reason=reason, kind=kind,
        ), PipelineReply)

    def node_update(
        node_id: _Id,
        fence: int | None = None,
        status: Annotated[str, Field(max_length=32)] | None = None,
        note: Annotated[str, Field(max_length=2000)] | None = None,
        evidence: Annotated[str, Field(max_length=4000)] | None = None,
    ) -> Updated:
        """Move a node, or heartbeat. Omit fence only for a transition without an active lease."""
        ctx = active(context)
        return _primitive(board, ctx, "node/update", NodeUpdateParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, status=status, note=note, evidence=evidence,
        ), Updated)

    def node_attach(
        node_id: _Id,
        fence: int,
        kind: Literal["commit", "file", "test_report", "url", "note"],
        ref: _Text,
        title: Annotated[str, Field(max_length=300)] | None = None,
    ) -> Attached:
        """Attach one artifact. The same kind and ref return the same id."""
        ctx = active(context)
        return _primitive(board, ctx, "node/attach", NodeAttachParams(
            project_id=ProjectId(ctx.project_id), node_id=NodeId(node_id), tab_id=TabId(ctx.tab_id),
            fence=fence, kind=kind, ref=ref, title=title,
        ), Attached)

    def ctx_get(
        node_id: _Id | None = None,
        query: _Text | None = None,
        max_items: Annotated[int, Field(ge=1, le=8)] = 6,
        response_format: Literal["concise", "detailed"] = "concise",
    ) -> CtxResult:
        """Knowledge for a node or a query. Summaries stay short."""
        _ = response_format
        ctx = active(context)
        return _primitive(board, ctx, "knowledge/get", KnowledgeGetParams(
            project_id=ProjectId(ctx.project_id),
            node_id=None if node_id is None else NodeId(node_id),
            query=query,
            max_items=max_items,
        ), CtxResult)

    def kb_add(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        type: Literal["fact", "decision", "requirement"],  # noqa: A002 -- MCP field name is type
        title: Annotated[str, Field(max_length=300)],
        body: Annotated[str, Field(max_length=8000)],
        source: _Text,
        code_refs: _Items | None = None,
        supersedes: _Id | None = None,
    ) -> KbAdded:
        """Record a fact, decision, or requirement. A fact's source must exist."""
        ctx = active(context)
        return _primitive(board, ctx, "knowledge/add", KnowledgeAddParams(
            project_id=ProjectId(ctx.project_id), kind=type, title=title, body=body, source=source,
            code_refs=tuple(code_refs or ()), supersedes=supersedes,
        ), KbAdded)

    def kb_impact(
        target: _Text,
        hops: Annotated[int, Field(ge=1, le=3)] = 3,
        limit: Annotated[int, Field(ge=1, le=30)] = 20,
        kinds: Annotated[list[Annotated[str, Field(max_length=32)]], Field(max_length=10)] | None = None,
    ) -> Impact:
        """Neighbors of a file, module, or knowledge id, one list per hop."""
        return board.impact(active(context), target, hops, limit, kinds)

    def handover(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        action: Literal["write", "read"],
        goal: Annotated[str, Field(max_length=8000)] | None = None,
        done: _Items | None = None,
        decisions: _Items | None = None,
        open_questions: _Items | None = None,
        next_steps: _Items | None = None,
        failing_checks: _Items | None = None,
    ) -> HandoverResult:
        """Write this tab's handover, or read the last one plus git diff --stat."""
        ctx = active(context)
        if action == "read":
            params: HandoverReadParams | HandoverWriteParams = HandoverReadParams(
                project_id=ProjectId(ctx.project_id), tab_id=TabId(ctx.tab_id),
            )
            method = "handover/read"
        else:
            params = HandoverWriteParams(
                project_id=ProjectId(ctx.project_id), tab_id=TabId(ctx.tab_id), goal=goal,
                done=tuple(done or ()), decisions=tuple(decisions or ()),
                open_questions=tuple(open_questions or ()), next_steps=tuple(next_steps or ()),
                failing_checks=tuple(failing_checks or ()),
            )
            method = "handover/write"
        return _primitive(board, ctx, method, params, HandoverResult)

    def plan_list(status: Literal["active", "all"] = "active") -> PlanList:
        """Plans for this project. Active means something is still open."""
        return board.list_plans(active(context), status)

    specs = (
        (plan_create, _IDEMPOTENT),
        (plan_add_nodes, _IDEMPOTENT),
        (plan_save, _IDEMPOTENT),
        (plan_delete, _DELETE),
        (plan_get, _READ),
        (plan_graph, _READ),
        (plan_node_get, _READ),
        (index_overview, _READ),
        (index_find, _READ),
        (index_neighbors, _READ),
        (index_read, _READ),
        (index_task_context, _READ),
        (index_lint, _READ),
        (analyzer_list, _READ),
        (analyzer_diagnostics, _READ),
        (plan_ready, _READ),
        (node_claim, _WRITE),
        (step_begin, _IDEMPOTENT),
        (step_submit, _IDEMPOTENT),
        (step_reset, _IDEMPOTENT),
        (gate_run, _WRITE),
        (node_escalate, _IDEMPOTENT),
        (node_update, _WRITE),
        (node_attach, _IDEMPOTENT),
        (ctx_get, _READ),
        (kb_add, _WRITE),
        (kb_impact, _READ),
        (handover, _WRITE),
        (plan_list, _READ),
    )

    def counted(size: int) -> None:
        tab = active(context).tab_id
        if sent is not None and tab:
            sent(tab, size)

    for fn, marks in specs:
        info = TOOL_INFO[fn.__name__]
        _ = server.tool(name=info.name, description=info.description, annotations=marks, structured_output=False)(
            publish(fn, counted),
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


def _local_headers(raw: object) -> bool:
    if not isinstance(raw, list):
        return True
    found: dict[bytes, str] = {}
    for item in raw:  # pyright: ignore[reportUnknownVariableType] -- untyped ASGI headers
        pair = _header_pair(item)  # pyright: ignore[reportUnknownArgumentType] -- validated ASGI pair
        if pair is None:
            return False
        name, value = pair
        name = name.lower()
        if name not in {b"origin", b"host"}:
            continue
        if name in found:
            return False
        found[name] = value.decode("latin-1")
    return origin_allowed(found.get(b"origin")) and host_allowed(found.get(b"host"))


async def _forbidden(send: _Send) -> None:
    body = b'{"reason":"INGEST_ORIGIN_FORBIDDEN"}'
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
