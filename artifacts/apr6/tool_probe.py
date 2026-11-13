"""Real OpenCode model-tool path, isolated HOME and local Messages fixture."""

import json
import os
import signal
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.infrastructure.agents.opencode.launcher import _config

CLI = "/home/sas/.containers/containers/agent-opencode/.npm-global/bin/opencode"
OUT = Path(__file__).resolve().parent / "tools"
OUT.mkdir(exist_ok=True)


def probe(mode, dangerous, command="touch marker"):
    key = f"{mode}-{dangerous}" + ("-safe" if command == "pwd" else "")
    requests = []
    with tempfile.TemporaryDirectory(prefix="aconsole-apr6-tool-") as directory:
        root = Path(directory)
        marker = root / "marker"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "count_tokens" in self.path:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":1}')
                    return
                requests.append({"path": self.path, "tools": body.get("tools", [])})
                used = any(isinstance(msg.get("content"), list) and
                           any(block.get("type") == "tool_result" for block in msg["content"])
                           for msg in body.get("messages", []))
                content = ({"type": "text", "text": "Probe complete."} if used else
                           {"type": "tool_use", "id": "toolu_probe", "name": "shell",
                            "input": {"command": command, "description": "Disposable permission probe"}})
                message = {"id": "msg_probe", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6",
                           "content": [content], "stop_reason": "end_turn" if used else "tool_use",
                           "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
                self.end_headers()
                if not body.get("stream"):
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

        model = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        config = json.loads(_config(8765, ApprovalPolicyChoice(
            mode=ApprovalPolicyMode(mode), dangerous=DangerousSubmode(dangerous))))
        config.pop("mcp")
        config.update({"update": "disable", "model": "anthropic/claude-sonnet-4-6", "providers": {
            "anthropic": {"settings": {"baseURL": f"http://127.0.0.1:{model.server_port}/v1",
                                       "apiKey": "probe-only"}}}})
        env = {"PATH": os.environ["PATH"], "HOME": directory, "XDG_CONFIG_HOME": str(root / "config"),
               "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"),
               "XDG_STATE_HOME": str(root / "state"), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
               "ANTHROPIC_API_KEY": "probe-only"}
        (OUT / (key + "-config.json")).write_text(json.dumps(config, indent=2) + "\n")
        process = subprocess.Popen([CLI, "run", "--standalone", "--format", "json", "Run the probe once."],
                                   cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            try:
                stdout, stderr = process.communicate(timeout=30)
                status = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                status = 124
            (OUT / (key + ".stdout")).write_bytes(stdout)
            (OUT / (key + ".stderr")).write_bytes(stderr)
            result = {"case": key, "exit": status, "executed": marker.exists(), "requests": requests}
            (OUT / (key + ".json")).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps({k: v for k, v in result.items() if k != "requests"},
                             ensure_ascii=False), "requests", len(requests), flush=True)
            return result
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            model.shutdown()
            model.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    results = [probe(mode, dangerous) for mode, dangerous in [
        ("allow", "allow"), ("deny", "ask"), ("allow", "ask"), ("ask", "ask"),
    ]]
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
