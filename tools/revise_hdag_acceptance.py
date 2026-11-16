"""Run an additive acceptance revision; preserve the deliberately exhausted plan.

The four successful L1 nodes verify fixtures and evidence. Exhaustion remains a
separate negative fixture, so cancellation cannot deadlock their dependencies.
Run on the host, against the disposable daemon prepared by hdag_acceptance.py.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import msgspec
from exercise_hdag import Exercise

from aconsole.contracts.app import DirectionGateParams, PlanIdParams, SetNodeStatusParams
from aconsole.contracts.common import NodeId, NodeStatus


class Revision(Exercise):
    async def run(self, manifest: Path, evidence: Path) -> None:
        destination = self.workspace / "evidence/retry"
        destination.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name in ("ACC-2-exhaustion.json", "ACC-2-review.json"):
            source = evidence / name
            data = json.loads(source.read_text())
            if not data["complete"]:
                raise AssertionError("negative fixture is incomplete")
            await asyncio.to_thread(shutil.copyfile, source, destination / name)
            hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.git("add", "evidence/retry")
        self.git("commit", "-qm", "Preserve negative fixture evidence before revision freeze")
        payload = json.loads(await asyncio.to_thread(manifest.read_text))
        payload["nodes"][0]["gate_profile"]["environment"] = {
            "container": self.state.gate, "image": self.state.image}
        async with self.client(0) as client:
            created = await self.call(client, "plan_create", payload)
        self.state = msgspec.structs.replace(self.state, plan=created["plan_id"],
                                            nodes={node["key"]: node["node_id"] for node in created["nodes"]})
        self.rpc("plan/freeze", PlanIdParams(plan_id=self.state.plan))
        self.plan()
        for number, name in ((1, "success"), (2, "retry"), (3, "identity"), (4, "resources")):
            async with self.client(number) as client:
                node = self.state.nodes[f"ACC-{number}"]
                claim = await self.call(client, "node_claim", {"node_id": node})
                params = {"node_id": node, "fence": claim["fence"]}
                await self.call(client, "step_begin", params)
                await self.call(client, "step_submit", {**params, "step": "spec"})
                value = 10 + number
                (self.workspace / f"src/{name}.py").write_text(f"def value() -> int:\n    return {value}\n")
                test = f"from src.{name} import value\n\ndef test_value() -> None:\n    assert value() == {value}\n"
                if number == 2:
                    test += ('\n\ndef test_negative_fixture_evidence() -> None:\n'
                        '    import hashlib\n    import json\n    from pathlib import Path\n'
                        f'    expected = {hashes!r}\n'
                        '    for name, digest in expected.items():\n'
                        '        raw = (Path("evidence/retry") / name).read_bytes()\n'
                        '        assert hashlib.sha256(raw).hexdigest() == digest\n'
                        '        data = json.loads(raw)\n        assert data["complete"]\n'
                        '        reasons = [e.get("response", {}).get("error", {}).get("reason") '
                        'for e in data["events"]]\n'
                        '        assert "ATTEMPTS_EXHAUSTED" in reasons\n')
                (self.workspace / f"tests/test_{name}.py").write_text(test)
                for step in ("patch", "test", "verify"):
                    result = await self.call(client, "step_submit", {**params, "step": step})
                if not result["passed"]:
                    raise AssertionError(f"{name}: real micro gate failed")
                blast = await self.call(client, "gate_run", {**params, "tier": "blast"})
                if not blast["passed"]:
                    raise AssertionError(f"{name}: real blast gate failed")
                await self.call(client, "node_update", {**params, "status": "done",
                                                        "evidence": blast["receipt_hash"]})
            self.commit(name)
            self.plan()
        ready = json.loads((self.root / "ui-ready.json").read_text())
        params = DirectionGateParams(plan_id=self.state.plan, node_id=self.state.nodes["ACC-0"],
                                    tab_id="acc-0", ui_pid=ready["ui_pid"])
        self.rpc("plan/directionGate", msgspec.structs.replace(params,
            ui_pid=json.loads((self.root / "ready.json").read_text())["pid"]), expected_error="NODE_CONTRACT")
        measured = self.rpc("plan/directionGate", params)["result"]
        if measured["passed"] or measured["tab_count"] != 5 or measured["nodes_before"] != measured["nodes_after"]:
            raise AssertionError("resource limits or topology guard did not fail closed")
        self.rpc("plan/setNodeStatus", SetNodeStatusParams(node_id=NodeId(params.node_id), status=NodeStatus.DONE,
            note="Negative acceptance probe: a red receipt must refuse L0 closure."),
            expected_error="GATE_RECEIPT_REQUIRED")
        (self.root / "ui-disconnect").touch()
        async with asyncio.timeout(10):
            while not (self.root / "ui-observer-closed").exists():  # noqa: ASYNC110 -- external process writes marker
                await asyncio.sleep(.1)
        self.rpc("plan/directionGate", params, expected_error="NODE_CONTRACT")
        self.plan()
        (self.root / "revision-state.json").write_bytes(msgspec.json.encode(self.state))

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.workspace), *args], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    exercise = Revision(args.root)
    complete = False
    try:
        asyncio.run(exercise.run(args.manifest, args.evidence))
        complete = True
    finally:
        (args.evidence / "revision-2.json").write_text(json.dumps({"case": "revision-2",
            "complete": complete, "events": exercise.events}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
