"""Project index reads bound to the authenticated tab and worktree."""

from __future__ import annotations

import secrets
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, final

from pydantic import BaseModel

from aconsole.contracts.app import ProjectIndexItem, ProjectIndexLink, ProjectIndexParams, ProjectIndexView
from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.project_inventory import ProjectInventories, WorktreeInventory, read_file
from aconsole.core.infrastructure.codegraph.project_lint import LintResult, ProjectLinter
from aconsole.core.infrastructure.codegraph.project_search import (
    FileSearch,
    IndexEntry,
    SearchDirection,
    SearchIntent,
    SearchKind,
    SourceRef,
    file_entry,
    find_rows,
    neighboring_rows,
    overview_rows,
)
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.runner import GitRunner
from aconsole.core.infrastructure.mcp.graph_pages import GraphCursorError, graph_digest, graph_page
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext, require_tab, tool_error

_EMPTY_GRAPH = ExtractorGraph(version=1, nodes=(), edges=())
_READ_LINES = 120
_MAX_READ_BYTES = 24 * 1024
_CONTEXT_BYTES = 8 * 1024
type LintScope = Literal["changed", "node", "targets"]


class IndexPage(BaseModel, frozen=True):
    revision: str
    state: str
    items: tuple[IndexEntry, ...]
    cursor: str | None = None
    coverage: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    ambiguous: dict[str, tuple[str, ...]] | None = None


class SourceExcerpt(BaseModel, frozen=True):
    ref: SourceRef
    status: str
    text: str | None = None
    next_line: int | None = None


class IndexRead(BaseModel, frozen=True):
    revision: str
    state: str
    items: tuple[SourceExcerpt, ...]


class TaskContext(BaseModel, frozen=True):
    revision: str
    state: str
    plan_revision: str
    digest: str
    unchanged: bool = False
    contract: tuple[dict[str, object], ...] = ()
    direction: tuple[dict[str, object], ...] = ()
    items: tuple[IndexEntry, ...] = ()
    contract_cursor: str | None = None
    contract_truncated: bool = False


@dataclass(frozen=True)
class Snapshot:
    inventory: WorktreeInventory
    graph: ExtractorGraph
    revision: str
    coverage: tuple[str, ...]

    @property
    def state(self) -> str:
        return "partial" if self.coverage else "ready"


@dataclass(frozen=True, kw_only=True)
class PageRequest:
    method: str
    params: tuple[object, ...]
    cursor: str | None
    limit: int


