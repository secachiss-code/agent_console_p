# ruff: noqa: ANN001, ANN201, INP001, PTH115, S106, T201
# Standalone evidence runner; dynamic protocol fixtures and bounded subprocesses are intentional.
"""Read-only effect probe: denied IPC identities and host file write capability.

No SQL, no file bytes read/written, no valid approval id/nonce, and no mutations.
O_RDWR descriptors are immediately closed; their successful opening is recorded.
"""

import json
import os
import socket
from pathlib import Path

OUT = Path(__file__).resolve().parent / "security.json"
SOCKET = "/run/user/1000/aconsole/core.sock"
result = {"self_mount_namespace": os.readlink("/proc/self/ns/mnt"), "ipc": [], "files": []}


def role_probe(role):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(SOCKET)
        params = {"protocol": [{"major": 1, "minor": 0}, {"major": 1, "minor": 0}],
                  "role": role, "client_name": "aconsole-qa6-negative-probe", "client_version": "0.1",
                  "pid": os.getpid(), "capabilities": []}
        if role == "sidecar":
            params.update(tab_id="qa6-nonexistent-tab", sidecar_token="qa6-invalid-token")
        stream = client.makefile("rwb", buffering=0)
        stream.write(json.dumps({"kind": "request", "jsonrpc": "2.0", "id": 1,
                                 "method": "initialize", "params": params}).encode() + b"\n")
        response = json.loads(stream.readline())
        row = {"role": role, "initialize": response.get("error", {"unexpected_accept": True}), "methods": []}
        # A successful host-role impersonation is already a failure. Never subscribe
        # to nonce-bearing events or exercise a mutation after such a result.
        if "error" in response:
            for identity, method in enumerate(["settings/update", "project/setApprovalPolicy", "approval/decide"], 2):
                stream.write(json.dumps({"kind": "request", "jsonrpc": "2.0", "id": identity, "method": method,
                                         "params": {}}).encode() + b"\n")
                reply = json.loads(stream.readline())
                row["methods"].append({"method": method, "error": reply.get("error")})
        return row


for role in ("ui", "cli", "sidecar"):
    try:
        result["ipc"].append(role_probe(role))
    except (OSError, ValueError) as error:
        result["ipc"].append({"role": role, "probe_error": type(error).__name__, "detail": str(error)})

for name in ["/home/sas/.local/share/aconsole/aconsole.db", "/home/sas/.bashrc"]:
    path = Path(name)
    row = {"path": name}
    try:
        before = path.stat()
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            row.update(read_write_open=False, error=type(error).__name__, errno=error.errno)
        else:
            os.close(descriptor)
            row["read_write_open"] = True
        after = path.stat()
        row.update(size_before=before.st_size, size_after=after.st_size,
                   mtime_before=before.st_mtime_ns, mtime_after=after.st_mtime_ns,
                   bytes_changed_by_probe=False)
    except OSError as error:
        row.update(probe_error=type(error).__name__, errno=error.errno)
    result["files"].append(row)

OUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
