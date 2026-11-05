"""Per-tab agent argv: hooks, statusline and notify point at this core."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Final, final

from aconsole.contracts.common import AgentKind
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache
from aconsole.core.infrastructure.agents.cursor.launcher import CursorLauncher
from aconsole.core.infrastructure.agents.opencode.launcher import OpenCodeLauncher
from aconsole.core.infrastructure.agents.opencode.stream import EventStream

_LOG = logging.getLogger(__name__)
_NO_PORT: Final = 0
_CODEX_HOOKS_HELP: Final = """Hooks Codex не подтверждены.
Согласования остаются в терминале, автоматические чекпоинты отключены.
Откройте /hooks в Codex, проверьте настройки hooks и ограничения администратора,
затем перезапустите агента в AConsole."""


@final
class HostAgentCommands:
    """Write the tab's files into the agent's home and return the command that uses them.

    Without a home or an ingest port the plain agent still runs, just without status from hooks.
    """

    def __init__(
        self,
        home_of: Callable[[str], Path | None],
        opencode_heard: Callable[[str, bytes], None] | None = None,
        *,
        codex_hooks: Callable[[str, tuple[str, ...]], bytes] | None = None,
        codex_issue: Callable[[str, str], None] | None = None,
    ) -> None:
        self._home_of = home_of
        self._codex_hooks = codex_hooks
        self._codex_issue = codex_issue
        self._codex_ready = False
        self._claude = ClaudeLauncher()
        self._codex = CodexLauncher()
        self._cursor = CursorLauncher()
        self._opencode = OpenCodeLauncher()
        self._opencode_heard = opencode_heard
        self._streams: dict[str, EventStream] = {}

    def command(self, agent: AgentKind, tab_id: str, token: str, container: str, port: str) -> tuple[str, ...] | None:
        """`claude --settings …`, `codex -c …`, or the OpenCode server script. None means run the plain command."""
        if agent is AgentKind.CODEX:
            self._codex_ready = False
        number = int(port) if port.isdecimal() else _NO_PORT
        if number == _NO_PORT or agent not in {AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR}:
            return None
        if agent is AgentKind.OPENCODE:
            return self._open_opencode(tab_id, token, number)
        home = self._home_of(container)
        if home is None:
            return None
        try:
            if agent is AgentKind.CODEX:
                tab = CodexTab(agent_home=home, tab_id=tab_id, port=number, token=token)
                return self._codex_command(tab, container)
            return self._write_files(agent, home, tab_id, token, number)
        except (OSError, ValueError):  # json.JSONDecodeError is a ValueError
            _LOG.warning("cannot write hooks for tab %s; starting the agent without them", tab_id, exc_info=True)
            return None

    def _write_files(self, agent: AgentKind, home: Path, tab_id: str, token: str, port: int) -> tuple[str, ...]:
        if agent is AgentKind.CLAUDE:
            return self._claude.launch(ClaudeTab(agent_home=home, tab_id=tab_id, port=port, token=token)).argv
        return self._cursor.launch(home)

    def _codex_command(self, codex: CodexTab, container: str) -> tuple[str, ...]:
        self._codex_ready = False
        source = self._codex_hooks
        if source is not None:
            try:
                argv = self._verified_codex(codex, container, source)
            except (OSError, ValueError):
                _LOG.warning("Codex hook verification failed for %s", codex.tab_id, exc_info=True)
            else:
                self._codex_ready = True
                return argv
        if self._codex_issue is not None:
            self._codex_issue(codex.tab_id, _CODEX_HOOKS_HELP)
        return self._codex.launch(codex, HookTrustCache()).argv

    def _verified_codex(
        self, tab: CodexTab, container: str, source: Callable[[str, tuple[str, ...]], bytes],
    ) -> tuple[str, ...]:
        probe = self._codex.probe(tab)
        checked = self._codex.verified(tab, source(container, probe.argv))
        confirmed = self._codex.verified(tab, source(container, checked.argv), require_trusted=True)
        if confirmed.argv != checked.argv:
            message = "Codex hook hashes changed during verification"
            raise ValueError(message)
        return confirmed.argv

    def capabilities(self, kind: AgentKind) -> frozenset[str]:
        """Advertise Codex hook features only after the installed CLI confirmed their trust."""
        names = CAPABILITIES[DomainAgent(kind.value)].names()
        if kind is AgentKind.CODEX and self._codex_ready:
            return names | {"hooks", "approvals", "checkpoints"}
        return names

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
