"""The hostrun allowlist matches the server, the example policy, and the two client lists."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "hostrun"))

from tools.hostrun import actions, server  # noqa: E402

_AGENT_DENIED = frozenset({"core-run"})
_FRONT_DENIED = frozenset({"core-run", "gate", "distrobox-dry-run", "conformance", "devtools"})


def _policy_actions() -> dict[str, frozenset[str]]:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    return {name: container.actions for name, container in policy.containers.items()}


def _cli_actions() -> frozenset[str]:
    text = (ROOT / "docs" / "plan" / "core" / "templates" / "cli.json").read_text(encoding="utf-8")
    return frozenset(re.findall(r'"Shell\(hostrun:([A-Za-z0-9-]+)', text))


def _front_actions() -> frozenset[str]:
    text = (ROOT / "docs" / "plan" / "front" / "codex" / "hostrun.rules").read_text(encoding="utf-8")
    match = re.search(r'pattern = \["hostrun", \[(.*?)]\]', text, re.DOTALL)
    assert match is not None
    return frozenset(re.findall(r'"([^"]+)"', match.group(1)))


def test_allowlist_matches_known_actions() -> None:
    known = actions.KNOWN_ACTIONS
    by_container = _policy_actions()
    assert set().union(*by_container.values()) == known
    assert by_container["agent-cursor"] == known
    assert by_container["agent-claude"] == known
    assert by_container["agent-codex"] == known - _FRONT_DENIED
    assert _cli_actions() == known - _AGENT_DENIED
    assert "core-run" not in _cli_actions()
    assert _front_actions() == by_container["agent-codex"]
    assert _FRONT_DENIED.isdisjoint(_front_actions())
