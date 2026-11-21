"""Package the project workflows as on-demand resources for every MCP adapter."""

from collections.abc import Callable
from importlib.resources import files

from mcp.server.mcpserver import MCPServer

_SKILLS = (
    ("aconsole-project-index", "Find code and tests with the project index."),
    ("aconsole-plan-node", "Inspect, execute or resume one plan node."),
)


def register_skills(server: MCPServer) -> None:
    for name, description in _SKILLS:
        _ = server.resource(
            f"aconsole://skills/{name}", name=name, description=description, mime_type="text/markdown",
        )(_reader(name))


def _reader(name: str) -> Callable[[], str]:
    def read() -> str:
        return files("aconsole").joinpath("skills", name, "SKILL.md").read_text(encoding="utf-8")

    return read
