"""Pydantic models for the MCP tools. The SDK requires pydantic, not msgspec."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from aconsole.contracts.common import EdgeKind


class NodeSpec(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    key: str = Field(pattern=r"^[A-Z]{1,4}-\d{1,4}$")
    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(max_length=2000)
    depends_on: tuple[str, ...] = ()
    parent: str | None = None
    related: tuple[str, ...] = ()
    owner_agent: Literal["claude", "codex"] | None = None
    paths: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    test_cmd: str | None = None
    review_required: bool = False
    output_format: str | None = None
    boundaries: str | None = None


class PlanCreateIn(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(max_length=4000)
    nodes: tuple[NodeSpec, ...] = ()


class PlanAddNodesIn(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    plan_id: str
    nodes: tuple[NodeSpec, ...] = Field(max_length=50)


class NodeClaimIn(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    node_id: str


class NodeUpdateIn(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    node_id: str
    fence: int
    status: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    evidence: str | None = Field(default=None, max_length=4000)


class NodeAttachIn(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    node_id: str
    fence: int
    kind: Literal["commit", "file", "test_report", "url", "note"]
    ref: str
    title: str | None = None


class McpErrorBody(BaseModel):
    model_config: ClassVar[ConfigDict] = {"frozen": True}
    code: int
    reason: str
    message: str
    hint: str


__all__ = (
    "McpErrorBody",
    "NodeAttachIn",
    "NodeClaimIn",
    "NodeSpec",
    "NodeUpdateIn",
    "PlanAddNodesIn",
    "PlanCreateIn",
)


def edge_kinds() -> tuple[str, ...]:
    return tuple(kind.value for kind in EdgeKind)
