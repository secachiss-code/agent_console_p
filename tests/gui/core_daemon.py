"""Run the real integration daemon without discovering the developer's containers or accounts."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aconsole.ui.client.daemon import core_python


def core_command(socket: Path) -> list[str]:
    return [str(core_python()), "-m", "tests.gui.core_daemon", str(socket)]


def _serve(socket: str) -> int:
    from aconsole.core.bootstrap import Daemon, run_with_signals
    from aconsole.core.infrastructure.persistence.startup import default_database
    from aconsole.core.infrastructure.tabs import HostBins, HostTabs

    host = HostTabs(bins=HostBins(podman=Path("/usr/bin/false")), socket_path=socket)
    daemon = Daemon(Path(socket), database=default_database(), host=host)
    daemon.serve_ingest = True
    daemon.vault_default = Path(os.environ["XDG_DATA_HOME"]) / "vault"
    return asyncio.run(run_with_signals(daemon))


if __name__ == "__main__":
    raise SystemExit(_serve(sys.argv[1]))
