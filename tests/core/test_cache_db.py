"""Recovery, capacity, fencing and batched hot-cache operations."""

import time
lazy from pathlib import Path

import pytest

from aconsole.core.infrastructure.paths import runtime_cache_path
from aconsole.core.infrastructure.persistence.cache.schema import ast_skeleton_cache, node_scratchpad
from aconsole.core.infrastructure.persistence.cache.store import CacheStore, StaleScratchFenceError


def _scratch(fence: int = 1, tab: str = "t") -> dict[str, str | int | None]:
    return {"node_id": "n", "tab_id": tab, "fence": fence, "attempt_no": 1, "step_type": "spec", "head_block": "h",
                "remaining_items_json": "[]", "compact_verdict": None, "diff_stat": None, "updated_at": 0}


def test_scratch_survives_restart_and_stale_fence_cannot_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    store = CacheStore.open(path)
    store.put(node_scratchpad, [_scratch(2)])
    with pytest.raises(StaleScratchFenceError):
        store.put(node_scratchpad, [_scratch(1), _scratch(2, "other")])
    store.close()
    store = CacheStore.open(path)
    row = store.get(node_scratchpad, {"node_id": "n"})
    assert row is not None
    assert row["fence"] == 2
    assert row["tab_id"] == "t"
    store.close()


def test_corrupt_cache_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    path.write_bytes(b"not a database")
    store = CacheStore.open(path)
    store.put(node_scratchpad, [_scratch()])
    assert store.get(node_scratchpad, {"node_id": "n"}) is not None
    store.close()


def test_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert runtime_cache_path() is None
    first = CacheStore.open_runtime()
    first.put(node_scratchpad, [_scratch()])
    first.close()
    second = CacheStore.open_runtime()
    assert second.get(node_scratchpad, {"node_id": "n"}) is None
    second.close()


def test_full_cache_evicts_derived_data_and_retries(tmp_path: Path) -> None:
    store = CacheStore.open(tmp_path / "cache.db", max_mib=16)
    rows = [{"file_sha256": str(i), "extractor": "ast", "signatures": "x" * 1_000_000,
                 "byte_len": 1_000_000, "last_used_at": i} for i in range(15)]
    store.put(ast_skeleton_cache, rows)
    store.put(ast_skeleton_cache, [{"file_sha256": "new", "extractor": "ast", "signatures": "y" * 2_000_000,
                                     "byte_len": 2_000_000, "last_used_at": 16}])
    assert store.get(ast_skeleton_cache, {"file_sha256": "new", "extractor": "ast"}) is not None
    assert store.get(ast_skeleton_cache, {"file_sha256": "0", "extractor": "ast"}) is None
    store.close()


def test_ten_thousand_scratch_upserts(tmp_path: Path) -> None:
    store = CacheStore.open(tmp_path / "cache.db")
    started = time.monotonic()
    store.put(node_scratchpad, [_scratch() for _ in range(10_000)])
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    store.close()
