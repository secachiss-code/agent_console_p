"""Agent kind and the capabilities a container can offer."""

from dataclasses import dataclass
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
        }
        return frozenset(name for name, on in enabled.items() if on)
