"""Complete planning contracts for transport tests that stop at freeze/claim.

These fixtures do not provide or simulate successful verification receipts.
"""

from tests.gate_profiles import MICRO_PROFILE


def planning_nodes(key: str) -> list[dict[str, object]]:
    return [
        {"key": "DIR-0", "title": "Transport direction", "objective": "Exercise the real plan transport",
         "level": 0, "paths": ["README.md"], "acceptance": ["The module lease reaches the UI"],
         "depends_on": [key], "invariants": "Only kernel gates can verify this module",
         "gate_profile": {**MICRO_PROFILE, "direction": [["python", "-m", "pytest", "tests"]]}},
        {"key": key, "title": "Transport module", "objective": "Preserve the lease and its owning tab",
         "parent": "DIR-0", "paths": ["README.md"], "acceptance": ["The claimed lease survives restart"],
         "test_cmd": "python -m pytest tests"},
    ]
