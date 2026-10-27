"""Record Claude HTTP hook bodies. Observational hooks return {}. PermissionRequest is held then denied."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOLD_S = float(os.environ.get("ACONSOLE_HOLD_S", "20"))
OUT = Path(os.environ.get("ACONSOLE_HOOK_OUT", "tests/fixtures/claude/2.1.309"))
PORT = int(os.environ.get("ACONSOLE_PORT", "8765"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        OUT.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(raw.decode())
            event_name = str(payload.get("hook_event_name", "unknown"))
            name = "statusline" if "statusline" in self.path else event_name
        except json.JSONDecodeError:
            name = "invalid"
            payload = {"raw": raw.decode(errors="replace")}
        stamp = str(int(time.time() * 1000))
        path = OUT / f"{name}-{stamp}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        sys_auth = self.headers.get("Authorization", "")
        (OUT / f"{name}.headers").write_text(f"path {self.path}\nauth {sys_auth}\n", encoding="utf-8")
        body = b"{}"
        if name == "PermissionRequest":
            time.sleep(HOLD_S)
            body = json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "deny", "message": "Отклонено в AConsole"},
                    }
                }
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, template: str, *args: object) -> None:
        _ = (template, args)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    import time

    time.sleep(300)
    server.shutdown()


if __name__ == "__main__":
    main()
