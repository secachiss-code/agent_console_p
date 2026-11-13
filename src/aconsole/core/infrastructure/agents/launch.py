"""Per-tab agent argv: hooks, statusline and notify point at this core."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final, final

from aconsole.contracts.app import SessionListResult
from aconsole.contracts.common import AgentKind
from aconsole.contracts.messages import UiMessage
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice
from aconsole.core.infrastructure.agents.claude import sessions as claude_sessions
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.agents.codex import sessions as codex_sessions
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache
from aconsole.core.infrastructure.agents.cursor.launcher import CursorLauncher
from aconsole.core.infrastructure.agents.opencode import sessions as opencode_sessions
from aconsole.core.infrastructure.agents.opencode.launcher import OpenCodeLauncher
from aconsole.core.infrastructure.agents.opencode.stream import EventStream

_LOG = logging.getLogger(__name__)
_NO_PORT: Final = 0
_CODEX_HOOKS_HELP: Final = UiMessage.CODEX_HOOKS_HELP


@final
class HostAgentCommands:
    """Write the tab's files into the agent's home and return the command that uses them.

    Without a home or an ingest port the plain agent still runs, just without status from hooks.
    """

    def __init__(  # noqa: PLR0913 -- independent launcher probes and policy source
        self,
        home_of: Callable[[str], Path | None],
        opencode_heard: Callable[[str, bytes], None] | None = None,
        *,
        codex_hooks: Callable[[str, tuple[str, ...]], bytes] | None = None,
        codex_issue: Callable[[str, str], None] | None = None,
        approval_choice: Callable[[str], ApprovalPolicyChoice] | None = None,
        claude_version: Callable[[str], str] | None = None,
        opencode_version: Callable[[str], str] | None = None,
    ) -> None:
        self._home_of = home_of
        self._approval_choice = approval_choice
        self._claude_version = claude_version
        self._opencode_version = opencode_version
        self._opencode_policy_tabs: set[str] = set()
        self._policy_tabs: set[str] = set()
        self._codex_hooks = codex_hooks
        self._codex_issue = codex_issue
        self._codex_ready = False
        self._claude = ClaudeLauncher()
        self._codex = CodexLauncher()
        self._cursor = CursorLauncher()
        self._opencode = OpenCodeLauncher()
        self._opencode_heard = opencode_heard
        self._streams: dict[str, EventStream] = {}
        self._servers: dict[str, tuple[int, str]] = {}

    def command(  # noqa: PLR0911 -- one exit per harness and its unavailable fallback
        self, agent: AgentKind, tab_id: str, token: str, container: str, port: str,
    ) -> tuple[str, ...] | None:
        """`claude --settings …`, `codex -c …`, or the OpenCode server script. None means run the plain command."""
        if agent is AgentKind.CODEX:
            self._codex_ready = False
        number = int(port) if port.isdecimal() else _NO_PORT
        if agent is AgentKind.CLAUDE:
            return self._claude_command(container, tab_id, token, number)
        if agent is AgentKind.OPENCODE:
            return self._opencode_command(container, tab_id, token, number)
        if number == _NO_PORT or agent not in {AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR}:
            return None
        home = self._home_of(container)
        if home is None:
            return None
        try:
            if agent is AgentKind.CODEX:
                tab = CodexTab(agent_home=home, tab_id=tab_id, port=number, token=token)
                return self._codex_command(tab, container)
            return self._cursor.launch(home)
        except (OSError, ValueError):  # json.JSONDecodeError is a ValueError
            _LOG.warning("cannot write hooks for tab %s; starting the agent without them", tab_id, exc_info=True)
            return None

    def _automatic(self, tab_id: str) -> bool:
        return self._approval_choice is not None and self._approval_choice(tab_id).mode.value in {"allow", "deny"}

    def _opencode_command(self, container: str, tab_id: str, token: str, port: int) -> tuple[str, ...] | None:
        self._opencode_policy_tabs.discard(tab_id)
        supported = (self._opencode_version is not None
                     and self._opencode_version(container).strip() == "opencode v2.1.2")
        if port == _NO_PORT or (self._automatic(tab_id) and not supported):
            if not self._automatic(tab_id):
                return None
            message = "echo 'AConsole: automatic policy requires verified OpenCode v2.1.2 and ingest' >&2; exit 1"
            return ("/bin/sh", "-c", message)
        choice = ApprovalPolicyChoice() if self._approval_choice is None else self._approval_choice(tab_id)
        argv = self._open_opencode(tab_id, token, port, choice)
        if supported:
            self._opencode_policy_tabs.add(tab_id)
        return argv

    @staticmethod
    def _policy_unavailable() -> tuple[str, ...]:
        message = "echo 'AConsole: policy requires Claude >=2.1.295 and writable tab settings' >&2; exit 1"
        return ("/bin/sh", "-c", message)

    def policy_ready(self, tab_id: str) -> bool:
        return tab_id in self._policy_tabs

    def _claude_command(self, container: str, tab_id: str, token: str, port: int) -> tuple[str, ...] | None:
        self._policy_tabs.discard(tab_id)
        try:
            home = self._home_of(container)
            if home is None or port == _NO_PORT:
                return self._policy_unavailable() if self._automatic(tab_id) else None
            if self._claude_version is not None:
                version = re.match(r"(\d+)\.(\d+)\.(\d+)", self._claude_version(container).strip())
                supported = version is not None and tuple(int(part) for part in version.groups()) >= (2, 1, 295)
                if not supported and self._automatic(tab_id):
                    return self._policy_unavailable()
                if supported:
                    self._policy_tabs.add(tab_id)
            choice = ApprovalPolicyChoice() if self._approval_choice is None else self._approval_choice(tab_id)
            return self._claude.launch(ClaudeTab(
                agent_home=home, tab_id=tab_id, port=port, token=token, policy=choice,
            )).argv
        except (OSError, ValueError):
            self._policy_tabs.discard(tab_id)
            _LOG.warning("cannot prepare Claude policy for tab %s", tab_id, exc_info=True)
            return self._policy_unavailable() if self._automatic(tab_id) else None

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
        if kind is AgentKind.CLAUDE and self._policy_tabs:
            return names | {"approval_policy"}
        if kind is AgentKind.OPENCODE and self._opencode_policy_tabs:
            return names | {"approval_policy_static"}
        if kind is AgentKind.CODEX and self._codex_ready:
            return names | {"hooks", "approvals", "checkpoints"}
        return names

    def release(self, agent: AgentKind, tab_id: str, container: str) -> None:
        """Stop the tab's event stream and drop its Claude settings. The shared scripts stay."""
        self._stop_stream(tab_id)
        self._policy_tabs.discard(tab_id)
        self._opencode_policy_tabs.discard(tab_id)
        home = self._home_of(container)
        if home is None or agent is not AgentKind.CLAUDE:
            return
        try:
            self._claude.close(home, tab_id)
        except (OSError, ValueError):
            _LOG.warning("cannot remove hooks for tab %s", tab_id, exc_info=True)

    def list_sessions(self, agent: AgentKind, root: str, container: str) -> SessionListResult:
        """Read the CLI's metadata, or ask only OpenCode servers this core created."""
        if agent is AgentKind.OPENCODE:
            found = {
                item.session_id: item for port, token in self._servers.values()
                for item in opencode_sessions.list_sessions(port, token, root).sessions
            }
            return SessionListResult(sessions=tuple(sorted(
                found.values(), key=lambda item: (item.updated_at, item.session_id), reverse=True,
            )))
        home = self._home_of(container)
        if home is None:
            return SessionListResult(sessions=())
        if agent is AgentKind.CLAUDE:
            return claude_sessions.list_sessions(home, root)
        if agent is AgentKind.CODEX:
            return codex_sessions.list_sessions(home / ".codex" / "state_5.sqlite", root)
        return SessionListResult(sessions=())

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
        for tab_id in tuple(self._servers.keys() | self._streams.keys()):
            self._stop_stream(tab_id)

    def _open_opencode(
        self, tab_id: str, token: str, ingest_port: int, policy: ApprovalPolicyChoice,
    ) -> tuple[str, ...]:
        """A new terminal is a new server on a new port, so an older stream of this tab has nothing left to read."""
        launch = self._opencode.launch(ingest_port, policy)
        self._stop_stream(tab_id)
        self._servers[tab_id] = (launch.server_port, token)
        heard = self._opencode_heard
        if heard is not None:
            stream = EventStream(launch.server_port, token, lambda body: heard(tab_id, body))
            self._streams[tab_id] = stream
            stream.start()
        return launch.argv

    def _stop_stream(self, tab_id: str) -> None:
        _ = self._servers.pop(tab_id, None)
        stream = self._streams.pop(tab_id, None)
        if stream is not None:
            stream.stop()
