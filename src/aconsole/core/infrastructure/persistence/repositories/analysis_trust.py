"""Persist trust for analysis runs, defaulting to untrusted."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from aconsole.core.application.services.project_analysis import ProjectTrust
from aconsole.core.infrastructure.persistence.models import Project, ProjectAnalysisTrust

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AnalysisTrustRepository:
    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def get(self, project_id: str) -> ProjectTrust | None:
        if self._session.get(Project, project_id) is None:
            return None
        trusted = self._session.scalar(
            select(ProjectAnalysisTrust.analysis_trusted).where(ProjectAnalysisTrust.project_id == project_id)
        )
        return ProjectTrust(trusted=bool(trusted))

    def save(self, project_id: str, trust: ProjectTrust) -> bool:
        """Return false if the project does not exist."""
        if self._session.get(Project, project_id) is None:
            return False
        statement = insert(ProjectAnalysisTrust).values(project_id=project_id, analysis_trusted=trust.trusted)
        _ = self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ProjectAnalysisTrust.project_id],
                set_={ProjectAnalysisTrust.analysis_trusted: statement.excluded.analysis_trusted},
            ),
        )
        return True
