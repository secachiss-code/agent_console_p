"""Per-tab agent argv: hooks, statusline and notify point at this core."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Final, final

from aconsole.contracts.common import AgentKind
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache

_LOG = logging.getLogger(__name__)
_NO_PORT: Final = 0


@final
class HostAgentCommands:
    """Write the tab's files into the agent's home and return the command that uses them.

    Without a home or an ingest port the plain agent still runs, just without status from hooks.
    """

    def __init__(self, home_of: Callable[[str], Path | None]) -> None:
        self._home_of = home_of
        self._claude = ClaudeLauncher()
        self._codex = CodexLauncher()

    def command(self, agent: AgentKind, tab_id: str, token: str, container: str, port: str) -> tuple[str, ...] | None:
        """`claude --settings …` or `codex -c …`. None means run the plain command."""
        number = int(port) if port.isdecimal() else _NO_PORT
        if number == _NO_PORT or agent not in {AgentKind.CLAUDE, AgentKind.CODEX}:
            return None
        home = self._home_of(container)
        if home is None:
            return None
        try:
            if agent is AgentKind.CLAUDE:
                tab = ClaudeTab(agent_home=home, tab_id=tab_id, port=number, token=token)
                return self._claude.launch(tab).argv
            # No hook is trusted yet (ADR-0002): notify and MCP are still configured.
            codex = CodexTab(agent_home=home, tab_id=tab_id, port=number, token=token)
            return self._codex.launch(codex, HookTrustCache()).argv
        except (OSError, ValueError):
            _LOG.warning("cannot write hooks for tab %s; starting the agent without them", tab_id, exc_info=True)
            return None

    def release(self, agent: AgentKind, tab_id: str, container: str) -> None:
        """Drop the tab's Claude settings. The shared scripts stay."""
        home = self._home_of(container)
        if home is None or agent is not AgentKind.CLAUDE:
            return
        try:
            self._claude.close(home, tab_id)
        except (OSError, ValueError):
            _LOG.warning("cannot remove hooks for tab %s", tab_id, exc_info=True)
