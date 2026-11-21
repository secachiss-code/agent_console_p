"""Bounded retrieval over the file inventory and the existing module graph."""

import re
import sqlite3
import threading
from collections import OrderedDict, deque
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal, final

from pydantic import BaseModel, Field

from aconsole.core.infrastructure.codegraph.project_inventory import InventoryFile, WorktreeInventory
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphNode

type SearchIntent = Literal["understand", "change"]
type SearchKind = Literal["directory", "file", "symbol", "module", "submodule"]
type SearchDirection = Literal["in", "out", "both"]
_FTS_ROOTS = 2
_FTS_MATCHES = 256
_QUERY_TERMS = 12


class SourceRef(BaseModel, frozen=True):
    path: str = Field(min_length=1, max_length=4096)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class IndexEntry(BaseModel, frozen=True):
    id: str
    kind: SearchKind
    name: str
    ref: SourceRef
    reason: str | None = None
    count: int | None = None
    via: tuple[str, ...] = ()
    via_edges: tuple[str, ...] = ()
    related: tuple[SourceRef, ...] = ()
    related_reason: str | None = None


@final
class FileSearch:
    """Keep a small cache of FTS stores and update changed documents in place."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roots: OrderedDict[Path, tuple[sqlite3.Connection, dict[str, str]]] = OrderedDict()

    def matches(self, root: Path, snapshot: WorktreeInventory, query: str) -> dict[str, int]:
        words = [match.group() for match in re.finditer(r"[^\W_]+", query.casefold())][:_QUERY_TERMS]
        if not words:
            return {}
        expression = " AND ".join(f'"{word}"' for word in words)
        with self._lock:
            connection = self._refresh(root, snapshot)
            rows = connection.execute(
                "SELECT path FROM files_fts WHERE files_fts MATCH ? ORDER BY bm25(files_fts), path LIMIT ?",
                (expression, _FTS_MATCHES),
            )
            return {str(row[0]): rank for rank, row in enumerate(rows)}  # pyright: ignore[reportAny] -- SQLite returns strings for this column.

    def _refresh(self, root: Path, snapshot: WorktreeInventory) -> sqlite3.Connection:
        cached = self._roots.get(root)
        if cached is None:
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            _ = connection.execute("CREATE VIRTUAL TABLE files_fts USING fts5(path, body, tokenize='unicode61')")
            previous: dict[str, str] = {}
        else:
            connection, previous = cached
        current = {
            path: item.sha256 for path, item in snapshot.files.items()
            if item.sha256 is not None and item.text is not None
        }
        removed = [(path,) for path, digest in previous.items() if current.get(path) != digest]
        added = [(path, snapshot.files[path].text) for path, digest in current.items() if previous.get(path) != digest]
        if removed or added:
            with connection:
                _ = connection.executemany("DELETE FROM files_fts WHERE path = ?", removed)
                _ = connection.executemany("INSERT INTO files_fts (path, body) VALUES (?, ?)", added)
        self._roots[root] = connection, current
        self._roots.move_to_end(root)
        if len(self._roots) > _FTS_ROOTS:
            _, (old, _) = self._roots.popitem(last=False)
            old.close()
        return connection


def file_entry(item: InventoryFile, reason: str | None = None) -> IndexEntry:
    return IndexEntry(
        id=f"file:{item.path}", kind="file", name=PurePosixPath(item.path).name,
        ref=SourceRef(path=item.path, sha256=item.sha256), reason=reason,
    )


def in_scope(path: str, scope: str) -> bool:
    return scope in {"", "."} or path == scope or path.startswith(scope + "/")


def overview_rows(inventory: WorktreeInventory, scope: str, depth: int) -> tuple[IndexEntry, ...]:
    counts: dict[str, int] = {}
    rows: list[IndexEntry] = []
    for path, item in inventory.files.items():
        if not in_scope(path, scope):
            continue
        relative = path if scope in {"", "."} else path.removeprefix(scope + "/")
        parts = relative.split("/")
        if depth >= len(parts) or path == scope:
            rows.append(file_entry(item))
        else:
            group = "/".join(parts[:max(depth, 1)])
            prefix = group if scope in {"", "."} else f"{scope}/{group}"
            counts[prefix] = counts.get(prefix, 0) + 1
    rows.extend(
        IndexEntry(id=f"dir:{path}", kind="directory", name=PurePosixPath(path).name,
                   ref=SourceRef(path=path), count=count)
        for path, count in counts.items()
    )
    rows.extend(
        IndexEntry(id=f"submodule:{path}", kind="submodule", name=PurePosixPath(path).name, ref=SourceRef(path=path))
        for path in inventory.submodules if in_scope(path, scope)
    )
    return tuple(sorted(rows, key=lambda row: (row.kind, row.ref.path)))


def _exact_rank(query: str, names: Iterable[str]) -> int | None:
    folded = tuple(name.casefold() for name in names)
    if query in folded:
        return 0
    if any(query in name for name in folded):
        return 1
    return None


def find_rows(  # noqa: PLR0913 -- ranked query and its snapshot
    inventory: WorktreeInventory,
    *,
    query: str,
    scope: str,
    intent: SearchIntent,
    allowed: frozenset[SearchKind],
    matches: dict[str, int],
) -> tuple[IndexEntry, ...]:
    key = query.strip().casefold()
    results: list[tuple[tuple[int, int, str, str], IndexEntry]] = []
    if not key:
        return ()
    for path, item in inventory.files.items():
        if not in_scope(path, scope):
            continue
        name = PurePosixPath(path)
        if not allowed or "file" in allowed:
            rank = _exact_rank(key, (path, name.name, name.stem))
            if rank is not None or path in matches:
                reason = ("exact_path_or_name", "path_or_name", "text_match")[2 if rank is None else rank]
                order = (2 if rank is None else rank, matches.get(path, 0), path, "")
                results.append((order, file_entry(item, reason)))
        if allowed and "symbol" not in allowed:
            continue
        for symbol in item.symbols:
            score = _exact_rank(key, (symbol.name, symbol.name.rsplit(".", 1)[-1]))
            if score is None:
                continue
            ref = SourceRef(path=path, start_line=symbol.start_line, end_line=symbol.end_line, sha256=item.sha256)
            identifier = f"symbol:{path}#{symbol.name}:{symbol.occurrence}"
            row = IndexEntry(id=identifier, kind="symbol", name=symbol.name, ref=ref, reason="definition")
            priority = -1 if score == 0 and intent == "change" else score
            results.append(((priority, 0, path, identifier), row))
    return tuple(row for _, row in sorted(results, key=lambda item: item[0]))


def neighboring_rows(  # noqa: PLR0913 -- neighborhood parameters
    modules: ExtractorGraph,
    inventory: WorktreeInventory,
    *,
    targets: tuple[str, ...],
    depth: int,
    direction: SearchDirection,
    kinds: frozenset[str],
) -> tuple[tuple[IndexEntry, ...], tuple[str, ...], dict[str, tuple[str, ...]]]:
    visible = {node.id: node for node in modules.nodes if node.path in inventory.files}
    seeds, unresolved, ambiguous = _seeds(inventory, visible, targets)
    adjacent: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in visible}
    for edge in modules.edges:
        if edge.source not in visible or edge.target not in visible or (kinds and edge.kind not in kinds):
            continue
        if direction != "in":
            adjacent[edge.source].append((edge.target, edge.kind))
        if direction != "out":
            adjacent[edge.target].append((edge.source, edge.kind))
    seen = set(seeds)
    pending: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
        (node_id, (seeds[node_id],), ()) for node_id in sorted(seeds)
    )
    rows: list[IndexEntry] = []
    while pending:
        node_id, chain, edges = pending.popleft()
        if edges:
            node = visible[node_id]
            rows.append(IndexEntry(
                id=node_id, name=node.name, kind="module",
                ref=SourceRef(path=node.path, sha256=inventory.files[node.path].sha256),
                via=chain, via_edges=edges, reason="dependency",
            ))
        if len(edges) == depth:
            continue
        for neighbor, kind in sorted(adjacent[node_id]):
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append((neighbor, (*chain, neighbor), (*edges, kind)))
    return tuple(rows), unresolved, ambiguous


def _seeds(
    inventory: WorktreeInventory, visible: dict[str, GraphNode], targets: tuple[str, ...]
) -> tuple[dict[str, str], tuple[str, ...], dict[str, tuple[str, ...]]]:
    seeds: dict[str, str] = {}
    unresolved: list[str] = []
    ambiguous: dict[str, tuple[str, ...]] = {}
    for target in targets:
        matches = tuple(node.id for node in visible.values() if target in {node.id, node.path, node.name})
        if target in visible:
            seeds[target] = target
        elif target in inventory.files:
            seeds.update((identifier, target) for identifier in matches)
        elif len(matches) == 1:
            seeds[matches[0]] = target
        elif matches:
            ambiguous[target] = matches[:8]
        else:
            unresolved.append(target)
    return seeds, tuple(unresolved), ambiguous
