"""A0.3: core dependencies stay free-threaded and the spike pipeline commits."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from spikes.s1_core import harness


def test_spike_s1_imports_keep_gil_off() -> None:
    assert sys._is_gil_enabled() is False
    assert harness.import_core_dependencies() is False


def test_spike_s1_pipeline() -> None:
    directory = Path(tempfile.mkdtemp(prefix="spike-s1-"))
    spike = harness.Spike(directory)
    spike.start()
    try:
        bench = spike.benchmark(0.4)
    finally:
        spike.stop()
    assert bench.events > 0
    assert bench.http > 0
    assert bench.stored == bench.events
    assert bench.event_p99_ms >= bench.event_p50_ms
    assert bench.http_p99_ms >= bench.http_p50_ms