@final
class ProjectIndex:
    def __init__(self, board: PlanBoard) -> None:
        self._board = board
        self._inventories = ProjectInventories()
        self._text = FileSearch()
        self._lint = ProjectLinter()
        self._secret = secrets.token_bytes(32)

    def _root(self, ctx: TabContext) -> Path:
        require_tab(ctx)
        root = ctx.worktree or self._board.project_root(ctx.project_id)
        if root is None:
            tool_error("INDEX_UNAVAILABLE", "this tab has no project root")
        return root.resolve()

    def _snapshot(self, ctx: TabContext, root: Path) -> Snapshot:
        try:
            inventory = self._inventories.snapshot(root)
        except (GitCommandError, OSError):
            tool_error("INDEX_UNAVAILABLE", "the worktree inventory could not be read")
        index = self._board.code_graph
        graph, graph_revision, failed = _EMPTY_GRAPH, "", ("module_graph",)
        if isinstance(index, CodeGraphIndex):
            with suppress(DomainError, StoreWriteError, OSError):
                graph, graph_revision, failed = index.snapshot(ctx.project_id, worktree=root)
        coverage = (*failed, *(("inventory_limit",) if not inventory.complete else ()))
        return Snapshot(inventory, graph, graph_digest(inventory.revision, graph_revision, coverage), coverage)

    def _page(  # noqa: PLR0913 -- shared index page metadata
        self,
        ctx: TabContext,
        snapshot: Snapshot,
        query: PageRequest,
        items: tuple[IndexEntry, ...],
        *,
        unresolved: tuple[str, ...] = (),
        ambiguous: dict[str, tuple[str, ...]] | None = None,
    ) -> IndexPage:
        binding = graph_digest(ctx.tab_id, ctx.project_id, snapshot.revision, query.method, query.params, query.limit)

        def reply(start: int, end: int, cursor: str | None) -> IndexPage:
            return IndexPage(
                revision=snapshot.revision, state=snapshot.state, coverage=snapshot.coverage, items=items[start:end],
                cursor=cursor, unresolved=unresolved, ambiguous=ambiguous or None,
            )

        try:
            return graph_page(
                self._secret, binding, cursor=query.cursor, limit=query.limit, length=len(items), reply=reply,
            )
        except GraphCursorError:
            tool_error("GRAPH_CURSOR_STALE", "the cursor no longer identifies this index read")
        except OverflowError as exc:
            tool_error("RESPONSE_TOO_LARGE", str(exc))

    def overview(self, ctx: TabContext, query: PageRequest, scope: str, depth: int) -> IndexPage:
        snapshot = self._snapshot(ctx, self._root(ctx))
        return self._page(ctx, snapshot, query, overview_rows(snapshot.inventory, scope, depth))

    def scene(self, ctx: TabContext, params: ProjectIndexParams) -> ProjectIndexView:
        root = self._root(ctx)
        snapshot = self._snapshot(ctx, root)
        request = PageRequest(method="project/index", params=(params.scope, params.query, params.anchor),
                              cursor=params.cursor, limit=36)
        if params.anchor is not None:
            rows, _, _ = neighboring_rows(
                snapshot.graph, snapshot.inventory, targets=(params.anchor,), depth=2,
                direction="both", kinds=frozenset(),
            )
            anchor = snapshot.inventory.files.get(params.anchor)
            if anchor is not None:
                rows = (file_entry(anchor), *rows)
        elif params.query:
            matches = self._text.matches(root, snapshot.inventory, params.query)
            rows = find_rows(snapshot.inventory, query=params.query, scope=params.scope, intent="understand",
                             allowed=frozenset(), matches=matches)
        else:
            rows = overview_rows(snapshot.inventory, params.scope, 1)
        selected = self._page(ctx, snapshot, request, rows)
        excerpt = None
        lint = None
        if params.anchor in snapshot.inventory.files:
            excerpt = _excerpt(root, snapshot, SourceRef(path=params.anchor, start_line=1, end_line=26)).text
            if params.diagnose:
                lint = self._lint.run(root, snapshot.inventory, (params.anchor,))
        return ProjectIndexView(
            revision=selected.revision, state=selected.state, cursor=selected.cursor, coverage=selected.coverage,
            items=tuple(ProjectIndexItem(id=row.id, kind=row.kind, name=row.name, path=row.ref.path,
                                         count=row.count, start_line=row.ref.start_line) for row in selected.items),
            links=_scene_links(snapshot.graph, selected.items),
            excerpt=excerpt[:6000] if excerpt is not None else None,
            lint_state=None if lint is None else lint.state,
            diagnostics=() if lint is None else tuple(
                f"{item.path}:{item.line}:{item.column} {item.code or ''} {item.message}"
                for item in lint.diagnostics[:12]
            ),
        )

    def find(  # noqa: PLR0913 -- public search selectors
        self, ctx: TabContext, page: PageRequest, *, query: str, scope: str,
        intent: SearchIntent, kinds: frozenset[SearchKind],
    ) -> IndexPage:
        root = self._root(ctx)
        snapshot = self._snapshot(ctx, root)
        matches = self._text.matches(root, snapshot.inventory, query)
        rows = find_rows(snapshot.inventory, query=query, scope=scope, intent=intent, allowed=kinds, matches=matches)
        tests = _test_paths(snapshot.inventory)
        return self._page(ctx, snapshot, page, tuple(_related(tests, row, intent) for row in rows))

    def neighbors(  # noqa: PLR0913 -- public graph selectors
        self, ctx: TabContext, page: PageRequest, *, targets: tuple[str, ...], depth: int,
        direction: SearchDirection, kinds: frozenset[str],
    ) -> IndexPage:
        snapshot = self._snapshot(ctx, self._root(ctx))
        rows, unresolved, ambiguous = neighboring_rows(
            snapshot.graph, snapshot.inventory, targets=targets, depth=depth, direction=direction, kinds=kinds,
        )
        return self._page(ctx, snapshot, page, rows, unresolved=unresolved, ambiguous=ambiguous)

    def read(
        self, ctx: TabContext, refs: tuple[SourceRef, ...], expected_revision: str | None, max_bytes: int
    ) -> IndexRead:
        root = self._root(ctx)
        snapshot = self._snapshot(ctx, root)
        if expected_revision is not None and snapshot.revision != expected_revision:
            tool_error("INDEX_REVISION_STALE", "the index advanced since these references were issued")
        base = IndexRead(revision=snapshot.revision, state=snapshot.state, items=())
        excerpts = [_excerpt(root, snapshot, ref) for ref in refs]
        budget = min(max_bytes, _MAX_READ_BYTES)
        return _bounded_read(base, excerpts, budget)

    def task(
        self, ctx: TabContext, node_id: str, intent: SearchIntent, since_digest: str | None, limit: int
    ) -> TaskContext:
        detail = self._board.node_details(ctx, node_id, ("contract",), 8, None)
        parent = detail.node.parent
        direction = None if parent is None else self._board.node_details(ctx, parent, ("contract",), 12, None)
        root = self._root(ctx)
        snapshot = self._snapshot(ctx, root)
        paths = self._board.node_paths(ctx, node_id)
        selected = tuple(
            file_entry(item, "task_scope") for path, item in snapshot.inventory.files.items()
            if paths and not check_scope(paths, (path,)).violations
        )
        tests = _test_paths(snapshot.inventory)
        selected = tuple(_related(tests, row, intent) for row in selected[:limit])
        entries = tuple(entry.model_dump(exclude_none=True) for entry in detail.entries)
        direction_revision = None if direction is None else direction.revision
        digest = graph_digest(detail.revision, snapshot.revision, direction_revision, selected)
        answer = TaskContext(
            revision=snapshot.revision, state=snapshot.state, plan_revision=detail.revision, digest=digest,
            unchanged=digest == since_digest,
        )
        if answer.unchanged:
            return answer
        answer = answer.model_copy(update={
            "contract": entries, "contract_cursor": detail.cursor, "contract_truncated": detail.cursor is not None,
        })
        if direction is not None:
            constraints = tuple(
                entry.model_dump(exclude_none=True) for entry in direction.entries
                if entry.field in {"invariants", "boundaries", "objective", "path"}
            )
            with_direction = answer.model_copy(update={"direction": constraints})
            if _reply_bytes(with_direction) <= _CONTEXT_BYTES:
                answer = with_direction
        for count in range(len(selected), 0, -1):
            candidate = answer.model_copy(update={"items": selected[:count]})
            if _reply_bytes(candidate) <= _CONTEXT_BYTES:
                return candidate
        return answer if _reply_bytes(answer) <= _CONTEXT_BYTES else answer.model_copy(update={
            "contract": (), "contract_truncated": True, "contract_cursor": None,
        })

    def lint(  # noqa: PLR0913 -- public lint selectors and pagination
        self, ctx: TabContext, page: PageRequest, *, scope: LintScope, node_id: str | None,
        targets: tuple[str, ...], expected_revision: str | None,
    ) -> LintResult:
        root = self._root(ctx)
        snapshot = self._snapshot(ctx, root)
        if expected_revision is not None and expected_revision != snapshot.revision:
            tool_error("INDEX_REVISION_STALE", "the index advanced since these targets were issued")
        paths = self._lint_paths(ctx, root, scope, node_id, targets)
        result = self._lint.run(root, snapshot.inventory, paths)
        state = result.state
        if self._inventories.snapshot(root).revision != snapshot.inventory.revision:
            state = "stale"
        binding = graph_digest(ctx.tab_id, ctx.project_id, snapshot.revision, page.params, page.limit)

        def reply(start: int, end: int, cursor: str | None) -> LintResult:
            return result.model_copy(update={
                "revision": snapshot.revision, "state": state, "node_id": node_id, "cursor": cursor,
                "diagnostics": result.diagnostics[start:end],
            })

        try:
            return graph_page(
                self._secret, binding, cursor=page.cursor, limit=page.limit,
                length=len(result.diagnostics), reply=reply,
            )
        except GraphCursorError:
            tool_error("GRAPH_CURSOR_STALE", "the cursor no longer identifies these lint findings")
        except OverflowError as exc:
            tool_error("RESPONSE_TOO_LARGE", str(exc))

    def _lint_paths(
        self, ctx: TabContext, root: Path, scope: LintScope, node_id: str | None, targets: tuple[str, ...]
    ) -> tuple[str, ...]:
        if scope == "targets":
            if not targets:
                tool_error("GRAPH_QUERY_INVALID", "targets mode requires at least one file")
            return targets
        try:
            head = GitRunner(timeout_s=5.0).read(root, "rev-parse", "--verify", "HEAD").text.strip()
            changed = GitDiff(timeout_s=5.0).changed_paths(root, head)
        except (GitCommandError, DiffReadError):
            tool_error("INDEX_UNAVAILABLE", "the Git change set could not be determined; specify targets")
        if scope == "changed":
            return changed
        if node_id is None:
            tool_error("GRAPH_QUERY_INVALID", "node mode requires node_id")
        allowed = self._board.node_paths(ctx, node_id)
        return tuple(path for path in changed if not check_scope(allowed, (path,)).violations)


