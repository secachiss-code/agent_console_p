"""Round-trip, registry, and generated-artifact checks for the contract package."""

from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.errors import ERRORS, error_by_name
from aconsole.contracts.generate import documents, pydantic_models, struct_types, write_artifacts
from aconsole.contracts.mcp_models import edge_kinds
from aconsole.contracts.methods import METHODS, method_by_name
from tests.contracts.examples import samples


def test_samples_round_trip() -> None:
    built = samples()
    assert set(built) == {struct.__name__ for struct in struct_types()}
    for name, value in built.items():
        again = msgspec.json.decode(msgspec.json.encode(value), type=type(value))
        assert again == value, name


def test_golden_count_covers_every_struct() -> None:
    golden_dir = Path("tests/contracts/golden")
    assert len(list(golden_dir.glob("*.json"))) >= len(struct_types())


def test_error_registry_is_unique() -> None:
    assert len({item.code for item in ERRORS}) == len(ERRORS)
    assert len({item.name for item in ERRORS}) == len(ERRORS)
    assert error_by_name("TAB_NOT_FOUND").code == 2003
    with pytest.raises(KeyError):
        error_by_name("NO_SUCH_ERROR")


def test_methods_reference_known_errors() -> None:
    names = {item.name for item in ERRORS}
    assert method_by_name("tab/open").long is True
    for method in METHODS:
        assert set(method.errors) <= names
    with pytest.raises(KeyError):
        method_by_name("no/such")


def test_documents_match_the_tree(tmp_path: Path) -> None:
    rendered = documents()
    assert "docs/contracts/generated/types.md" in rendered
    assert any(path.endswith(".schema.json") for path in rendered)
    written = write_artifacts(tmp_path)
    assert set(written) == set(rendered)
    assert edge_kinds() == ("blocks", "parent", "related")
    assert pydantic_models()
