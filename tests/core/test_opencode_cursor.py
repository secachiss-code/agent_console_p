"""OpenCode and Cursor CLI: the launch, the status they report, and what is refused.

The real CLIs are not used. Event shapes follow OpenCode's documentation, not a recorded stream.
"""

from __future__ import annotations

from aconsole.contracts.common import AgentKind
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.infrastructure.containers.podman import ContainerError, Inspected
from aconsole.core.infrastructure.setup_checks import _agent_command  # pyright: ignore[reportPrivateUsage]
from aconsole.core.infrastructure.tabs import HostAgents

_TOKEN = "tok" + "-secret"


def test_capabilities_match_the_plan() -> None:
    for kind in (DomainAgent.OPENCODE, DomainAgent.CURSOR):
        names = CAPABILITIES[kind].names()
        assert {"resume", "mcp_http"} <= names
        assert not {"fork", "limits", "hooks"} & names


def test_host_agents_lists_all_four() -> None:
    def inspect(name: str) -> Inspected:
        raise ContainerError(name)

    infos = HostAgents(inspect).agents()
    by_kind = {info.kind: info for info in infos}
    assert by_kind[AgentKind.OPENCODE].container == "agent-opencode"
    assert by_kind[AgentKind.OPENCODE].display_name == "OpenCode"
    assert by_kind[AgentKind.CURSOR].container == "agent-cursor"
    assert by_kind[AgentKind.CURSOR].available is False
    assert "fork" not in by_kind[AgentKind.CURSOR].capabilities
    assert "fork" in by_kind[AgentKind.CLAUDE].capabilities


def test_setup_check_asks_the_right_binary() -> None:
    assert _agent_command("agent-opencode") == "opencode"
    assert _agent_command("agent-cursor") == "agent"
