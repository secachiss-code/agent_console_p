"""The committed bytes are the encoding of every contract struct."""

from __future__ import annotations

from pathlib import Path

import msgspec

from aconsole.contracts.generate import struct_types
from tests.contracts.examples import samples

_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "golden"


def test_contracts_golden_bytes() -> None:
    built = samples()
    names = {struct.__name__ for struct in struct_types()}
    stored = {path.name.removesuffix(".json") for path in _GOLDEN.glob("*.json")}
    assert set(built) == names
    assert stored == names
    for name, value in built.items():
        encoded = msgspec.json.encode(value, order="deterministic")
        assert (_GOLDEN / f"{name}.json").read_bytes() == encoded