def _test_paths(inventory: WorktreeInventory) -> dict[str, list[SourceRef]]:
    found: dict[str, list[SourceRef]] = {}
    for path, item in inventory.files.items():
        name = Path(path).name
        if name.startswith("test_") or name.endswith("_test.py"):
            found.setdefault(name, []).append(SourceRef(path=path, sha256=item.sha256))
    return found


def _scene_links(graph: ExtractorGraph, rows: tuple[IndexEntry, ...]) -> tuple[ProjectIndexLink, ...]:
    direct = {row.ref.path: row.id for row in rows if row.kind != "directory"}
    folders = tuple((row.ref.path + "/", row.id) for row in rows if row.kind == "directory")
    mapped: dict[str, str] = {}
    for node in graph.nodes:
        entry = direct.get(node.path) or next(
            (ident for folder, ident in folders if node.path.startswith(folder)), None,
        )
        if entry is not None:
            mapped[node.id] = entry
    edges = {(mapped[edge.source], mapped[edge.target], edge.kind) for edge in graph.edges
             if edge.source in mapped and edge.target in mapped and mapped[edge.source] != mapped[edge.target]}
    return tuple(ProjectIndexLink(source=src, target=dst, kind=kind) for src, dst, kind in sorted(edges))


def _related(tests: dict[str, list[SourceRef]], row: IndexEntry, intent: SearchIntent) -> IndexEntry:
    test_name = Path(row.ref.path).stem
    if intent == "understand" or test_name.startswith("test_"):
        return row
    refs = (*tests.get(f"test_{test_name}.py", ()), *tests.get(f"{test_name}_test.py", ()))[:3]
    return row.model_copy(update={"related": refs, "related_reason": "inferred_from_test_filename"}) if refs else row


