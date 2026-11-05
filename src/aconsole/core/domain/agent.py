"""Agent kind and the capabilities a container can offer."""

from dataclasses import dataclass, replace
from enum import StrEnum


class AgentKind(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GROK = "grok"
    CURSOR = "cursor"


@dataclass(frozen=True, kw_only=True)
class AgentCapabilities:
    hooks: bool
    statusline: bool
    limits: bool
    resume: bool
    fork: bool
    skills: bool
    approvals: bool
    checkpoints: bool
    mcp_http: bool

    def names(self) -> frozenset[str]:
        enabled = {
            "hooks": self.hooks,
            "statusline": self.statusline,
            "limits": self.limits,
            "resume": self.resume,
            "fork": self.fork,
            "skills": self.skills,
            "approvals": self.approvals,
            "checkpoints": self.checkpoints,
            "mcp_http": self.mcp_http,
        }
        return frozenset(name for name, on in enabled.items() if on)


_ALL_ON = AgentCapabilities(
    hooks=True, statusline=True, limits=True, resume=True, fork=True, skills=True, approvals=True, checkpoints=True,
    mcp_http=True,
)
# No hooks, so no inline approvals and no checkpoint at each prompt. Their status comes from elsewhere.
_NO_HOOKS = AgentCapabilities(
    hooks=False, statusline=False, limits=False, resume=True, fork=False, skills=True, approvals=False,
    checkpoints=False, mcp_http=True,
)
CAPABILITIES: dict[AgentKind, AgentCapabilities] = {
    AgentKind.CLAUDE: _ALL_ON,
    AgentKind.CODEX: replace(
        _ALL_ON, statusline=False, skills=False, hooks=False, approvals=False, checkpoints=False,
    ),
    AgentKind.OPENCODE: replace(_NO_HOOKS, limits=True),
    AgentKind.CURSOR: replace(_NO_HOOKS, limits=True),
}
