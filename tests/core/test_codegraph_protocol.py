"""Extractor JSON is versioned, and the runner calls `hostrun devtools` without the network."""

from __future__ import annotations

import os
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.errors import CodegraphExtractorFailedDetails
from aconsole.core.infrastructure.codegraph.protocol import (
    ExtractorGraph,
    ExtractorProtocolError,
    GraphEdge,
    GraphNode,
    decode_graph,
)
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun, ExtractorRunner

_HOSTRUN = "/home/sas/.containers/host-bins/hostrun"
_ARGV = "ACONSOLE_EXTRACTOR_ARGV"
_MODE = "ACONSOLE_EXTRACTOR_MODE"
_STDOUT = "ACONSOLE_EXTRACTOR_STDOUT"
_SCRIPT = """#!/bin/sh
printf '%s\\n' "$@" > "$ACONSOLE_EXTRACTOR_ARGV"
if [ "$ACONSOLE_EXTRACTOR_MODE" = "fail" ]; then
  printf '%s\\n' 'extractor failed' >&2
  exit 1
fi
if [ "$ACONSOLE_EXTRACTOR_MODE" = "sleep" ]; then
  sleep 5
fi
cat "$ACONSOLE_EXTRACTOR_STDOUT"
"""


def test_codegraph_protocol_schema_and_unknown_version() -> None:
    graph = ExtractorGraph(
        version=1,
        nodes=(GraphNode(id="a", path="a.py", name="a", kind="module"),),
        edges=(GraphEdge(source="a", target="b", kind="imports"),),
    )
    raw = msgspec.json.encode(graph)
    assert decode_graph(raw) == graph
    assert decode_graph(b'{"version":1,"nodes":[],"edges":[]}') == ExtractorGraph(version=1, nodes=(), edges=())
    with pytest.raises(ExtractorProtocolError, match="schema"):
        decode_graph(b'{"version":1,"edges":[]}')
    with pytest.raises(ExtractorProtocolError, match="unsupported extractor version"):
        decode_graph(b'{"version":2,"nodes":[],"edges":[]}')


def test_codegraph_protocol_runner_builds_hostrun_devtools_argv(tmp_path: Path) -> None:
    runner = ExtractorRunner()
    assert runner.argv("python", "src") == (_HOSTRUN, "devtools", "python", "src")
    assert runner.argv("ts", "web") == (_HOSTRUN, "devtools", "ts", "web")
    assert runner.argv("rust-metadata", "crates/app") == (
        _HOSTRUN,
        "devtools",
        "rust-metadata",
        "crates/app",
    )
    assert runner.argv("csharp", "src") == (_HOSTRUN, "devtools", "csharp", "src")
    seen = _run_ok(tmp_path)
    assert seen == ["devtools", "python", "pkg"]


def test_codegraph_protocol_runner_maps_failures(tmp_path: Path) -> None:
    failed = _run(tmp_path, "fail", b"")
    assert failed.graph is None
    assert failed.error is not None
    assert failed.error.code == 5006
    assert failed.error.message == "CODEGRAPH_EXTRACTOR_FAILED"
    assert isinstance(failed.error.data.details, CodegraphExtractorFailedDetails)
    assert failed.error.data.details.language == "python"
    assert failed.error.data.details.stderr_tail == "extractor failed"
    unknown = ExtractorRunner(_stand_in(tmp_path)).run("ruby", "pkg")
    assert unknown.error is not None
    assert unknown.error.data is not None
    assert isinstance(unknown.error.data.details, CodegraphExtractorFailedDetails)
    assert unknown.error.data.details.stderr_tail == "unknown extractor language"
    outside = ExtractorRunner(_stand_in(tmp_path)).run("python", "../pkg")
    assert outside.error is not None
    assert outside.error.data is not None
    assert isinstance(outside.error.data.details, CodegraphExtractorFailedDetails)
    assert outside.error.data.details.stderr_tail == "extractor path must stay inside the worktree"
    version = _run(tmp_path, "ok", b'{"version":2,"nodes":[],"edges":[]}')
    assert version.graph is None
    assert version.error is not None
    assert version.error.data is not None
    assert isinstance(version.error.data.details, CodegraphExtractorFailedDetails)
    assert version.error.data.details.stderr_tail == "unsupported extractor version"
    timed = _run(tmp_path, "sleep", b"")
    assert timed.error is not None
    assert timed.error.data is not None
    assert isinstance(timed.error.data.details, CodegraphExtractorFailedDetails)
    assert timed.error.data.details.stderr_tail == "extractor timed out"
    missing = ExtractorRunner(tmp_path / "missing-hostrun").run("python", "pkg")
    assert missing.error is not None
    assert missing.error.data is not None
    assert isinstance(missing.error.data.details, CodegraphExtractorFailedDetails)
    assert missing.error.data.details.stderr_tail == "hostrun is missing"
    with pytest.raises(ValueError, match="absolute"):
        ExtractorRunner(Path("hostrun"))
    with pytest.raises(ValueError, match="timeout"):
        ExtractorRunner(timeout_s=0)


def _run_ok(tmp: Path) -> list[str]:
    raw = msgspec.json.encode(ExtractorGraph(version=1, nodes=(), edges=()))
    result = _run(tmp, "ok", raw, path="pkg")
    assert result.error is None
    assert result.graph == ExtractorGraph(version=1, nodes=(), edges=())
    return (tmp / "argv").read_text().splitlines()


def _run(tmp: Path, mode: str, stdout: bytes, *, path: str = "pkg") -> ExtractorRun:
    script = _stand_in(tmp)
    out = tmp / "stdout"
    out.write_bytes(stdout)
    argv = tmp / "argv"
    os.environ[_ARGV] = str(argv)
    os.environ[_MODE] = mode
    os.environ[_STDOUT] = str(out)
    try:
        timeout = 0.3 if mode == "sleep" else 5.0
        return ExtractorRunner(script, timeout_s=timeout).run("python", path)
    finally:
        os.environ.pop(_ARGV, None)
        os.environ.pop(_MODE, None)
        os.environ.pop(_STDOUT, None)


def _stand_in(tmp: Path) -> Path:
    script = tmp / "hostrun"
    if not script.exists():
        script.write_text(_SCRIPT)
        script.chmod(0o755)
    return script
