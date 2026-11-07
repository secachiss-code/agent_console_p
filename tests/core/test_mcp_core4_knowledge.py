"""A configured database cannot acknowledge a knowledge write it did not save."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.mcp import tools
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase
from tests.core.test_knowledge_store import _writer

if TYPE_CHECKING:
    from pathlib import Path


def test_kb_missing_project_is_an_error_and_not_an_in_memory_success(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        board = PlanBoard()
        board.use_knowledge(KnowledgeDatabase(writer))
        context = TabContext(tab_id="tab", project_id="missing", worktree=None, agent="claude")
        with pytest.raises(tools._ToolError, match="INTERNAL_ERROR"):
            _ = board.kb_add(context, "decision", "Keep", "Body", "source", [], None)
        assert board._facts == {}
        assert KnowledgeDatabase(writer).load() == ((), ())
    finally:
        writer.stop()
