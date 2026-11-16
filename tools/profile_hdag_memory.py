"""Repeatable isolated core/UI memory profiles. Never connects to the user's core.

Run with PYTHONPATH=src and the appropriate core/UI Python environment.
UI uses contract demo data and inert launch specs; core uses real SQLite and
five restored, inactive tabs. These are component baselines, not live-agent acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

lazy import msgspec

import aconsole
from aconsole.core.infrastructure.process_metrics import memory_sample, process_identity
lazy from aconsole.contracts.app import CloseTabParams
lazy from aconsole.contracts.common import TabId
lazy from aconsole.core.application.ports.tab_store import StoredTab
lazy from aconsole.core.bootstrap import Daemon
lazy from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
lazy from aconsole.ui.bootstrap import run_app, spawn_task
lazy from aconsole.ui.client.core_client import CoreClient
lazy from aconsole.ui.views.app import AConsoleApp


def sample(phase: str, tabs: int) -> dict[str, object]:
    identity = process_identity(os.getpid())
    objects = Counter(f"{type(item).__module__}.{type(item).__qualname__}" for item in gc.get_objects())
    return {"phase": phase, "tabs": tabs, "time": time.monotonic(), "process": asdict(identity),
            **memory_sample(identity), "tracked_objects": sum(objects.values()),
            "top_object_types": objects.most_common(15),
            "retained_terminal_pages": sum(type(item).__qualname__ == "TerminalPage" for item in gc.get_objects()),
            "aconsole_objects": {key: value for key, value in objects.items() if key.startswith("aconsole.")}}


async def core_profile(rows: list[dict[str, object]], root: Path, cycles: int, settle: float) -> None:

    rows.append(sample("probe-started", 0))
    # Database, sockets, cache and settings are all disposable. No installed core is restarted.
    os.environ["XDG_RUNTIME_DIR"] = str(root / "runtime")
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    daemon = Daemon(root / "core.sock", idle_s=3600, database=root / "aconsole.db")
    task = asyncio.create_task(daemon.serve())
    try:
        async with asyncio.timeout(30):
            while not daemon.socket.exists():
                if task.done():
                    await task
                    raise RuntimeError("core exited before listening")
                await asyncio.sleep(.05)
        writer = daemon._writer  # noqa: SLF001 -- this tool owns the isolated daemon
        if writer is None:
            raise RuntimeError("profile core has no database")
        store = TabDatabase(writer)
        await asyncio.sleep(settle)
        rows.append(sample("started", 0))
        now = datetime.now(UTC)
        for cycle in range(cycles):
            for count in range(1, 6):
                store.save(StoredTab(id=f"profile-{cycle}-{count}", project_id="profile", project_name="Profile",
                    project_root=str(root), container="profile-inactive", agent="codex", title=f"Tab {count}",
                    order=count, status="idle", status_detail=None, since=now, created_at=now,
                    worktree=str(root), branch=None, base_branch=None, dirty=False, workspace_mode="main_checkout",
                    plan_node=None, session=None))
                await asyncio.to_thread(daemon.tabs.reload)
                if count in {1, 5}:
                    await asyncio.sleep(settle)
                    rows.append(sample(f"cycle-{cycle + 1}-open", len(daemon.tabs.snapshot().tabs)))
            for count in range(1, 6):
                await asyncio.to_thread(daemon.tabs.close_tab, CloseTabParams(tab_id=TabId(f"profile-{cycle}-{count}")))
            await asyncio.to_thread(daemon.tabs.reload)
            await asyncio.sleep(settle)
            gc.collect()
            rows.append(sample(f"cycle-{cycle + 1}-closed", len(daemon.tabs.snapshot().tabs)))
    finally:
        daemon.request_stop()
        await task


def ui_profile(rows: list[dict[str, object]], root: Path, cycles: int, settle: float) -> None:

    # Keep the recording core in another process: importing its dependencies into UI distorts RSS.
    core = subprocess.Popen([sys.executable, "-c",
        ("import signal,threading; from dataclasses import replace; "
        "from aconsole.core.infrastructure.fake.server import FakeCore; "
        "import sys; c=FakeCore(sys.argv[1], 'demo'); c.script=replace(c.script, events=()); "
        "c.start(); done=threading.Event(); signal.signal(signal.SIGTERM, lambda *_: done.set()); "
        "done.wait(); c.stop()"), str(root / "core.sock")])
    deadline = time.monotonic() + 20
    while not (root / "core.sock").exists():
        if core.poll() is not None or time.monotonic() > deadline:
            core.terminate()
            core.wait()
            raise RuntimeError("profile fake core did not start")
        time.sleep(.05)

    class Probe(AConsoleApp):
        profile_error: Exception | None = None

        def do_activate(self) -> None:
            super().do_activate()
            spawn_task(self.measure())

        async def measure(self) -> None:
            try:
                async with asyncio.timeout(60):
                    while self.window is None:
                        if self.failed:
                            raise RuntimeError("UI failed to open")  # noqa: TRY301 -- report the async probe failure
                        await asyncio.sleep(.05)
                    window = self.window
                    snapshot = self.client.store.snapshot
                    if snapshot is None:
                        raise RuntimeError("UI profile has no snapshot")  # noqa: TRY301 -- report the async probe failure
                    for cycle in range(cycles):
                        for count in (0, 1, 5, 0):
                            self.client.store.apply_snapshot(
                                msgspec.structs.replace(snapshot, tabs=snapshot.tabs[:count]), 0)
                            for tab in snapshot.tabs[:count]:
                                window._select_tab(tab.id)  # noqa: SLF001 -- exercise this probe's window
                            await asyncio.sleep(settle)
                            gc.collect()
                            rows.append(sample(f"cycle-{cycle + 1}-tabs-{count}", count))
            except Exception as error:  # noqa: BLE001 -- propagate the GTK task failure after app.run
                self.profile_error = error
            finally:
                await self._finish()

    try:
        app = Probe(CoreClient(str(root / "core.sock")))
        status = run_app(lambda: app, ["aconsole-memory-profile"])
        if app.profile_error is not None:
            raise app.profile_error
        if status or not rows:
            raise RuntimeError("UI profile did not complete")
    finally:
        core.terminate()
        core.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("core", "ui"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--settle", type=float, default=2)
    args = parser.parse_args()
    if args.cycles < 1 or args.settle <= 0:
        parser.error("cycles and settle must be positive")
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ac-mem-", dir="/tmp") as folder:
        if args.component == "core":
            asyncio.run(core_profile(rows, Path(folder), args.cycles, args.settle))
        else:
            ui_profile(rows, Path(folder), args.cycles, args.settle)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    digest = hashlib.sha256()
    package = Path(aconsole.__file__).parent
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package.parent)).encode())
        digest.update(path.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"component": args.component, "revision": revision,
        "source_sha256": digest.hexdigest(), "complete": True,
        "python": sys.version, "renderer": os.environ.get("GSK_RENDERER", "default"),
        "scenario": "isolated component baseline; inactive/demo tabs, no live agents",
        "samples": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
