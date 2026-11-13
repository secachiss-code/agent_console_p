"""Bounded Cursor CLI probes in an isolated home; never read user credentials."""

import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path

CLI = "/home/sas/.containers/containers/agent-cursor/.local/bin/agent"
OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="aconsole-apr8-") as folder:
    root = Path(folder)
    home = root / "home"
    home.mkdir()
    work = root / "work"
    work.mkdir()
    cursor = home / ".cursor"
    cursor.mkdir()
    events = root / "events.jsonl"
    hook = root / "probe-hook"
    hook.write_text(f'#!/bin/sh\ncat >> {events}\nprintf "\\n" >> {events}\n'
                    'printf \'{"permission":"deny","decision":"deny","user_message":"APR8 probe"}\\n\'\n')
    hook.chmod(0o700)
    hooks = {name: [{"command": str(hook), "failClosed": True}] for name in [
        "workspaceOpen", "sessionStart", "beforeSubmitPrompt", "preToolUse", "beforeShellExecution",
    ]}
    (cursor / "hooks.json").write_text(json.dumps({"version": 1, "hooks": hooks}))
    (cursor / "cli-config.json").write_text(json.dumps({"permissions": {
        "allow": [], "deny": ["Shell(*)", "Read(*)", "Write(*)", "WebFetch(*)", "Mcp(*:*)"]}}))
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "XDG_CONFIG_HOME": str(root / "config"),
           "XDG_CACHE_HOME": str(root / "cache"), "XDG_DATA_HOME": str(root / "data"),
           "AGENT_CLI_CREDENTIAL_STORE": "file", "LANG": "C.UTF-8"}
    results = []
    for name, argv, stdin in [
        ("version", ["--version"], b""), ("help", ["--help"], b""),
        ("print-hook", ["-p", "Use the shell tool once to create marker in this directory, then stop.",
                        "--output-format", "stream-json"], b""),
        ("interactive-hook", [], b"Use the shell tool once to create marker in this directory, then stop.\n"),
    ]:
        process = subprocess.Popen([CLI, *argv], cwd=work, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = process.communicate(stdin, timeout=20)
            status = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            status = 124
        (OUT / (name + ".stdout")).write_bytes(stdout)
        (OUT / (name + ".stderr")).write_bytes(stderr)
        result = {"case": name, "argv": argv, "exit": status, "marker": (work / "marker").exists(),
                  "events": events.read_text() if events.exists() else ""}
        results.append(result)
        (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(name, "exit", status, "hook bytes", len(result["events"]), flush=True)