def _excerpt(root: Path, snapshot: Snapshot, ref: SourceRef) -> SourceExcerpt:
    stored = snapshot.inventory.files.get(ref.path)
    if stored is None or (current := read_file(root, ref.path, stored)) is None:
        return SourceExcerpt(ref=ref, status="unavailable")
    if current.stamp != stored.stamp or (ref.sha256 is not None and current.sha256 != ref.sha256):
        return SourceExcerpt(ref=ref, status="stale")
    if current.text is None:
        return SourceExcerpt(ref=ref, status="metadata_only")
    lines = current.text.splitlines(keepends=True)
    first = ref.start_line or 1
    last = min(ref.end_line or len(lines), len(lines), first + _READ_LINES - 1)
    if first > last:
        return SourceExcerpt(ref=ref, status="empty" if not lines else "invalid_range")
    return SourceExcerpt(
        ref=SourceRef(path=ref.path, start_line=first, end_line=last, sha256=current.sha256),
        status="ok", text="".join(lines[first - 1:last]),
        next_line=last + 1 if last < min(ref.end_line or len(lines), len(lines)) else None,
    )


def _bounded_read(base: IndexRead, excerpts: list[SourceExcerpt], budget: int) -> IndexRead:
    # Reserve a status for each requested reference before filling excerpts.
    chosen = [_deferred(excerpt) if excerpt.text is not None else excerpt for excerpt in excerpts]
    answer = base.model_copy(update={"items": tuple(chosen)})
    if _reply_bytes(answer) > budget:
        tool_error("RESPONSE_TOO_LARGE", "increase max_bytes or request fewer references")
    for position, excerpt in enumerate(excerpts):
        if excerpt.text is None:
            continue
        chosen[position] = excerpt
        candidate = base.model_copy(update={"items": tuple(chosen)})
        if _reply_bytes(candidate) <= budget:
            answer = candidate
        else:
            chosen[position] = _deferred(excerpt)
    return answer


def _deferred(excerpt: SourceExcerpt) -> SourceExcerpt:
    return SourceExcerpt(ref=excerpt.ref, status="reduce_range", next_line=excerpt.ref.start_line)


def _reply_bytes(result: BaseModel) -> int:
    return len(result.model_dump_json(exclude_defaults=True, exclude_none=True).encode())
