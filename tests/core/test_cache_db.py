"""Recovery, capacity, fencing and batched hot-cache operations."""

import time
lazy from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, Table, Text, create_engine, func, inspect, select

from aconsole.core.infrastructure.codegraph.skeleton import file_hashes, python_skeleton
from aconsole.core.infrastructure.paths import runtime_cache_path
from aconsole.core.infrastructure.persistence import engine as engine_module
from aconsole.core.infrastructure.persistence.cache.schema import CACHE_SCHEMA, ast_skeleton_cache, node_scratchpad
from aconsole.core.infrastructure.persistence.cache.store import CacheStore, StaleScratchFenceError
from aconsole.core.infrastructure.pipeline import PipelineCache


def _scratch(fence: int = 1, tab: str = "t") -> dict[str, str | int | None]:
    return {
        "node_id": "n",
        "tab_id": tab,
        "fence": fence,
        "attempt_no": 1,
        "step_type": "spec",
        "head_block": "h",
        "remaining_items_json": "[]",
        "compact_verdict": None,
        "diff_stat": None,
        "updated_at": 0,
    }


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
    _ = path.write_bytes(b"not a database")
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
    rows: list[dict[str, str | int | None]] = [
        {
            "interface_hash": str(i),
            "extractor": "ast",
            "signatures": "x" * 1_000_000,
            "byte_len": 1_000_000,
            "last_used_at": i,
        }
        for i in range(15)
    ]
    store.put(ast_skeleton_cache, rows)
    store.put(
        ast_skeleton_cache,
        [
            {
                "interface_hash": "new",
                "extractor": "ast",
                "signatures": "y" * 2_000_000,
                "byte_len": 2_000_000,
                "last_used_at": 16,
            }
        ],
    )
    assert store.get(ast_skeleton_cache, {"interface_hash": "new", "extractor": "ast"}) is not None
    assert store.get(ast_skeleton_cache, {"interface_hash": "0", "extractor": "ast"}) is None
    store.close()


def test_ten_thousand_scratch_upserts(tmp_path: Path) -> None:
    store = CacheStore.open(tmp_path / "cache.db")
    started = time.monotonic()
    store.put(node_scratchpad, [_scratch() for _ in range(10_000)])
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    store.close()


@pytest.mark.parametrize("version", [1, 3])
def test_incompatible_cache_is_recreated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int) -> None:
    path = tmp_path / "cache.db"
    # Build a valid SQLite cache from a different release, including an obsolete table.
    with monkeypatch.context() as patch:
        patch.setattr(engine_module, "CACHE_SCHEMA", version)
        old_engine = engine_module.cache_engine(path)
        obsolete = Table("obsolete_cache", MetaData(), Column("file_sha256", Text, primary_key=True))
        obsolete.create(old_engine)
        old_store = CacheStore(old_engine)
        old_store.put(node_scratchpad, [_scratch()])
        old_store.close()
    store = CacheStore.open(path)
    assert store.get(node_scratchpad, {"node_id": "n"}) is None
    probe = create_engine(f"sqlite:///{path}")
    try:
        assert "obsolete_cache" not in inspect(probe).get_table_names()
        columns = {column["name"] for column in inspect(probe).get_columns("ast_skeleton_cache")}
        assert "interface_hash" in columns
        assert "file_sha256" not in columns
    finally:
        probe.dispose()
    store.put(node_scratchpad, [_scratch(2)])
    store.close()
    reopened = CacheStore.open(path)
    assert reopened.get(node_scratchpad, {"node_id": "n"}) is not None
    reopened.close()
    assert CACHE_SCHEMA == 2


def test_different_bodies_share_interface_cache_entry(tmp_path: Path) -> None:
    store = CacheStore.open(tmp_path / "cache.db")
    hashes: list[str] = []
    for index, body in enumerate(("return value + 1", "return value * 2")):
        source = f"def calculate(value: int) -> int:\n    {body}\n"
        path = tmp_path / f"source{index}.py"
        _ = path.write_text(source)
        digest = file_hashes(tmp_path, path.name, "python", ())
        assert digest.body is not None
        hashes.append(digest.body)
        assert digest.interface
        skeleton = python_skeleton(source)
        store.put(
            ast_skeleton_cache,
            [
                {
                    "interface_hash": digest.interface,
                    "extractor": "python",
                    "signatures": skeleton,
                    "byte_len": len(skeleton.encode()),
                    "last_used_at": index,
                }
            ],
        )
        row = store.get(ast_skeleton_cache, {"interface_hash": digest.interface, "extractor": "python"})
        assert row is not None
        assert row["signatures"] == skeleton
    assert hashes[0] != hashes[1]
    probe = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    try:
        with probe.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(ast_skeleton_cache)) == 1
    finally:
        probe.dispose()
    store.close()


def test_skeleton_cache_separates_extractors_and_changed_interfaces(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    store = CacheStore.open(tmp_path / "cache.db")
    for name in ("first", "second"):
        source = f"def {name}(value: int) -> int:\n    return value\n"
        _ = path.write_text(source)
        digest = file_hashes(tmp_path, path.name, "python", ())
        assert digest.interface
        for extractor in ("python-v1", "python-v2"):
            skeleton = python_skeleton(source)
            store.put(
                ast_skeleton_cache,
                [
                    {
                        "interface_hash": digest.interface,
                        "extractor": extractor,
                        "signatures": skeleton,
                        "byte_len": len(skeleton.encode()),
                        "last_used_at": 0,
                    }
                ],
            )
    store.close()
    probe = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    try:
        with probe.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(ast_skeleton_cache)) == 4
    finally:
        probe.dispose()


def test_generation_mark_transfers_onto_a_new_session(tmp_path: Path) -> None:
    store = CacheStore.open(tmp_path / "cache.db")
    cache = PipelineCache(store)
    cache.generation_mark("tab", 2, "a" * 64)
    assert cache.generation_seen("tab", 2, "a" * 64)
    assert not cache.generation_seen("tab", 2, "b" * 64)
    store.close()
