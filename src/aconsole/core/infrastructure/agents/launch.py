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
from aconsole.core.infrastructure.agents.cursor.launcher import CursorLauncher
from aconsole.core.infrastructure.agents.opencode.launcher import OpenCodeLauncher
from aconsole.core.infrastructure.agents.opencode.stream import EventStream

_LOG = logging.getLogger(__name__)
_NO_PORT: Final = 0


@final
class HostAgentCommands:
    """Write the tab's files into the agent's home and return the command that uses them.

    Without a home or an ingest port the plain agent still runs, just without status from hooks.
    """

    def __init__(
        self,
        home_of: Callable[[str], Path | None],
        opencode_heard: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._home_of = home_of
        self._claude = ClaudeLauncher()
        self._codex = CodexLauncher()
        self._cursor = CursorLauncher()
        self._opencode = OpenCodeLauncher()
        self._opencode_heard = opencode_heard
        self._streams: dict[str, EventStream] = {}

    def command(self, agent: AgentKind, tab_id: str, token: str, container: str, port: str) -> tuple[str, ...] | None:
        """`claude --settings …`, `codex -c …`, or the OpenCode server script. None means run the plain command."""
        number = int(port) if port.isdecimal() else _NO_PORT
        if number == _NO_PORT or agent not in {AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR}:
            return None
        if agent is AgentKind.OPENCODE:
            return self._open_opencode(tab_id, token, number)
        home = self._home_of(container)
        if home is None:
            return None
        try:
            return self._write_files(agent, home, tab_id, token, number)
        except (OSError, ValueError):  # json.JSONDecodeError is a ValueError
            _LOG.warning("cannot write hooks for tab %s; starting the agent without them", tab_id, exc_info=True)
            return None

    def _write_files(self, agent: AgentKind, home: Path, tab_id: str, token: str, port: int) -> tuple[str, ...]:
        if agent is AgentKind.CLAUDE:
            return self._claude.launch(ClaudeTab(agent_home=home, tab_id=tab_id, port=port, token=token)).argv
        if agent is AgentKind.CURSOR:
            return self._cursor.launch(home)
        # No hook is trusted yet (ADR-0002): notify and MCP are still configured.
        codex = CodexTab(agent_home=home, tab_id=tab_id, port=port, token=token)
        return self._codex.launch(codex, HookTrustCache()).argv

    def release(self, agent: AgentKind, tab_id: str, container: str) -> None:
        """Stop the tab's event stream and drop its Claude settings. The shared scripts stay."""
        self._stop_stream(tab_id)
        home = self._home_of(container)
        if home is None or agent is not AgentKind.CLAUDE:
            return
        try:
            self._claude.close(home, tab_id)
        except (OSError, ValueError):
            _LOG.warning("cannot remove hooks for tab %s", tab_id, exc_info=True)

    def session_exists(self, agent: AgentKind, session: str, container: str) -> bool:
        """False only when Claude has no transcript of it: it writes one at the first message, not at the start.

        Looked for under every project, so an unusual path never loses a real conversation. The core checks the id
        before keeping it, so it is one plain path word. Other agents, and a home that cannot be read, get the benefit
        of the doubt.
        """
        home = self._home_of(container)
        if home is None or agent is not AgentKind.CLAUDE:
            return True
        try:
            return any((home / ".claude" / "projects").glob(f"*/{session}.jsonl"))
        except OSError:
            _LOG.warning("cannot look for session %s", session, exc_info=True)
            return True

    def stop_streams(self) -> None:
        for tab_id in tuple(self._streams):
            self._stop_stream(tab_id)

    def _open_opencode(self, tab_id: str, token: str, ingest_port: int) -> tuple[str, ...]:
        """A new terminal is a new server on a new port, so an older stream of this tab has nothing left to read."""
        launch = self._opencode.launch(ingest_port)
        self._stop_stream(tab_id)
        heard = self._opencode_heard
        if heard is not None:
            stream = EventStream(launch.server_port, token, lambda body: heard(tab_id, body))
            self._streams[tab_id] = stream
            stream.start()
        return launch.argv

    def _stop_stream(self, tab_id: str) -> None:
        stream = self._streams.pop(tab_id, None)
        if stream is not None:
            stream.stop()
