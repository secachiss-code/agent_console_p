# ruff: noqa: ANN001, ANN002, ANN201, ANN202, C901, E501, EM101, FBT002, I001, INP001, ISC004, PLC0415, PLR0912, PLR0913, PLR0915, PLR0917, PLR2004, Q000, S106, S603, SIM105, T201, TRY003
# Standalone native CLI evidence fixture; dynamic JSON and process calls are intentional.
"""Installed Codex hook semantics with a local Responses API and disposable files."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.domain.approvals import ApprovalBook
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.routes import IngestItem
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab

CLI = shutil.which("codex")
OUT = Path(__file__).resolve().parent / os.environ.get("APR7_OUTPUT", "live")
OUT.mkdir(parents=True, exist_ok=True)


def probe(key, approval="never", pre="allow", permission="allow", escalated=False, offline=False,
          app_server=False, damage=None, policy=None, command="touch marker"):
    hooks = []
    requests = []
    with tempfile.TemporaryDirectory(prefix="aconsole-apr7-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        codex_home = home / ".codex"
        codex_home.mkdir()
        work = root / "work"
        work.mkdir()
        marker = work / "marker"
        if command.startswith("rm "):
            marker.write_text("disposable marker")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw)
                if "/codex/" in self.path:
                    event = body.get("hook_event_name")
                    hooks.append(body)
                    if offline and event == "PreToolUse":
                        self.send_error(503)
                        return
                    response = {}
                    if event == "PreToolUse":
                        response = {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": pre,
                                                           "permissionDecisionReason": "Synthetic permission probe"}}
                    if event == "PermissionRequest":
                        response = {"hookSpecificOutput": {"hookEventName": event,
                                                           "decision": {"behavior": permission}}}
                    if policy is not None and event in {"PreToolUse", "PermissionRequest"}:
                        import asyncio
                        from datetime import UTC, datetime

                        service = ApprovalHold(ApprovalBook(), publish=lambda _event: None,
                            notify=lambda _row: None, audit=lambda _row: None, now=lambda: datetime.now(UTC))
                        response = json.loads(asyncio.run(handle_permission(
                            IngestItem(tab_id="probe", kind="codex.hook", body=raw), service, hold_seconds=30,
                            worktree=str(work), choice=choice, inline=False)).body)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                    return
                requests.append({"path": self.path, "tools": body.get("tools", []), "input": body.get("input")})
                used = any(item.get("type") == "function_call_output" for item in body.get("input", [])
                           if isinstance(item, dict))
                args = {"cmd": command, "yield_time_ms": 1000}
                if escalated:
                    args.update(sandbox_permissions="require_escalated", justification="Synthetic test escalation")
                item = ({"id": "msg_probe", "type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": "Probe complete.", "annotations": []}]}
                        if used else {"id": "fc_probe", "type": "function_call", "call_id": "call_probe",
                                      "name": "exec_command", "arguments": json.dumps(args), "status": "completed"})
                response = {"id": "resp_probe_done" if used else "resp_probe", "object": "response",
                            "status": "completed", "output": [item], "usage": {
                                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
                events = [
                    ("response.created", {"type": "response.created", "response": dict(response, status="in_progress", output=[])}),
                    ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item}),
                    ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
                    ("response.completed", {"type": "response.completed", "response": response}),
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for name, event in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(event)}\n\n".encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(codex_home), "LANG": "C.UTF-8",
               "ACONSOLE_PORT": str(port), "ACONSOLE_TAB": "probe", "ACONSOLE_TAB_TOKEN": "probe-only"}

        listing_runs = 0

        def listing(argv):
            nonlocal listing_runs
            listing_runs += 1
            error_log = (OUT / (key + "-listing.stderr")).open("ab")
            process = subprocess.Popen([CLI, *argv[1:], "app-server"], cwd=work, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log,
                                       start_new_session=True)
            lines = []
            try:
                process.stdin.write(
                    b'{"id":1,"method":"initialize","params":{"clientInfo":{"name":"aconsole-probe","version":"0.1"},'
                    b'"capabilities":{"experimentalApi":true}}}\n'
                    b'{"method":"initialized"}\n'
                    b'{"id":2,"method":"hooks/list","params":{}}\n')
                process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    end = time.monotonic() + 10
                    while time.monotonic() < end:
                        if not selector.select(timeout=0.1):
                            continue
                        line = process.stdout.readline()
                        if not line:
                            raise RuntimeError("app-server exited before hooks/list")
                        lines.append(line)
                        if json.loads(line).get("id") == 2:
                            return line
                raise TimeoutError("hooks/list timed out")
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=5)
                error_log.close()
                (OUT / (key + f"-listing-{listing_runs}.jsonl")).write_bytes(b"".join(lines))

        launcher = CodexLauncher()
        choice = (ApprovalPolicyChoice() if policy is None else ApprovalPolicyChoice(
            mode=ApprovalPolicyMode(policy[0]), dangerous=DangerousSubmode(policy[1])))
        tab = CodexTab(agent_home=home, tab_id="probe", token="probe-only", port=port, policy=choice)
        try:
            first = launcher.probe(tab)
            checked = launcher.verified(tab, listing(first.argv))
            launch = launcher.verified(tab, listing(checked.argv), require_trusted=True)
            if damage == "missing":
                launch.hook_path.unlink()
            elif damage == "crash":
                original = launch.hook_path.with_suffix(".original")
                original.write_bytes(launch.hook_path.read_bytes())
                original.chmod(0o700)
                launch.hook_path.write_text(
                    '#!/bin/sh\nbody=$(cat)\ncase "$body" in *PreToolUse*) kill -KILL $$;; esac\n'
                    f'printf %s "$body" | {original} codex\n')
            flags = [
                "-c", 'mcp_servers.aconsole.enabled=false', "-c", 'model_provider="probe"',
                "-c", f'model_providers.probe={{name="Local probe",base_url="http://127.0.0.1:{port}/v1",'
                      'wire_api="responses",requires_openai_auth=false}',
                "-c", 'model="gpt-6.1-sol"', "-c", 'features.code_mode=false',
            ]
            if policy is None:
                flags.extend(["-c", f'approval_policy="{approval}"', "-c", 'sandbox_mode="workspace-write"'])
            argv = [CLI, *launch.argv[1:], *flags,
                    *( ["app-server"] if app_server else
                       ["exec", "--skip-git-repo-check", "--json", "Run the probe once."] )]
            process = subprocess.Popen(argv, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       stdin=subprocess.PIPE if app_server else subprocess.DEVNULL,
                                       start_new_session=True)
            try:
                if app_server:
                    transcript = []

                    def send(message):
                        process.stdin.write(json.dumps(message).encode() + b"\n")
                        process.stdin.flush()

                    send({"id": 1, "method": "initialize", "params": {
                        "clientInfo": {"name": "aconsole-probe", "version": "0.1"},
                        "capabilities": {"experimentalApi": True}}})
                    end = time.monotonic() + 30
                    pending = b""
                    completed = False
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while time.monotonic() < end and not completed:
                            if not selector.select(timeout=0.1):
                                continue
                            chunk = os.read(process.stdout.fileno(), 65536)
                            if not chunk:
                                break
                            pending += chunk
                            *lines, pending = pending.split(b"\n")
                            for line in lines:
                                transcript.append(line + b"\n")
                                message = json.loads(line)
                                if message.get("id") == 1:
                                    send({"method": "initialized"})
                                    send({"id": 2, "method": "thread/start", "params": {
                                        "cwd": str(work), "model": "gpt-6.1-sol", "modelProvider": "probe",
                                        "approvalPolicy": approval, "sandbox": "workspace-write"}})
                                elif message.get("id") == 2:
                                    if "error" in message:
                                        raise RuntimeError(json.dumps(message))
                                    send({"id": 3, "method": "turn/start", "params": {
                                        "threadId": message["result"]["thread"]["id"],
                                        "input": [{"type": "text", "text": "Run the probe once."}]}})
                                elif message.get("method") == "turn/completed":
                                    completed = True
                                elif "id" in message and "method" in message:
                                    send({"id": message["id"], "result": {"decision": "decline"}})
                    os.killpg(process.pid, signal.SIGTERM)
                    tail, stderr = process.communicate(timeout=5)
                    stdout = b"".join(transcript) + tail
                    status = 0 if completed else 124
                else:
                    stdout, stderr = process.communicate(timeout=30)
                    status = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                status = 124
            (OUT / (key + ".stdout")).write_bytes(stdout)
            (OUT / (key + ".stderr")).write_bytes(stderr)
            executed = not marker.exists() if command.startswith("rm ") else marker.exists()
            result = {"case": key, "exit": status, "executed": executed, "hooks": hooks, "requests": requests}
            (OUT / (key + ".json")).write_text(json.dumps(result, indent=2) + "\n")
            print(key, "exit", status, "executed", executed, "events",
                  [event.get("hook_event_name") for event in hooks], "requests", len(requests), flush=True)
            return result
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    results = [
        probe("never-pre-allow"), probe("never-pre-deny", pre="deny"),
        probe("never-pre-ask", pre="ask"), probe("on-request-permission-allow", approval="on-request", escalated=True),
        probe("never-permission-allow", escalated=True), probe("offline-pre-deny", pre="deny", offline=True),
    ]
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
