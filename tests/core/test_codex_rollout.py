"""Rollout tails resume at an offset, including a cut last line and a rotated file."""

from __future__ import annotations

import compression.zstd
import os
from pathlib import Path

from aconsole.core.domain.quota import QuotaKey
from aconsole.core.infrastructure.agents.codex.rollout import RolloutTail, find_rollout, quotas

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "0.164.0" / "rollout.jsonl"
_SESSION = "01a16dd1-b420-7432-938c-eb76df822f72"


def _place(root: Path, day: str, name: str, body: bytes) -> Path:
    directory = root / "sessions" / "2026" / "10" / day
    directory.mkdir(parents=True)
    path = directory / name
    path.write_bytes(body)
    return path


def test_codex_rollout_reads_the_fixture_quota_once() -> None:
    lines = _FIXTURE.read_bytes().splitlines()
    assert quotas(lines[0]) == ()
    windows = quotas(lines[1])
    assert windows[0].key is QuotaKey.FIVE_HOUR
    assert windows[0].used_pct == 25.0
    assert windows[0].resets_at == 1700000000
    assert windows[1].key is QuotaKey.SEVEN_DAY
    assert windows[1].used_pct == 3.0
    assert quotas(b'{"type":"event_msg","payload":{"type":"token_count","rate_limits":{"primary":') == ()


def test_codex_rollout_tail_holds_a_cut_line_and_does_not_reread(tmp_path: Path) -> None:
    path = _place(tmp_path, "24", f"rollout-1-{_SESSION}.jsonl", b'{"ok":true}\n{"cut":')
    tail = RolloutTail(tmp_path)
    opened = tail.read(_SESSION)
    assert opened.path == path
    assert opened.lines == (b'{"ok":true}',)
    assert tail.read(_SESSION).lines == ()
    with path.open("ab") as handle:
        _ = handle.write(b"1}\n")
    finished = tail.read(_SESSION)
    assert finished.lines == (b'{"cut":1}',)
    assert tail.read(_SESSION).lines == ()


def test_codex_rollout_zst_and_rotation(tmp_path: Path) -> None:
    plain = _FIXTURE.read_bytes()
    if not plain.endswith(b"\n"):
        plain += b"\n"
    compressed = _place(tmp_path, "24", f"rollout-1-{_SESSION}.jsonl.zst", compression.zstd.compress(plain))
    tail = RolloutTail(tmp_path)
    first = tail.read(_SESSION)
    assert first.path == compressed
    assert len(first.lines) == 2
    assert quotas(first.lines[1])[0].key is QuotaKey.FIVE_HOUR
    assert tail.read(_SESSION).lines == ()
    os.utime(compressed, (1, 1))
    rotated = _place(tmp_path, "25", f"rollout-2-{_SESSION}.jsonl", b'{"type":"session_meta","payload":{"id":"n"}}\n')
    os.utime(rotated, (2_000_000_000, 2_000_000_000))
    nxt = tail.read(_SESSION)
    assert nxt.path == rotated
    assert nxt.lines == (b'{"type":"session_meta","payload":{"id":"n"}}',)
    assert find_rollout(tmp_path, "../" + _SESSION) is None
    long = b'{"long":true,"pad":"xxxxxxxxxxxxxxxxxxxx"}\n'
    rotated.write_bytes(long)
    shrunk = RolloutTail(tmp_path)
    assert shrunk.read(_SESSION).lines == (long.rstrip(b"\n"),)
    rotated.write_bytes(b'{"n":1}\n')
    assert shrunk.read(_SESSION).lines == (b'{"n":1}',)
