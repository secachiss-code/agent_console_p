"""Prepare and serve a disposable H-DAG project with the real daemon and stores.

The server must run on the host alongside Podman and the native UI. Its IPC
socket, database, token file and Git repository are separate from the user's app.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shlex
import signal
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import msgspec

from aconsole.core.application.ports.tab_store import StoredTab, TabDirectory
from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.tabs import HostBins, HostTabs

_PROJECT = "hdag-acceptance"
_MODEL = "gpt-6.1-sol"


def write_json(path: Path, value: object, *, private: bool = False) -> None:
    if private:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_state(root: Path) -> dict[str, object]:
    return msgspec.json.decode((root / "state.json").read_bytes(), type=dict[str, object])


def pin_environment(root: Path, image: str) -> None:
    state = read_state(root)
    engine = write_engine(f"sqlite:///{state['database']}")
    writer = Writer(engine)
    writer.start()
    try:
        store = PlanDatabase(writer)
        plan = next(item for item in store.load() if item.id == state["plan"])
        if plan.topology is None or plan.topology.phase != "draft":
            raise ValueError("only a draft may receive its initial environment")
        nodes = []
        for original in plan.nodes:
            node = original
            if node.level == 0:
                profile = dict(node.gate_profile or {})
                profile["environment"] = {"container": state["gate"], "image": image}
                node = replace(node, gate_profile=profile)
            nodes.append(node)
        store.save(replace(plan, nodes=tuple(nodes)))
        state["image"] = image
        write_json(root / "state.json", state)
    finally:
        writer.stop()
        engine.dispose()


def prepare(root: Path, manifest: Path, image: str, model: str) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "tests").mkdir()
    for name in ("success", "retry", "identity", "resources"):
        (workspace / f"src/{name}.py").write_text("def value() -> int:\n    return 0\n")
        (workspace / f"tests/test_{name}.py").write_text(
            f"from src.{name} import value\n\ndef test_value() -> None:\n    assert value() == 0\n")
    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.ruff_cache/\n")
    (workspace / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath=["."]\ntestpaths=["tests"]\naddopts="-q"\n'
        '[tool.basedpyright]\npythonVersion="3.15"\ntypeCheckingMode="strict"\ninclude=["src"]\n'
        '[tool.ruff]\ntarget-version="py315"\nline-length=120\n[tool.ruff.lint]\nselect=["E","F"]\n')
    for args in (("init", "-q", "-b", "main"), ("config", "user.name", "AConsole acceptance"),
                 ("config", "user.email", "acceptance@localhost"), ("add", "."), ("commit", "-qm", "Fixture baseline")):
        subprocess.run(["git", "-C", str(workspace), *args], check=True)
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    executor, gate = f"aconsole-acc-exec-{suffix}", f"aconsole-acc-gate-{suffix}"
    payload = msgspec.json.decode(manifest.read_bytes(), type=dict[str, object])
    nodes = [NodeIn.model_validate(value) for value in payload["nodes"]]
    direction = nodes[0]
    profile = dict(direction.gate_profile or {})
    profile["environment"] = {"container": gate, "image": image}
    # ACC-4 proves measurement and fail-closed behavior. The original memory
    # limits remain required for closing L0; they are not an impossible precondition
    # for invoking the L0 gate whose children must already be terminal.
    nodes[0] = direction.model_copy(update={"gate_profile": profile})
    nodes[4] = nodes[4].model_copy(update={"acceptance": [
        "Real core/UI metrics and five-tab count are recorded with process identities.",
        "An over-budget Tier 3 is red and cannot close L0; RSS limits remain 120/150 MiB.",
        "Logs, image identity, receipts and vertex counts survive restart."]})
    database = root / "state.db"
    migrate(f"sqlite:///{database}", "head")
    engine = write_engine(f"sqlite:///{database}")
    writer = Writer(engine)
    writer.start()
    now = datetime.now(UTC)
    try:
        store = TabDatabase(writer)
        store.save_settings(TabDirectory(worktrees_dir=None, rev=1, setup_completed=True,
                                        preferences={"codegraph_languages": ["python"]}))
        for number in range(5):
            store.save(StoredTab(id=f"acc-{number}", project_id=_PROJECT, project_name="H-DAG acceptance",
                project_root=str(workspace), container=executor, agent="codex", title=f"Acceptance {number}",
                order=number, status="idle", status_detail=None, since=now, created_at=now,
                worktree=str(workspace), branch="main", base_branch="main", dirty=False,
                workspace_mode="main_checkout", plan_node=None, session=None,
                mode="plan" if number == 0 else "execute", model=model))
        board = PlanBoard(checked_completion=True)
        board.use_store(PlanDatabase(writer))
        created = board.create(TabContext(tab_id="acc-0", project_id=_PROJECT, worktree=workspace,
                                         agent="codex", mode="plan"),
                               str(payload["title"]), str(payload["goal"]), nodes)
        stored = board.export_plan(created.plan_id)
        socket = f"/tmp/ac-hdag-{suffix}/core.sock"  # noqa: S108 -- isolated private runtime directory
        write_json(root / "state.json", {"workspace": str(workspace), "database": str(database),
            "socket": socket, "project": _PROJECT, "plan": created.plan_id,
            "nodes": {node.key: node.id for node in stored.nodes}, "executor": executor,
            "gate": gate, "image": image, "model": model, "phase": "draft"})
        write_json(root / "admitted-draft.json", msgspec.to_builtins(stored))
        write_json(root / "private.json", {f"acc-{number}": secrets.token_urlsafe(32) for number in range(5)},
                   private=True)
    finally:
        writer.stop()
        engine.dispose()


async def serve(root: Path) -> None:
    state = read_state(root)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    podman = root / "native-podman"
    podman.write_text("#!/bin/sh\nexport XDG_RUNTIME_DIR=" + shlex.quote(runtime)
                      + "\nexport DBUS_SESSION_BUS_ADDRESS=" + shlex.quote(bus)
                      + '\nexec /usr/bin/podman "$@"\n')
    podman.chmod(0o700)
    socket = Path(str(state["socket"]))
    socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket.parent.chmod(0o700)
    os.environ["XDG_RUNTIME_DIR"] = str(socket.parent)
    os.environ["XDG_DATA_HOME"] = str(root / "xdg-data")
    os.environ["XDG_STATE_HOME"] = str(root / "xdg-state")
    host = HostTabs(bins=HostBins(podman=podman), socket_path=str(socket))
    daemon = Daemon(socket, database=Path(str(state["database"])), idle_s=3600, host=host)
    daemon.serve_ingest = True
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, daemon.request_stop)
    task = asyncio.create_task(daemon.serve())
    try:
        async with asyncio.timeout(60):
            while not await asyncio.to_thread(socket.exists) or daemon.ingest_port is None:
                if task.done():
                    await task
                    raise RuntimeError("acceptance daemon stopped before readiness")
                await asyncio.sleep(.05)
        private = msgspec.json.decode((root / "private.json").read_bytes(), type=dict[str, str])
        for tab in daemon.tabs.snapshot().tabs:
            daemon._register_tab(tab, private[str(tab.id)])  # noqa: SLF001 -- this tool owns this isolated daemon
        write_json(root / "ready.json", {"pid": os.getpid(), "port": daemon.ingest_port, "socket": str(socket)})
        await task
    finally:
        daemon.request_stop()
        if not task.done():
            await task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "pin", "serve"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--model", default=_MODEL)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        if args.manifest is None or args.image is None:
            parser.error("prepare requires --manifest and --image")
        prepare(root, args.manifest, args.image, args.model)
    elif args.action == "pin":
        if args.image is None:
            parser.error("pin requires --image")
        pin_environment(root, args.image)
    else:
        asyncio.run(serve(root))


if __name__ == "__main__":
    main()
