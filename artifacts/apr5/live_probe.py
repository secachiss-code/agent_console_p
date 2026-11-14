# ruff: noqa: ANN002, ANN202, C901, E501, FBT001, FBT002, INP001, PLW1510, S106, S603, T201
# Standalone native CLI evidence fixture; dynamic JSON and process calls are intentional.
"""Exercise installed Claude with a local deterministic Messages endpoint and real hooks.

No external model, user account or user configuration is used. This verifies the CLI
permission engine, not provider availability or an interactive human's TUI response.
"""

import asyncio
import json
import os
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.domain.approvals import ApprovalBook
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.routes import IngestItem

CLI = "/home/sas/.containers/containers/agent-claude/.npm-global/bin/claude"
OUT = Path(__file__).resolve().parent / "live"


def probe(mode: str, dangerous: str, risky: bool, offline: bool = False) -> dict:
    choice = ApprovalPolicyChoice(mode=ApprovalPolicyMode(mode), dangerous=DangerousSubmode(dangerous))
    calls = []
    audits = []
    with tempfile.TemporaryDirectory(prefix="aconsole-apr5-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        work = root / "work"
        work.mkdir()
        marker = work / "marker"
        if risky:
            marker.write_text("disposable test file")
        command = "rm -f marker" if risky else "touch marker"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if "/claude/hook" in self.path:
                    event = json.loads(body)
                    calls.append(event)
                    if offline:
                        self.send_error(503)
                        return
                    if event["hook_event_name"] in {"PreToolUse", "PermissionRequest"}:
                        hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None,
                                            notify=lambda _approval: None, audit=audits.append,
                                            now=lambda: datetime.now(UTC))
                        reply = asyncio.run(handle_permission(
                            IngestItem(tab_id="probe", kind="claude.hook", body=body), hold,
                            hold_seconds=30, worktree=str(work), choice=choice, inline=False,
                        )).body
                    else:
                        reply = b"{}"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(reply)
                    return
                if "/messages" not in self.path:
                    self.send_error(404)
                    return
                request = json.loads(body)
                used = any(isinstance(msg.get("content"), list) and
                           any(block.get("type") == "tool_result" for block in msg["content"])
                           for msg in request.get("messages", []))
                content = ({"type": "text", "text": "Probe complete."} if used else
                           {"type": "tool_use", "id": "toolu_apr5", "name": "Bash", "input": {"command": command}})
                message = {"id": "msg_probe", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6",
                           "content": [content], "stop_reason": "end_turn" if used else "tool_use",
                           "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if request.get("stream") else "application/json")
                self.end_headers()
                if not request.get("stream"):
                    self.wfile.write(json.dumps(message).encode())
                    return
                events = [
                    ("message_start", {"type": "message_start", "message": dict(message, content=[], stop_reason=None)}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                              "content_block": content if used else dict(content, input={})}),
                ]
                if not used:
                    events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "input_json_delta", "partial_json": json.dumps(content["input"])}}))
                events.extend([
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"],
                        "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                    ("message_stop", {"type": "message_stop"}),
                ])
                for name, event in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(event)}\n\n".encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        launch = ClaudeLauncher().launch(ClaudeTab(agent_home=home, tab_id="probe", port=port,
                                                   token="probe-only-token", policy=choice))
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "LANG": "C.UTF-8",
               "CLAUDE_CONFIG_DIR": str(home / ".claude"), "ANTHROPIC_API_KEY": "probe-only-key",
               "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
               **launch.env}
        argv = [CLI, "-p", "Run the requested probe once.", "--settings", str(launch.settings_path),
                "--model", "claude-sonnet-4-6", "--output-format", "stream-json", "--verbose", "--max-turns", "3"]
        key = f"{mode}-{dangerous}-{'risky' if risky else 'plain'}{'-offline' if offline else ''}"
        try:
            result = subprocess.run(argv, cwd=work, env=env, capture_output=True, timeout=45)
            (OUT / f"{key}.stdout").write_bytes(result.stdout)
            (OUT / f"{key}.stderr").write_bytes(result.stderr)
            executed = not marker.exists() if risky else marker.exists()
            return {"case": key, "exit": result.returncode, "executed": executed,
                    "events": [event["hook_event_name"] for event in calls],
                    "audit": [{"phase": row.phase.value, "by": row.by.value} for row in audits]}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    results = {"version": subprocess.check_output([CLI, "--version"], text=True).strip(), "runs": []}
    for mode, dangerous, risky, offline in [
        ("allow", "ask", False, False), ("allow", "ask", True, False),
        ("allow", "allow", True, False), ("deny", "ask", False, False),
        ("deny", "ask", False, True), ("ask", "ask", False, False),
        ("scenario", "ask", False, False), ("allow", "allow", False, True),
    ]:
        row = probe(mode, dangerous, risky, offline)
        results["runs"].append(row)
        (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(row), flush=True)
