"""Adapter for loading normalized simulated data from JSON fixtures."""

import json
from pathlib import Path
from typing import Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.ports.sources import (
    TaskSourcePort,
    CodeSourcePort,
    CISourcePort,
    PolicySourcePort,
)
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.policies import QualityRule

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


class FixtureSourcesAdapter(TaskSourcePort, CodeSourcePort, CISourcePort, PolicySourcePort):
    """Loads tasks, diffs, CI runs, and policies from JSON fixture files."""

    def __init__(self, fixtures_dir: str | Path = "fixtures"):
        self.fixtures_dir = Path(fixtures_dir)
        self._cache: dict[str, dict[str, Any]] = {}

    def list_available_fixtures(self) -> list[str]:
        """Returns sorted list of fixture identifiers available on disk."""
        if not self.fixtures_dir.exists():
            return []
        files = sorted(self.fixtures_dir.glob("*.json"))
        return [f.stem for f in files]

    def load_fixture(self, fixture_name: str) -> dict[str, Any]:
        """Loads and parses raw JSON fixture file."""
        if fixture_name in self._cache:
            return self._cache[fixture_name]

        path = self.fixtures_dir / f"{fixture_name}.json"
        if not path.exists():
            path = self.fixtures_dir / fixture_name
            if not path.exists():
                raise FileNotFoundError(f"Fixture file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._cache[fixture_name] = data
        return data

    def get_tasks(self, project_id: str) -> list[TaskCard]:
        """Extracts TaskCard objects from the specified fixture."""
        fixture = self.load_fixture(project_id)
        cards: list[TaskCard] = []
        for raw in fixture.get("tasks", []):
            due = None
            if raw.get("due_date"):
                dt = datetime.fromisoformat(raw["due_date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
                due = dt

            state_val = raw.get("state", "desconocido")
            try:
                state = TaskState(state_val)
            except ValueError:
                state = TaskState.unknown

            cards.append(
                TaskCard(
                    id=raw["id"],
                    title=raw["title"],
                    list_id=raw.get("list_id"),
                    list_name=raw.get("list_name"),
                    labels=raw.get("labels", []),
                    state=state,
                    due_date=due,
                    detail=raw.get("detail"),
                    external_id=raw.get("external_id"),
                    is_included=raw.get("is_included", True),
                    has_contradiction=raw.get("has_contradiction", False),
                )
            )
        return cards

    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        """Extracts CodeDiffSnippet objects, optionally filtered by commit SHA."""
        fixture = self.load_fixture(project_id)
        snippets: list[CodeDiffSnippet] = []
        for raw in fixture.get("code_diffs", []):
            if commit_sha and raw.get("commit_sha") != commit_sha:
                continue
            snippets.append(
                CodeDiffSnippet(
                    repository=raw["repository"],
                    pull_request_id=raw.get("pull_request_id"),
                    commit_sha=raw["commit_sha"],
                    file_path=raw["file_path"],
                    diff_side=raw.get("diff_side", "NEW"),
                    lines=raw["lines"],
                    content=raw["content"],
                )
            )
        return snippets

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        """Extracts CIRunResult objects, optionally filtered by commit SHA."""
        fixture = self.load_fixture(project_id)
        runs: list[CIRunResult] = []
        for raw in fixture.get("ci_runs", []):
            if commit_sha and raw.get("commit_sha") != commit_sha:
                continue
            ts = None
            if raw.get("timestamp"):
                ts = datetime.fromisoformat(raw["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=DEFAULT_TIMEZONE)

            runs.append(
                CIRunResult(
                    id=raw["id"],
                    workflow_name=raw["workflow_name"],
                    commit_sha=raw["commit_sha"],
                    status=raw["status"],
                    total_tests=raw.get("total_tests"),
                    failed_tests=raw.get("failed_tests"),
                    coverage_percentage=raw.get("coverage_percentage"),
                    timestamp=ts,
                )
            )
        return runs

    def get_policies(self, project_id: str) -> list[QualityRule]:
        """Extracts configurable QualityRule policies for the project."""
        fixture = self.load_fixture(project_id)
        rules: list[QualityRule] = []
        for raw in fixture.get("policies", []):
            rules.append(
                QualityRule(
                    id=raw["id"],
                    description=raw["description"],
                    metric_key=raw["metric_key"],
                    operator=raw.get("operator", "=="),
                    threshold=raw.get("threshold"),
                    source=raw.get("source", "ci"),
                    mandatory=raw.get("mandatory", True),
                )
            )
        return rules
