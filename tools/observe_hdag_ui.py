"""Keep a native five-tab UI workload alive for isolated direction-gate probes.

The window uses demo data to avoid starting extra model sessions. A separate
read-only UI connection observes the real candidate daemon and registers this
actual GTK process through SO_PEERCRED. This is a component memory workload,
not a claim that the demo core is the production core or a full GUI acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from aconsole.core.infrastructure.process_metrics import memory_sample, process_identity
from aconsole.ui.bootstrap import run_app, spawn_task
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.views.app import AConsoleApp


class Probe(AConsoleApp):
    def __init__(self, root: Path, socket: str) -> None:
        # The checkout path can exceed AF_UNIX's 108-byte address limit.
        demo_socket = str(Path(socket).with_name("ui-demo.sock"))
        super().__init__(CoreClient(demo_socket), scenario="demo")
        self.root: Path = root
        self.observer: CoreClient = CoreClient(socket)
        self.error: Exception | None = None

    def do_activate(self) -> None:
        super().do_activate()
        spawn_task(self.measure())

    async def measure(self) -> None:
        try:
            async with asyncio.timeout(30):
                while self.window is None:
                    if self.failed:
                        raise RuntimeError("native UI failed to open")  # noqa: TRY301 -- report GTK task failure
                    await asyncio.sleep(.05)
            snapshot = self.client.store.snapshot
            if snapshot is None:
                raise RuntimeError("native UI has no snapshot")  # noqa: TRY301 -- report GTK task failure
            for tab in snapshot.tabs:
                self.window._select_tab(tab.id)  # noqa: SLF001 -- this probe owns the demo window
            await self.observer.connect()
            await self.observer.pull_snapshot()
            await asyncio.sleep(1)
            identity = process_identity(os.getpid())
            observed = self.observer.store.snapshot
            ready = {"ui_pid": os.getpid(), "process": asdict(identity), **memory_sample(identity),
                "window_tabs": len(snapshot.tabs), "candidate_tabs": 0 if observed is None else len(observed.tabs),
                "workload": "native AConsoleApp demo window with five allocated terminals; readonly candidate observer"}
            (self.root / "ui-ready.json").write_text(json.dumps(ready, indent=2) + "\n")
            disconnected = False
            while not (self.root / "ui-stop").exists():
                if not disconnected and (self.root / "ui-disconnect").exists():
                    await self.observer.close()
                    disconnected = True
                    (self.root / "ui-observer-closed").write_text(str(os.getpid()))
                await asyncio.sleep(.2)
        except Exception as error:  # noqa: BLE001 -- propagate the asynchronous task failure after GTK exits
            self.error = error
        finally:
            await self.observer.close()
            await self._finish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    state = json.loads((root / "state.json").read_text())
    app = Probe(root, state["socket"])
    status = run_app(lambda: app, ["aconsole-acceptance-ui"])
    if app.error is not None:
        raise app.error
    raise SystemExit(status)


if __name__ == "__main__":
    main()
