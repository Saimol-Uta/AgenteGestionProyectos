"""Abstract ports for data sources (tasks, code diffs, CI runs)."""

from abc import ABC, abstractmethod
from typing import Optional

from agente_gestion.domain.tasks import TaskCard
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult


class TaskSourcePort(ABC):
    """Port interface for task management systems e.g. Trello, Jira."""

    @abstractmethod
    def get_tasks(self, project_id: str) -> list[TaskCard]:
        """Retrieve current task cards for the specified project."""
        raise NotImplementedError


class CodeSourcePort(ABC):
    """Port interface for version control systems e.g. GitHub."""

    @abstractmethod
    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        """Retrieve code diff snippets for relevant commit(s) or PR(s)."""
        raise NotImplementedError


class CISourcePort(ABC):
    """Port interface for continuous integration systems e.g. GitHub Actions."""

    @abstractmethod
    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        """Retrieve CI execution runs and quality reports."""
        raise NotImplementedError
