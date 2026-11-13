"""Bounded isolated discovery of the installed OpenCode v2 interface."""

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CLI = "/home/sas/.containers/containers/agent-opencode/.npm-global/bin/opencode"
OUT = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="aconsole-apr6-inspect-") as folder:
    root = Path(folder)
    env = {"PATH": os.environ["PATH"], "HOME": folder, "XDG_CONFIG_HOME": str(root / "config"),
           "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"),
           "XDG_STATE_HOME": str(root / "state"), "OPENCODE_SERVER_PASSWORD": "probe-only"}
    for name, args in [("version", ["--version"]), ("run-help", ["run", "--help"]),
                       ("serve-help", ["serve", "--help"]), ("debug-help", ["debug", "--help"])]:
        result = subprocess.run([CLI, *args], cwd=root, env=env, capture_output=True, timeout=20)
        (OUT / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        print(name, result.returncode, flush=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (OUT / "inspect-server.log").open("wb") as log:
        process = subprocess.Popen([CLI, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                   cwd=root, env=env, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    response = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5)
                    response.close()
                    break
                except urllib.error.HTTPError:
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
            observations = []
            import base64
            auth = "Basic " + base64.b64encode(b"opencode:probe-only").decode()
            for path in ["/doc", "/openapi.json", "/api/doc", "/api/openapi.json", "/api/config", "/config"]:
                request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": auth})
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        body = response.read()
                        observations.append({"path": path, "status": response.status, "bytes": len(body)})
                        (OUT / (path.strip("/").replace("/", "-") + ".body")).write_bytes(body)
                except (OSError, urllib.error.URLError) as error:
                    observations.append({"path": path, "error": str(error)})
            (OUT / "inspection.json").write_text(json.dumps(observations, indent=2) + "\n")
            print(json.dumps(observations), flush=True)
        finally:
            process.terminate()
            process.wait(timeout=10)
