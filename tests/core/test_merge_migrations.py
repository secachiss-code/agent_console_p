"""Both independently deployed 0008 revisions upgrade to the merged head."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from sqlalchemy import inspect

from aconsole.core.infrastructure.persistence.engine import migrate, write_engine


@pytest.mark.parametrize("revision", ["0008_approval_policy", "0008_pending_session_fork"])
def test_upgrade_from_either_branch_keeps_both_features(tmp_path: Path, revision: str) -> None:
    url = f"sqlite:///{tmp_path / 'branch.db'}"
    migrate(url, revision)
    migrate(url, "head")
    engine = write_engine(url)
    try:
        schema = inspect(engine)
        assert {"approval_policy", "approval_dangerous"} <= {
            column["name"] for column in schema.get_columns("projects")
        }
        assert "fork_pending" in {column["name"] for column in schema.get_columns("tabs")}
    finally:
        engine.dispose()
