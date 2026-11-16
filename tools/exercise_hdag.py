"""Exercise a prepared isolated H-DAG over real MCP HTTP and native human IPC.

Run on the host with the candidate core Python. Negative cases use the real
container runner; no verdict, process metric, fence or receipt is synthesized.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
lazy from collections.abc import AsyncIterator

import httpx2
import msgspec
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from aconsole.contracts.app import PlanDecideParams, PlanIdParams
from aconsole.contracts.ipc import ResponseErr
from aconsole.devcli.client import request_frame


class State(msgspec.Struct):
    workspace: str
    database: str
    socket: str
    project: str
    plan: str
    nodes: dict[str, str]
    executor: str
    gate: str
    image: str
    model: str


class Exercise:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state = msgspec.json.decode((self.root / "state.json").read_bytes(), type=State)
        self.workspace = Path(self.state.workspace)
        if self.workspace.parent != self.root or self.state.project != "hdag-acceptance":
            raise ValueError("only the disposable acceptance project may be exercised")
        self.events: list[dict[str, object]] = []
        self.private = msgspec.json.decode((self.root / "private.json").read_bytes(), type=dict[str, str])

    @asynccontextmanager
    async def client(self, number: int) -> AsyncIterator[Client]:
        ready = msgspec.json.decode((self.root / "ready.json").read_bytes(), type=dict[str, object])
        headers = {"Authorization": "Bearer " + self.private[f"acc-{number}"]}
        async with httpx2.AsyncClient(headers=headers, timeout=180) as http:
            transport = streamable_http_client(f"http://127.0.0.1:{ready['port']}/mcp", http_client=http)
            async with Client(transport) as client:
                yield client

    async def call(self, client: Client, name: str, arguments: dict[str, object],
                   *, expected_error: str | None = None) -> dict[str, object]:
        result = await client.call_tool(name, arguments)
        text = "".join(item.text for item in result.content if isinstance(item, TextContent))
        if result.is_error and result.structured_content is not None:
            body = msgspec.convert(result.structured_content, type=dict[str, object])
        else:
            body = msgspec.json.decode(text, type=dict[str, object])
        self.events.append({"transport": "mcp-http", "tool": name, "arguments": arguments, "response": body,
                            "is_error": bool(result.is_error)})
        if expected_error is not None:
            if not result.is_error:
                raise AssertionError(f"{name}: expected a refusal")
            error = body.get("error")
            if expected_error != "*" and (not isinstance(error, dict) or error.get("reason") != expected_error):
                raise AssertionError(f"{name}: unexpected refusal {body}")
        elif result.is_error:
            raise AssertionError(f"{name}: {body}")
        return body

    def rpc(self, method: str, params: msgspec.Struct, *, expected_error: str | None = None) -> dict[str, object]:
        response = request_frame(self.state.socket, method, params, timeout=180)
        body = msgspec.json.decode(msgspec.json.encode(response), type=dict[str, object])
        self.events.append({"transport": "human-ipc", "method": method, "response": body})
        if expected_error is not None:
            if not isinstance(response, ResponseErr) or response.error.message != expected_error:
                raise AssertionError(f"{method}: expected {expected_error}, got {body}")
        elif isinstance(response, ResponseErr):
            raise AssertionError(f"{method}: {body}")
        return body

    def plan(self) -> dict[str, object]:
        response = self.rpc("plan/get", PlanIdParams(plan_id=self.state.plan))
        plan = response["result"]
        if not isinstance(plan, dict) or len(plan["nodes"]) != 5 or plan["phase"] != "frozen":
            raise AssertionError("frozen topology must remain 1 L0 + 4 L1")
        return plan

    def node(self, key: str) -> dict[str, object]:
        return next(item for item in self.plan()["nodes"] if item["key"] == key)

    def inbox(self) -> list[dict[str, object]]:
        response = self.rpc("plan/inbox", PlanIdParams(plan_id=self.state.plan))
        return response["result"]["entries"]

    def commit(self, case: str) -> None:
        subprocess.run(["git", "-C", str(self.workspace), "add", "--", f"src/{case}.py", f"tests/test_{case}.py"],
                       check=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "-qm", f"Acceptance checkpoint: {case}"],
                       check=True)

    async def retries(self) -> None:
        if self.node("ACC-1")["status"] != "done":
            raise AssertionError("the live CLI happy path must finish first")
        self.commit("success")
        node = self.state.nodes["ACC-2"]
        async with self.client(2) as client:
            claim = await self.call(client, "node_claim", {"node_id": node})
            params = {"node_id": node, "fence": claim["fence"]}
            for attempt in range(1, 5):
                begin = await self.call(client, "step_begin", params)
                handoff = msgspec.json.decode(begin["handoff"], type=dict[str, object])
                if handoff["attempt"] != attempt:
                    raise AssertionError("attempt numbering changed")
                if attempt == 1:
                    await self.call(client, "step_submit", {**params, "step": "spec"})
                (self.workspace / "src/retry.py").write_text('def value() -> int:\n    return "invalid"\n')
                for step in ("patch", "test"):
                    await self.call(client, "step_submit", {**params, "step": step})
                failed = await self.call(client, "step_submit", {**params, "step": "verify"})
                if failed["passed"] or failed["exhausted"] != (attempt == 4):
                    raise AssertionError("the real failed gate did not consume the expected budget")
            await self.call(client, "node_update", {**params, "status": "pending"},
                            expected_error="NODE_TRANSITION_INVALID")
            await self.call(client, "step_begin", params, expected_error="*")
        blocked = self.node("ACC-2")
        if blocked["status"] != "blocked" or blocked["attempt_no"] != 4:
            raise AssertionError("four failures must remain blocked without a fifth attempt")
        entries = [item for item in self.inbox() if item["node_id"] == node]
        if len(entries) != 1:
            raise AssertionError("exhaustion must create exactly one open inbox request")

    async def review(self) -> None:
        node = self.state.nodes["ACC-2"]
        blocked = self.node("ACC-2")
        if blocked["status"] != "blocked" or blocked["attempt_no"] != 4:
            raise AssertionError("restart lost the exhausted budget")
        entry = next(item for item in self.inbox() if item["node_id"] == node)
        params = PlanDecideParams(plan_id=self.state.plan, request_id=entry["id"], decision="unblock", note="")
        self.rpc("plan/decide", params, expected_error="NODE_CONTRACT")
        params = msgspec.structs.replace(params,
            note="Authorized acceptance review after restart; budget stays exhausted.")
        self.rpc("plan/decide", params)
        self.rpc("plan/decide", params)
        async with self.client(2) as client:
            claim = await self.call(client, "node_claim", {"node_id": node})
            await self.call(client, "step_begin", {"node_id": node, "fence": claim["fence"]},
                            expected_error="ATTEMPTS_EXHAUSTED")
        if self.node("ACC-2")["attempt_no"] != 4:
            raise AssertionError("human unblock must not secretly replenish attempts")
        # The fixture is deliberately exhausted. Cancellation is its correct human
        # terminal outcome; it is never marked done with a fabricated green receipt.
        await asyncio.to_thread(subprocess.run, ["git", "-C", str(self.workspace), "restore", "--source=HEAD", "--",
                        "src/retry.py", "tests/test_retry.py"], check=True)
        entry = next(item for item in self.inbox() if item["node_id"] == node)
        self.rpc("plan/decide", PlanDecideParams(plan_id=self.state.plan, request_id=entry["id"],
                 decision="cancel", note="Negative fixture exhausted as intended; preserve its four failure receipts."))
        if self.node("ACC-2")["status"] != "cancelled":
            raise AssertionError("human cancellation was not persisted")

    async def identity(self) -> None:
        node = self.state.nodes["ACC-3"]
        async with self.client(3) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            if names & {"plan_create", "plan_add_nodes", "plan_save", "plan_delete", "plan_decide"}:
                raise AssertionError("an executor sees graph or human decision tools")
            await self.call(client, "plan_create", {"title": "Forbidden", "goal": "Forbidden"},
                            expected_error="PLAN_ROLE")
            claim = await self.call(client, "node_claim", {"node_id": node})
            params = {"node_id": node, "fence": claim["fence"]}
            await self.call(client, "step_begin", params)
            await self.call(client, "step_submit", {**params, "step": "spec", "fence": 0},
                            expected_error="LEASE_STALE_FENCE")
            await self.call(client, "step_submit", {**params, "step": "spec"})
            outside = self.workspace / "outside.txt"
            outside.write_text("outside contract\n")
            try:
                await self.call(client, "step_submit", {**params, "step": "patch"}, expected_error="SCOPE_VIOLATION")
            finally:
                outside.unlink()
            (self.workspace / "src/identity.py").write_text("def value() -> int:\n    return 1\n")
            (self.workspace / "tests/test_identity.py").write_text(
                "from src.identity import value\n\ndef test_value() -> None:\n    assert value() == 1\n")
            for step in ("patch", "test", "verify"):
                green = await self.call(client, "step_submit", {**params, "step": step})
            if not green["passed"]:
                raise AssertionError("identity fixture did not obtain a real micro receipt")
            source = self.workspace / "src/identity.py"
            original = source.read_text()
            source.write_text(original.replace("return 1", "return 2"))
            await self.call(client, "gate_run", {**params, "tier": "blast"}, expected_error="GATE_RECEIPT_REQUIRED")
            await self.call(client, "node_update", {**params, "status": "done", "evidence": "forged green"},
                            expected_error="GATE_RECEIPT_REQUIRED")
            source.write_text(original)
            blast = await self.call(client, "gate_run", {**params, "tier": "blast"})
            replay = await self.call(client, "gate_run", {**params, "tier": "blast"})
            if not blast["passed"] or not replay["cached"] or blast["receipt_hash"] != replay["receipt_hash"]:
                raise AssertionError("unchanged blast state must replay the real receipt")
            await self.call(client, "node_update", {**params, "status": "done", "evidence": str(blast["receipt_hash"])})
        self.commit("identity")
        self.plan()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("retries", "review", "identity"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    exercise = Exercise(args.root)
    complete = False
    try:
        asyncio.run(getattr(exercise, args.case)())
        complete = True
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"case": args.case, "complete": complete, "events": exercise.events},
                                         ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
