"""Unified extractor JSON: version, nodes, and edges. Version 1 is the only one accepted."""

from typing import Final, final

import msgspec

_VERSION: Final = 1
_SCHEMA: Final = "extractor output does not match the graph schema"
_VERSION_MSG: Final = "unsupported extractor version"


class GraphNode(msgspec.Struct, frozen=True, kw_only=True):
    """One file or module the extractor named."""

    id: str
    path: str
    name: str
    kind: str


class GraphEdge(msgspec.Struct, frozen=True, kw_only=True):
    """A directed link. `kind` is the extractor's relation, such as `imports`."""

    source: str
    target: str
    kind: str


class ExtractorGraph(msgspec.Struct, frozen=True, kw_only=True):
    """The stdout contract of every code-graph extractor."""

    version: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


@final
class ExtractorProtocolError(Exception):
    """Stdout was not version 1 of the graph schema."""

    detail: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.detail = message


def decode_graph(raw: bytes) -> ExtractorGraph:
    """Decode extractor stdout. Unknown versions and broken JSON both fail."""
    try:
        graph = msgspec.json.decode(raw, type=ExtractorGraph)
    except msgspec.ValidationError as exc:
        raise ExtractorProtocolError(_SCHEMA) from exc
    if graph.version != _VERSION:
        raise ExtractorProtocolError(_VERSION_MSG)
    return graph
