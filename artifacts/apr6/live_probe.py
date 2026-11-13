"""Evaluate production static permission rules in the installed OpenCode server."""

import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.infrastructure.agents.opencode.launcher import _config

CLI = "/home/sas/.containers/containers/agent-opencode/.npm-global/bin/opencode"
OUT = Path(__file__).resolve().parent / "live"
OUT.mkdir(exist_ok=True)


def probe(mode, dangerous):
    key = f"{mode}-{dangerous}"
    with tempfile.TemporaryDirectory(prefix="aconsole-apr6-") as directory:
        root = Path(directory)
        env = {"PATH": os.environ["PATH"], "HOME": directory, "XDG_CONFIG_HOME": str(root / "config"),
               "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"),
               "XDG_STATE_HOME": str(root / "state"), "OPENCODE_SERVER_PASSWORD": "probe-only"}
        config = json.loads(_config(8765, ApprovalPolicyChoice(
            mode=ApprovalPolicyMode(mode), dangerous=DangerousSubmode(dangerous),
        )))
        # There is no AConsole daemon in this fixture. Keep production permissions,
        # but don't attempt an unrelated MCP connection during server discovery.
        config.pop("mcp")
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        auth = "Basic " + base64.b64encode(b"opencode:probe-only").decode()

        def request(path, body=None, authenticated=True):
            headers = {"Content-Type": "application/json"}
            if authenticated:
                headers["Authorization"] = auth
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                         data=None if body is None else json.dumps(body).encode(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=8) as response:
                    raw = response.read()
                    return {"status": response.status, "body": json.loads(raw) if raw else None}
            except urllib.error.HTTPError as error:
                return {"status": error.code, "body": error.read().decode()}

        with (OUT / (key + ".log")).open("wb") as log:
            server = subprocess.Popen([CLI, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                      cwd=root, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        request("/api/config")
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("OpenCode server exited")
                        time.sleep(0.1)
                session = request("/api/session", {"location": {"directory": directory}})
                (OUT / (key + "-session.json")).write_text(json.dumps(session, indent=2) + "\n")
                session_id = session["body"]["data"]["id"]
                rows = []
                for action, resource in [("shell", "pwd"), ("shell", "touch marker"),
                                         ("shell", "rm -f marker"), ("edit", "marker"),
                                         ("mcp_unknown_execute", "*")]:
                    rows.append({"action": action, "resource": resource,
                                 "response": request(f"/api/session/{session_id}/permission",
                                                     {"action": action, "resources": [resource]})})
                pending = request(f"/api/session/{session_id}/permission")
                unauthenticated = request(f"/api/session/{session_id}/permission", authenticated=False)
                result = {"case": key, "rules": config, "decisions": rows,
                          "pending": pending, "unauthenticated": unauthenticated}
                (OUT / (key + ".json")).write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result), flush=True)
                return result
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    runs = [probe(mode, dangerous) for mode, dangerous in [
        ("ask", "ask"), ("allow", "ask"), ("allow", "allow"), ("deny", "ask"), ("scenario", "ask"),
    ]]
    (OUT / "results.json").write_text(json.dumps(runs, indent=2) + "\n")
