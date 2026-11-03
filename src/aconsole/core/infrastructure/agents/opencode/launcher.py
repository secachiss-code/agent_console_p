"""OpenCode runs as `opencode serve` plus `opencode attach`, so the core can read the server's event stream.

Nothing is written to the agent's home: the MCP entry travels in `OPENCODE_CONFIG_CONTENT`. The tab token is put
into that value inside the container, so it never appears on a command line. The same token is the server password.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Final, final

_HOST: Final = "127.0.0.1"
_MARK: Final = "@ACONSOLE_TOKEN@"
# `$1` config, `$2` server port, the rest goes to `opencode attach`: `-s <session>`.
# The server dies with the terminal: `podman exec` would leave it running, and nothing else knows its port.
_SCRIPT: Final = r"""
config=$1
port=$2
shift 2
if ! command -v opencode >/dev/null 2>&1; then
    echo "aconsole: opencode is not installed in this container. Looked in: $PATH" >&2
    exit 127
fi
export OPENCODE_CONFIG_CONTENT="${config%%@ACONSOLE_TOKEN@*}$ACONSOLE_TAB_TOKEN${config#*@ACONSOLE_TOKEN@}"
export OPENCODE_SERVER_PASSWORD="$ACONSOLE_TAB_TOKEN"
url="http://127.0.0.1:$port"
opencode serve --hostname 127.0.0.1 --port "$port" </dev/null >/dev/null 2>&1 &
server=$!
stop() { kill "$server" 2>/dev/null; }
trap stop EXIT
trap 'stop; exit 129' HUP
trap 'stop; exit 143' TERM
tries=0
while [ "$tries" -lt 100 ] && ! curl -s -o /dev/null -m 1 "$url/"; do
    kill -0 "$server" 2>/dev/null || break
    sleep 0.1
    tries=$((tries + 1))
done
opencode attach "$url" "$@"
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCodeLaunch:
    """`argv` takes `-s <session>` at its end. `server_port` is where the event stream will be."""

    argv: tuple[str, ...]
    server_port: int


def free_port() -> int:
    """A port free right now. The server binds it a moment later, in a container that shares the host's network."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((_HOST, 0))
        port: int = probe.getsockname()[1]  # pyright: ignore[reportAny] -- AF_INET address is (host, port)
        return port


@final
class OpenCodeLauncher:
    """Build the command. It starts nothing."""

    def launch(self, ingest_port: int) -> OpenCodeLaunch:
        port = free_port()
        return OpenCodeLaunch(
            argv=("/bin/sh", "-c", _SCRIPT, "aconsole-opencode", _config(ingest_port), str(port)),
            server_port=port,
        )


def _config(ingest_port: int) -> str:
    server = {
        "type": "remote",
        "url": f"http://{_HOST}:{ingest_port}/mcp",
        "enabled": True,
        "oauth": False,
        "headers": {"Authorization": f"Bearer {_MARK}"},
    }
    return json.dumps({"mcp": {"aconsole": server}}, separators=(",", ":"))
