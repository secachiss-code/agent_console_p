# ruff: noqa: INP001 -- standalone operator helper, not an importable package
"""Submit the prepared scope repair or resume VLT-1 through native public IPC.

Run in the host shell with the project's existing Python environment. Without
an action argument, print the repair payload and make no IPC calls.
"""

import argparse
import os
import sys
from pathlib import Path

import msgspec

from aconsole.contracts.app import SetNodeStatusParams
from aconsole.contracts.common import NodeId, NodeStatus
from aconsole.contracts.ipc import ResponseOk
from aconsole.devcli.client import request_frame


class ScopeRepair(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    project_id: str
    title: str
    goal: str
    plan_id: str
    repair_paths: dict[str, tuple[str, ...]]
    repair_contracts: dict[str, str]
    repair_note: str


class Arguments(argparse.Namespace):
    action: str = "preview"
    socket: str = f"/run/user/{os.getuid()}/aconsole/core.sock"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("action", choices=("preview", "repair-scopes", "resume-vlt1"), nargs="?", default="preview")
    _ = parser.add_argument("--socket", default=Arguments.socket)
    args = parser.parse_args(namespace=Arguments())
    payload = Path(__file__).with_name("repair-plan.json").read_bytes()
    repair = msgspec.json.decode(payload, type=ScopeRepair)
    if args.action == "preview":
        _ = sys.stdout.write(payload.decode() + "\n")
        return 0
    if args.action == "repair-scopes":
        method = "plan/save"
        params: msgspec.Struct = repair
    else:
        method = "plan/setNodeStatus"
        params = SetNodeStatusParams(
            node_id=NodeId("node-912"),
            status=NodeStatus.PENDING,
            note="User requested fixing VLT-1 typing and repeating pipeline acceptance; local checks passed.",
        )
    response = request_frame(args.socket, method, params, timeout=60)
    _ = sys.stdout.write(msgspec.json.encode(response).decode() + "\n")
    return 0 if isinstance(response, ResponseOk) else 1


if __name__ == "__main__":
    raise SystemExit(main())
