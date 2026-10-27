"""Obsidian note shapes the core writes. `.obsidian/` is never a source of truth."""

from typing import Literal

import msgspec

from aconsole.contracts.common import AgentKind, AwareDatetime, NodeStatus, Ulid


class TaskNote(msgspec.Struct, frozen=True, kw_only=True):
    aconsole: Literal[1] = 1
    note_type: Literal["task"] = msgspec.field(name="type", default="task")
    id: Ulid
    plan: str
    key: str
    status: NodeStatus
    owner_agent: AgentKind | None = None
    depends_on: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    review_required: bool = False
    tags: tuple[str, ...] = ()
    lease: str = ""
    updated: AwareDatetime


class CanvasNode(msgspec.Struct, frozen=True, kw_only=True):
    id: Ulid
    node_type: Literal["file"] = msgspec.field(name="type", default="file")
    file: str
    x: int
    y: int
    width: int
    height: int


class CanvasFile(msgspec.Struct, frozen=True, kw_only=True):
    nodes: tuple[CanvasNode, ...]
    edges: tuple[str, ...] = ()
