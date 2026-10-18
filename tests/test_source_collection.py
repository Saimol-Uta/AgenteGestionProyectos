"""Tests for normalized composite real-source collection boundary (T5a)."""

import base64
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import unittest
from typing import Any, Optional

from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import (
    DiffSide,
    EvidenceItem,
    EvidenceProvenance,
    EvidenceSource,
)
from agente_gestion.domain.policies import QualityRule, evaluate_policies
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.ports.source_collection import (
    SourceCollectionPort,
    SourceCollectionResult,
    SourceErrorInfo,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import (
    CISourcePort,
    CodeSourcePort,
    PolicySourcePort,
    TaskSourcePort,
)
from agente_gestion.adapters.sources.local_policy import LocalYamlPolicyAdapter
from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.github import GitHubCodeSourceAdapter

# Import under test - will fail RED before implementation exists
from agente_gestion.adapters.sources.composite import CompositeSourcesAdapter


SAMPLE_SHA = "a" * 40
ALT_SHA = "b" * 40


class FakeTransport(HttpGetTransportPort):
    """Deterministic fake HTTP transport for real adapter integration tests."""

    def __init__(self, responses: dict[str, HttpResponse | Exception] | None = None):
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url in self.responses:
            resp = self.responses[url]
            if isinstance(resp, Exception):
                raise resp
            return resp
        return HttpResponse(status_code=200, body={"total_count": 0, "workflow_runs": []})


class FakeTaskSource(TaskSourcePort):
    """Deterministic fake task source adapter."""

    def __init__(
        self,
        tasks: Optional[list[TaskCard]] = None,
        evidence: Optional[list[EvidenceItem]] = None,
        error: Optional[Exception] = None,
        evidence_error: Optional[Exception] = None,
    ):
        self.tasks = tasks if tasks is not None else []
        self.evidence = evidence if evidence is not None else []
        self.error = error
        self.evidence_error = evidence_error
        self.calls: list[str] = []
        self.invalidated_projects: list[Optional[str]] = []

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        self.invalidated_projects.append(project_id)

    def get_tasks(self, project_id: str) -> list[TaskCard]:
        self.calls.append(project_id)
        if self.error:
            raise self.error
        return [t.model_copy(deep=True) for t in self.tasks]

    def get_evidence(self, project_id: str) -> list[EvidenceItem]:
        if self.evidence_error:
            raise self.evidence_error
        return [e.model_copy(deep=True) for e in self.evidence]


class FakeCodeSource(CodeSourcePort):
    """Deterministic fake code diff source adapter."""

    def __init__(
        self,
        diffs: Optional[list[CodeDiffSnippet]] = None,
        evidence: Optional[list[EvidenceItem]] = None,
        resolved_sha: Optional[str] = None,
        is_complete: bool = True,
        unavailable_file_paths: tuple[str, ...] = (),
        error: Optional[Exception] = None,
        evidence_error: Optional[Exception] = None,
    ):
        self.diffs = diffs if diffs is not None else []
        self.evidence = evidence if evidence is not None else []
        self._last_resolved_commit_sha = resolved_sha
        self._last_collection_complete = is_complete
        self._unavailable_file_paths = unavailable_file_paths
        self.error = error
        self.evidence_error = evidence_error
        self.calls: list[tuple[str, Optional[str]]] = []
        self.invalidated_projects: list[Optional[str]] = []

    @property
    def last_resolved_commit_sha(self) -> Optional[str]:
        return self._last_resolved_commit_sha

    @property
    def last_collection_complete(self) -> bool:
        return self._last_collection_complete

    @property
    def unavailable_file_paths(self) -> list[str]:
        return list(self._unavailable_file_paths)

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        self.invalidated_projects.append(project_id)

    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        self.calls.append((project_id, commit_sha))
        if self.error:
            raise self.error
        if commit_sha:
            self._last_resolved_commit_sha = commit_sha
        return [d.model_copy(deep=True) for d in self.diffs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        if self.evidence_error:
            raise self.evidence_error
        return [e.model_copy(deep=True) for e in self.evidence]


class FakeCISource(CISourcePort):
    """Deterministic fake continuous integration source adapter."""

    def __init__(
        self,
        runs: Optional[list[CIRunResult]] = None,
        evidence: Optional[list[EvidenceItem]] = None,
        error: Optional[Exception] = None,
        evidence_error: Optional[Exception] = None,
    ):
        self.runs = runs if runs is not None else []
        self.evidence = evidence if evidence is not None else []
        self.error = error
        self.evidence_error = evidence_error
        self.calls: list[tuple[str, Optional[str]]] = []
        self.invalidated_projects: list[Optional[str]] = []

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        self.invalidated_projects.append(project_id)

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        self.calls.append((project_id, commit_sha))
        if self.error:
            raise self.error
        return [r.model_copy(deep=True) for r in self.runs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        if self.evidence_error:
            raise self.evidence_error
        return [e.model_copy(deep=True) for e in self.evidence]


class FakePolicySource(PolicySourcePort):
    """Deterministic fake quality policies source adapter."""

    def __init__(
        self,
        policies: Optional[list[QualityRule]] = None,
        error: Optional[Exception] = None,
    ):
        self.policies = policies if policies is not None else []
        self.error = error
        self.calls: list[str] = []

    def get_policies(self, project_id: str) -> list[QualityRule]:
        self.calls.append(project_id)
        if self.error:
            raise self.error
        return [p.model_copy(deep=True) for p in self.policies]


def _make_sample_task(project_id: str = "proj-1") -> tuple[TaskCard, EvidenceItem]:
    card = TaskCard(
        id="card-1",
        title="Implement feature",
        state=TaskState.in_progress,
        list_name="In Progress",
    )
    evidence = EvidenceItem(
        provenance=EvidenceProvenance(
            id="trello-task-card-1",
            source=EvidenceSource.trello,
            project_id=project_id,
            timestamp=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            payload={"id": "card-1", "name": "Implement feature"},
        ),
        category="task",
        summary="Task card-1 in In Progress",
    )
    return card, evidence


def _make_sample_diff(
    project_id: str = "proj-1",
    sha: str = SAMPLE_SHA,
) -> tuple[CodeDiffSnippet, EvidenceItem]:
    diff = CodeDiffSnippet(
        repository="owner/repo",
        commit_sha=sha,
        file_path="src/main.py",
        diff_side="NEW",
        lines=[1, 2],
        content="+print('hello')\n",
    )
    evidence = EvidenceItem(
        provenance=EvidenceProvenance(
            id=f"github-diff-{sha[:8]}-0",
            source=EvidenceSource.github,
            project_id=project_id,
            repository="owner/repo",
            commit_sha=sha,
            file_path="src/main.py",
            diff_side=DiffSide.NEW,
            lines=[1, 2],
            timestamp=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            payload={"status": "modified"},
        ),
        category="diff",
        summary="Diff snippet for src/main.py",
    )
    return diff, evidence


def _make_sample_ci(
    project_id: str = "proj-1",
    sha: str = SAMPLE_SHA,
) -> tuple[CIRunResult, EvidenceItem]:
    run = CIRunResult(
        id="run-1",
        workflow_name="CI",
        commit_sha=sha,
        status="success",
        total_tests=None,
        failed_tests=None,
        coverage_percentage=None,
        timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
    )
    evidence = EvidenceItem(
        provenance=EvidenceProvenance(
            id="actions-ci-run-1",
            source=EvidenceSource.github_actions,
            project_id=project_id,
            repository="owner/repo",
            commit_sha=sha,
            external_id="run-1",
            timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
            payload={"status": "completed", "conclusion": "success"},
        ),
        category="ci_run",
        summary="CI workflow CI status success",
    )
    return run, evidence


def _make_sample_policy() -> QualityRule:
    return QualityRule(
        id="Q1",
        description="No failing tests",
        metric_key="failed_tests",
        operator="<=",
        threshold=0.0,
        source="ci",
        mandatory=True,
    )


class TestCompositeSourcesAdapter(unittest.TestCase):
    """Unit tests for CompositeSourcesAdapter."""

    def setUp(self) -> None:
        self.project_id = "test-project"
        self.cutoff = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)

    def test_all_providers_complete_default_policies(self) -> None:
        """All 4 providers succeed: snapshot is complete with 4 success records."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA, is_complete=True)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)

        self.assertIsInstance(result, SourceCollectionResult)
        self.assertTrue(result.snapshot.is_complete)
        self.assertIsNone(result.snapshot.incomplete_reason)
        self.assertEqual(result.target_commit_sha, SAMPLE_SHA)
        self.assertEqual(len(result.snapshot.tasks), 1)
        self.assertEqual(len(result.snapshot.code_diffs), 1)
        self.assertEqual(len(result.snapshot.ci_runs), 1)
        self.assertEqual(len(result.snapshot.policies), 1)
        self.assertEqual(len(result.evidence), 3)

        # Audit records: exactly 4 records, all success
        self.assertEqual(len(result.records), 4)
        for rec in result.records:
            self.assertEqual(rec.status, SourceExecutionStatus.success)
            self.assertIsNone(rec.error)
            self.assertEqual(rec.attempt_count, 1)
            self.assertGreaterEqual(rec.duration_seconds, 0.0)

        providers = {r.provider for r in result.records}
        self.assertEqual(
            providers,
            {
                SourceProvider.trello,
                SourceProvider.github,
                SourceProvider.github_actions,
                SourceProvider.local_policy,
            },
        )

    def test_default_local_policy_equivalence(self) -> None:
        """Using real LocalYamlPolicyAdapter with policies.yaml yields expected 3 baseline rules."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        real_policy = LocalYamlPolicyAdapter("config/policies.yaml")

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=real_policy,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertTrue(result.snapshot.is_complete)
        self.assertEqual(len(result.snapshot.policies), 3)
        rule_ids = {p.id for p in result.snapshot.policies}
        self.assertEqual(rule_ids, {"Q1", "Q2", "Q3"})

    def test_explicit_commit_sha_override(self) -> None:
        """Explicit commit_sha is passed to code adapter and propagated to CI."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, ALT_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, ALT_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=ALT_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff, commit_sha=ALT_SHA)
        self.assertEqual(result.target_commit_sha, ALT_SHA)
        self.assertEqual(code_src.calls[0], (self.project_id, ALT_SHA))
        self.assertEqual(ci_src.calls[0], (self.project_id, ALT_SHA))

    def test_default_tip_resolved_sha_used_by_ci(self) -> None:
        """When commit_sha is None, code adapter resolves SHA once, and CI receives exact pinned SHA."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff, commit_sha=None)
        self.assertEqual(result.target_commit_sha, SAMPLE_SHA)
        self.assertEqual(code_src.calls[0], (self.project_id, None))
        self.assertEqual(ci_src.calls[0], (self.project_id, SAMPLE_SHA))

    def test_empty_diff_genuine_empty_commit(self) -> None:
        """Commit with 0 diffs and complete status uses published SHA, not diffs[0]."""
        task, task_ev = _make_sample_task(self.project_id)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(
            diffs=[],
            evidence=[],
            resolved_sha=SAMPLE_SHA,
            is_complete=True,
            unavailable_file_paths=(),
        )
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertTrue(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.code_diffs, [])
        self.assertEqual(result.target_commit_sha, SAMPLE_SHA)
        self.assertEqual(ci_src.calls[0], (self.project_id, SAMPLE_SHA))

    def test_partial_code_preserves_evidence_and_queries_ci_for_pinned_sha(self) -> None:
        """Validated partial diffs remain useful, but missing patch metadata keeps the snapshot incomplete."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        ci = ci.model_copy(update={
            "workflow_name": "Quality",
            "status": "failure",
            "workflow_path": ".github/workflows/quality.yml",
            "run_attempt": 2,
        })
        ci_ev = ci_ev.model_copy(update={
            "summary": "CI workflow Quality status failure",
            "provenance": ci_ev.provenance.model_copy(update={
                "payload": {
                    "status": "completed",
                    "conclusion": "failure",
                    "workflow_path": ".github/workflows/quality.yml",
                    "run_id": "run-1",
                    "run_attempt": 2,
                },
            }),
        })
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(
            diffs=[diff],
            evidence=[diff_ev],
            resolved_sha=SAMPLE_SHA,
            is_complete=False,
            unavailable_file_paths=("large.bin",),
        )
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertIn("large.bin", result.snapshot.incomplete_reason)
        self.assertEqual(result.snapshot.code_diffs, [diff])
        self.assertEqual([item for item in result.evidence if item.provenance.source == EvidenceSource.github], [diff_ev])
        self.assertEqual(ci_src.calls, [(self.project_id, SAMPLE_SHA)])
        self.assertEqual(result.snapshot.ci_runs[0].status, "failure")
        self.assertEqual(result.snapshot.ci_runs[0].workflow_path, ".github/workflows/quality.yml")
        self.assertEqual(result.snapshot.ci_runs[0].run_attempt, 2)
        ci_evidence = next(item for item in result.evidence if item.provenance.source == EvidenceSource.github_actions)
        self.assertEqual(ci_evidence.provenance.external_id, "run-1")
        self.assertEqual(ci_evidence.provenance.payload["workflow_path"], ".github/workflows/quality.yml")
        self.assertEqual(ci_evidence.provenance.payload["run_attempt"], 2)
        self.assertEqual(result.target_commit_sha, SAMPLE_SHA)
        code_record = next(r for r in result.records if r.provider == SourceProvider.github)
        self.assertEqual(code_record.status, SourceExecutionStatus.success)
        self.assertIsNone(code_record.error)
        ci_record = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.status, SourceExecutionStatus.success)
        self.assertEqual(adapter.last_resolved_commit_sha, SAMPLE_SHA)
        # Other independent sources continue.
        self.assertEqual(len(result.snapshot.tasks), 1)
        self.assertEqual(len(result.snapshot.policies), 1)

    def test_wrapped_partial_code_queries_ci_for_pinned_sha_but_invalid_wrapping_suppresses_ci(self) -> None:
        source = b"line one\nline two\nline three\n"
        encoded = base64.b64encode(source).decode("ascii")
        chunks = [encoded[index:index + 12] for index in range(0, len(encoded), 12)]
        wrapped = "\r\n".join(chunks) + "\r\n"
        cases = (("valid CRLF wrapping", wrapped, True), ("invalid character", wrapped + "#", False))
        recovered_path = "scripts/recovered.py"
        sibling_path = "src/sibling.py"
        commit_url = f"https://api.github.com/repos/owner/repo/commits/{SAMPLE_SHA}"
        recovered_url = (
            f"https://api.github.com/repos/owner/repo/contents/{recovered_path}?ref={SAMPLE_SHA}"
        )
        sibling_url = f"https://api.github.com/repos/owner/repo/contents/{sibling_path}?ref={SAMPLE_SHA}"

        for label, content, is_valid in cases:
            with self.subTest(label=label):
                transport = FakeTransport({
                    commit_url: HttpResponse(status_code=200, body={
                        "sha": SAMPLE_SHA,
                        "files": [
                            {
                                "filename": sibling_path, "status": "modified",
                                "patch": "@@ -1 +1 @@\n-old\n+new\n",
                            },
                            {
                                "filename": recovered_path, "status": "modified", "patch": None,
                                "sha": "d" * 40,
                            },
                        ],
                    }),
                    sibling_url: HttpResponse(status_code=404, body={}),
                    recovered_url: HttpResponse(status_code=200, body={
                        "encoding": "base64", "content": content,
                        "sha": "d" * 40, "size": len(source),
                    }),
                })
                code_source = GitHubCodeSourceAdapter(
                    owner="owner", repo="repo", transport=transport,
                )
                ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
                ci_source = FakeCISource([ci], [ci_ev])
                adapter = CompositeSourcesAdapter(
                    task_source=FakeTaskSource(),
                    code_source=code_source,
                    ci_source=ci_source,
                    policy_source=FakePolicySource(),
                    default_commit_sha=SAMPLE_SHA,
                )

                result = adapter.collect_snapshot(self.project_id, self.cutoff)

                if is_valid:
                    self.assertFalse(result.snapshot.is_complete)
                    self.assertEqual(result.target_commit_sha, SAMPLE_SHA)
                    self.assertEqual(ci_source.calls, [(self.project_id, SAMPLE_SHA)])
                    self.assertEqual(len(result.snapshot.ci_runs), 1)
                    self.assertEqual(result.snapshot.ci_runs[0].commit_sha, SAMPLE_SHA)
                    full_file = next(
                        item for item in result.snapshot.code_diffs
                        if item.file_path == recovered_path and item.evidence_scope == "full_file"
                    )
                    self.assertEqual(full_file.content, source.decode("utf-8"))
                    self.assertEqual(full_file.source_blob_sha, "d" * 40)
                    self.assertTrue(any(
                        item.file_path == sibling_path and item.evidence_scope == "diff"
                        for item in result.snapshot.code_diffs
                    ))
                    recovered_evidence = next(
                        item for item in result.evidence
                        if item.provenance.source == EvidenceSource.github
                        and item.provenance.file_path == recovered_path
                    )
                    self.assertFalse(recovered_evidence.provenance.payload["patch_available"])
                    self.assertTrue(recovered_evidence.provenance.payload["full_file_available"])
                    self.assertEqual(recovered_evidence.provenance.payload["source_blob_sha"], "d" * 40)
                    self.assertFalse(code_source.last_collection_complete)
                else:
                    self.assertFalse(result.snapshot.is_complete)
                    self.assertEqual(result.snapshot.code_diffs, [])
                    self.assertFalse(any(
                        item.provenance.source == EvidenceSource.github for item in result.evidence
                    ))
                    self.assertIsNone(result.target_commit_sha)
                    self.assertEqual(result.snapshot.ci_runs, [])
                    self.assertEqual(ci_source.calls, [])
                    code_record = next(
                        item for item in result.records if item.provider == SourceProvider.github
                    )
                    self.assertEqual(code_record.status, SourceExecutionStatus.failed)
                    self.assertEqual(code_record.error.category, SourceErrorCategory.malformed_payload)
                    self.assertNotIn(
                        SourceProvider.github_actions,
                        {item.provider for item in result.records},
                    )

    def test_full_file_recovery_security_and_access_errors_discard_partial_code(self) -> None:
        path = "styles/site.css"
        contents_url = f"https://api.github.com/repos/owner/repo/contents/{path}?ref={SAMPLE_SHA}"
        secret = "RECOVERY_SECRET_TOKEN"
        cases = (
            ("blob mismatch", HttpResponse(status_code=200, body={
                "encoding": "base64",
                "content": base64.b64encode(b"safe\n").decode(),
                "sha": "c" * 40,
                "size": len(b"safe\n"),
            }), SourceErrorCategory.malformed_payload, None),
            ("secret echo", HttpResponse(status_code=200, body={
                "encoding": "base64",
                "content": base64.b64encode(secret.encode()).decode(),
                "sha": "d" * 40,
                "size": len(secret.encode()),
            }), SourceErrorCategory.malformed_payload, secret),
            ("authentication", HttpResponse(status_code=401, body={}), SourceErrorCategory.authentication, None),
            ("permission", HttpResponse(status_code=403, body={}), SourceErrorCategory.permission_denied, None),
        )
        for label, content_response, expected_category, known_secret in cases:
            with self.subTest(label=label):
                transport = FakeTransport({
                    f"https://api.github.com/repos/owner/repo/contents/src/sibling.py?ref={SAMPLE_SHA}": HttpResponse(
                        status_code=200, body={},
                    ),
                    f"https://api.github.com/repos/owner/repo/commits/{SAMPLE_SHA}": HttpResponse(
                        status_code=200,
                        body={
                            "sha": SAMPLE_SHA,
                            "files": [
                                {
                                    "filename": "src/sibling.py", "status": "modified",
                                    "patch": "@@ -1 +1 @@\n-old\n+new\n",
                                },
                                {
                                    "filename": path, "status": "modified", "patch": None,
                                    "sha": "d" * 40,
                                },
                            ],
                        },
                    ),
                    contents_url: content_response,
                })
                code_source = GitHubCodeSourceAdapter(
                    owner="owner", repo="repo", token=known_secret, transport=transport,
                )
                task, task_ev = _make_sample_task(self.project_id)
                ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
                ci_source = FakeCISource([ci], [ci_ev])
                adapter = CompositeSourcesAdapter(
                    task_source=FakeTaskSource([task], [task_ev]),
                    code_source=code_source,
                    ci_source=ci_source,
                    policy_source=FakePolicySource([_make_sample_policy()]),
                    default_commit_sha=SAMPLE_SHA,
                    known_secret_values=[known_secret] if known_secret else None,
                )

                result = adapter.collect_snapshot(self.project_id, self.cutoff)

                self.assertEqual(result.snapshot.code_diffs, [])
                self.assertFalse(any(item.provenance.source == EvidenceSource.github for item in result.evidence))
                self.assertEqual(result.target_commit_sha, None)
                self.assertEqual(result.snapshot.ci_runs, [])
                self.assertEqual(ci_source.calls, [])
                code_record = next(item for item in result.records if item.provider == SourceProvider.github)
                self.assertEqual(code_record.status, SourceExecutionStatus.failed)
                self.assertEqual(code_record.error.category, expected_category)
                self.assertNotIn(SourceProvider.github_actions, {item.provider for item in result.records})
                if known_secret:
                    self.assertNotIn(known_secret, result.model_dump_json())

    def test_known_secret_in_code_skips_ci(self) -> None:
        """When code payload contains a known secret, code fails and CI is skipped with 0 calls."""
        secret = "SECRET_CODE_TOKEN_123"
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        diff.content = f"+secret_key = '{secret}'\n"
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            known_secret_values=[secret],
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(len(ci_src.calls), 0)
        self.assertNotIn(SourceProvider.github_actions, {r.provider for r in result.records})
        code_rec = next(r for r in result.records if r.provider == SourceProvider.github)
        self.assertEqual(code_rec.status, SourceExecutionStatus.failed)
        self.assertIsNone(result.target_commit_sha)
        self.assertIsNone(adapter.last_resolved_commit_sha)
        self.assertNotIn(secret, result.model_dump_json())
        self.assertEqual(len(result.snapshot.tasks), 1)
        self.assertEqual(len(result.snapshot.policies), 1)

    def test_known_secret_in_resolved_commit_sha_fails_code_and_skips_ci(self) -> None:
        """When resolved commit SHA contains a known secret, code fails, target SHA is cleared, and CI is skipped."""
        secret = "c0ffee" * 5 + "12"  # 32 hex chars
        secret_sha = secret + "00000000"  # 40 hex chars
        task, task_ev = _make_sample_task(self.project_id)
        ci, ci_ev = _make_sample_ci(self.project_id, secret_sha)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(diffs=[], evidence=[], resolved_sha=secret_sha)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            known_secret_values=[secret],
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(len(ci_src.calls), 0)
        self.assertNotIn(SourceProvider.github_actions, {r.provider for r in result.records})
        code_rec = next(r for r in result.records if r.provider == SourceProvider.github)
        self.assertEqual(code_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(code_rec.error.category, SourceErrorCategory.malformed_payload)
        self.assertIsNone(result.target_commit_sha)
        self.assertIsNone(adapter.last_resolved_commit_sha)
        dump = result.model_dump_json()
        self.assertNotIn(secret, dump)
        self.assertNotIn(secret, code_rec.error.message)

    def test_known_secret_in_fallback_commit_sha_fails_code_and_skips_ci(self) -> None:
        """When fallback/target commit SHA contains a known secret, code fails, target SHA is cleared, and CI is skipped."""
        secret = "c0ffee" * 5 + "12"  # 32 hex chars
        secret_sha = secret + "00000000"  # 40 hex chars
        task, task_ev = _make_sample_task(self.project_id)
        ci, ci_ev = _make_sample_ci(self.project_id, secret_sha)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(diffs=[], evidence=[], resolved_sha=None)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            default_commit_sha=secret_sha,
            known_secret_values=[secret],
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(len(ci_src.calls), 0)
        self.assertNotIn(SourceProvider.github_actions, {r.provider for r in result.records})
        code_rec = next(r for r in result.records if r.provider == SourceProvider.github)
        self.assertEqual(code_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(code_rec.error.category, SourceErrorCategory.malformed_payload)
        self.assertIsNone(result.target_commit_sha)
        self.assertIsNone(adapter.last_resolved_commit_sha)
        dump = result.model_dump_json()
        self.assertNotIn(secret, dump)

    def test_known_secret_in_code_evidence_skips_ci_and_clears_target_sha(self) -> None:
        """Safe SHA with empty diffs but metadata evidence containing known secret fails code and clears target SHA."""
        secret = "SECRET_METADATA_TOKEN"
        safe_sha = SAMPLE_SHA
        task, task_ev = _make_sample_task(self.project_id)
        ci, ci_ev = _make_sample_ci(self.project_id, safe_sha)
        policy = _make_sample_policy()
        _, diff_ev = _make_sample_diff(self.project_id, safe_sha)
        diff_ev.summary = f"Evidence containing secret {secret}"

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(diffs=[], evidence=[diff_ev], resolved_sha=safe_sha)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            known_secret_values=[secret],
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(len(ci_src.calls), 0)
        self.assertNotIn(SourceProvider.github_actions, {r.provider for r in result.records})
        code_rec = next(r for r in result.records if r.provider == SourceProvider.github)
        self.assertEqual(code_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(code_rec.error.category, SourceErrorCategory.malformed_payload)
        self.assertIsNone(result.target_commit_sha)
        self.assertIsNone(adapter.last_resolved_commit_sha)
        dump = result.model_dump_json()
        self.assertNotIn(secret, dump)

    def test_code_evidence_helper_failure_skips_ci(self) -> None:
        """When code evidence retrieval fails after diff resolution, CI is skipped with 0 calls."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(
            diffs=[diff],
            evidence=[diff_ev],
            resolved_sha=SAMPLE_SHA,
            evidence_error=SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload),
        )
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(len(ci_src.calls), 0)
        self.assertNotIn(SourceProvider.github_actions, {r.provider for r in result.records})
        self.assertIsNone(adapter.last_resolved_commit_sha)

    def test_ci_preserves_head_sha_mismatches_but_marks_target_incomplete(self) -> None:
        """Preserve actual CI head SHA mismatches; CI record succeeds but snapshot is incomplete."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        # CI run has mismatched SHA
        mismatched_ci, ci_ev = _make_sample_ci(self.project_id, ALT_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([mismatched_ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        # Mismatched run and evidence are preserved
        self.assertEqual(len(result.snapshot.ci_runs), 1)
        self.assertEqual(result.snapshot.ci_runs[0].commit_sha, ALT_SHA)
        # CI execution record is success (GET succeeded)
        ci_record = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.status, SourceExecutionStatus.success)
        # Snapshot is incomplete because no run matches exact target SHA
        self.assertFalse(result.snapshot.is_complete)
        self.assertIn("No continuous integration runs found matching", result.snapshot.incomplete_reason)

    def test_ci_empty_runs_availability_preserved_and_incomplete(self) -> None:
        """Empty CI run list is preserved honestly and snapshot is marked incomplete."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource(runs=[], evidence=[])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.ci_runs, [])
        ci_record = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.status, SourceExecutionStatus.success)
        self.assertIn("No continuous integration runs found matching", result.snapshot.incomplete_reason)

    def test_trello_failure_omits_tasks_and_marks_incomplete(self) -> None:
        """Trello fetch failure marks snapshot incomplete, omits tasks, keeps other sources."""
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource(
            error=SourceError(SourceProvider.trello, SourceErrorCategory.network)
        )
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.tasks, [])
        trello_rec = next(r for r in result.records if r.provider == SourceProvider.trello)
        self.assertEqual(trello_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(trello_rec.error.category, SourceErrorCategory.network)
        # Other sources still present
        self.assertEqual(len(result.snapshot.code_diffs), 1)
        self.assertEqual(len(result.snapshot.ci_runs), 1)
        self.assertEqual(len(result.snapshot.policies), 1)

    def test_trello_evidence_helper_failure_atomic_unit(self) -> None:
        """If Trello evidence helper fails, tasks and evidence are NOT published partially."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource(
            tasks=[task],
            evidence=[task_ev],
            evidence_error=SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload),
        )
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.tasks, [])
        # No trello evidence in combined evidence
        for ev in result.evidence:
            self.assertNotEqual(ev.provenance.source, EvidenceSource.trello)
        trello_rec = next(r for r in result.records if r.provider == SourceProvider.trello)
        self.assertEqual(trello_rec.status, SourceExecutionStatus.failed)

    def test_code_fetch_failure_skips_ci_and_continues_others(self) -> None:
        """When GitHub code collection fails, CI is skipped without calling ci_source."""
        task, task_ev = _make_sample_task(self.project_id)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource(
            error=SourceError(SourceProvider.github, SourceErrorCategory.network)
        )
        ci_src = FakeCISource()
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.code_diffs, [])
        self.assertEqual(len(ci_src.calls), 0)  # CI was NOT called
        self.assertEqual(len(result.snapshot.tasks), 1)
        self.assertEqual(len(result.snapshot.policies), 1)

    def test_policy_failure_no_fixture_fallback(self) -> None:
        """When policy source fails, no fixture fallback occurs; policies are empty and recorded failed."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource(
            error=SourceError(SourceProvider.local_policy, SourceErrorCategory.not_found)
        )

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.policies, [])
        pol_rec = next(r for r in result.records if r.provider == SourceProvider.local_policy)
        self.assertEqual(pol_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(pol_rec.error.category, SourceErrorCategory.not_found)

    def test_mismatched_provider_canonical_reattribution(self) -> None:
        """If an injected adapter raises SourceError with a mismatched provider, reattribute canonically."""
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        # Trello erroneously raises SourceError with provider=github
        tasks_src = FakeTaskSource(
            error=SourceError(SourceProvider.github, SourceErrorCategory.network)
        )
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        trello_rec = next(r for r in result.records if r.provider == SourceProvider.trello)
        self.assertEqual(trello_rec.provider, SourceProvider.trello)
        self.assertEqual(trello_rec.error.provider, SourceProvider.trello)

    def test_unexpected_exception_canonicalized(self) -> None:
        """Unexpected raw exceptions are canonicalized to safe categories without raw diagnostics."""
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource(
            error=RuntimeError("internal secret-bearing path /root/.secret")
        )
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        trello_rec = next(r for r in result.records if r.provider == SourceProvider.trello)
        self.assertEqual(trello_rec.status, SourceExecutionStatus.failed)
        self.assertNotIn("secret", trello_rec.error.message)
        self.assertNotIn("root", trello_rec.error.message)
        self.assertEqual(
            trello_rec.error.message,
            "Source 'trello' failed with error category 'malformed_payload'.",
        )

    def test_timezone_aware_cutoff_required(self) -> None:
        """Naive cutoff_datetime raises ValueError before calling any source."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        naive_cutoff = datetime(2026, 10, 7, 0, 0)
        with self.assertRaises(ValueError):
            adapter.collect_snapshot(self.project_id, naive_cutoff)

        self.assertEqual(len(tasks_src.calls), 0)
        self.assertEqual(len(code_src.calls), 0)

    def test_utc_cutoff_and_actual_retrieval_capture_time(self) -> None:
        """Snapshot retains requested cutoff and actual UTC capture time without backdating."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        guayaquil_cutoff = datetime(2026, 10, 6, 19, 0, tzinfo=ZoneInfo("America/Guayaquil"))
        before = datetime.now(timezone.utc)
        result = adapter.collect_snapshot(self.project_id, guayaquil_cutoff)
        after = datetime.now(timezone.utc)

        self.assertEqual(result.snapshot.cutoff_datetime, guayaquil_cutoff)
        self.assertGreaterEqual(result.snapshot.captured_at, before)
        self.assertLessEqual(result.snapshot.captured_at, after)

    def test_collection_lifecycle_invalidates_provider_cache(self) -> None:
        """collect_snapshot invalidates provider caches before collection to prevent stale tip."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertIn(self.project_id, tasks_src.invalidated_projects)
        self.assertIn(self.project_id, code_src.invalidated_projects)
        self.assertIn(self.project_id, ci_src.invalidated_projects)

    def test_get_evidence_from_cached_collection_without_refetch(self) -> None:
        """get_evidence accesses cached collection without triggering fresh provider queries."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertEqual(len(tasks_src.calls), 1)

        evidence = adapter.get_evidence(self.project_id)
        self.assertEqual(len(evidence), 3)
        # Did not trigger a second get_tasks call
        self.assertEqual(len(tasks_src.calls), 1)

    def test_mutation_isolation_deep_copies(self) -> None:
        """Caller mutating returned snapshot or evidence does not affect cached collection."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result1 = adapter.collect_snapshot(self.project_id, self.cutoff)
        result1.snapshot.tasks.clear()
        result1.evidence.clear()

        # Second accessor call retains original cached items
        evidence2 = adapter.get_evidence(self.project_id)
        self.assertEqual(len(evidence2), 3)

    def test_no_stale_sha_reused_on_subsequent_failure(self) -> None:
        """When a subsequent code fetch fails, no stale target SHA from prior success is reused."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        res1 = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertEqual(res1.target_commit_sha, SAMPLE_SHA)
        self.assertEqual(len(tasks_src.calls), 1)

        # Repeated identical collection returns deepcopy of frozen result without refetch
        res_repeat = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertEqual(res_repeat.target_commit_sha, SAMPLE_SHA)
        self.assertEqual(res_repeat.snapshot.captured_at, res1.snapshot.captured_at)
        self.assertEqual(len(tasks_src.calls), 1)

        # Differing request on same instance is safely rejected
        diff_cutoff = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            adapter.collect_snapshot(self.project_id, diff_cutoff)

        with self.assertRaises(ValueError):
            adapter.collect_snapshot("other-project", self.cutoff)

        with self.assertRaises(ValueError):
            adapter.collect_snapshot(self.project_id, self.cutoff, commit_sha=ALT_SHA)

        # Fresh instance for subsequent run with failed code fetch has no stale SHA
        fresh_code_src = FakeCodeSource(
            error=SourceError(SourceProvider.github, SourceErrorCategory.network)
        )
        fresh_adapter = CompositeSourcesAdapter(
            task_source=FakeTaskSource([task], [task_ev]),
            code_source=fresh_code_src,
            ci_source=FakeCISource([ci], [ci_ev]),
            policy_source=FakePolicySource([policy]),
        )
        res_fresh = fresh_adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(res_fresh.snapshot.is_complete)
        self.assertIsNone(res_fresh.target_commit_sha)
        self.assertIsNone(fresh_adapter.last_resolved_commit_sha)

    def test_known_secret_in_project_id_rejected(self) -> None:
        """Known secrets in project_id configuration rejected before source execution."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            known_secret_values=["SUPER_SECRET_TOKEN_XYZ"],
        )

        with self.assertRaises(SourceError) as ctx:
            adapter.collect_snapshot("proj-SUPER_SECRET_TOKEN_XYZ-id", self.cutoff)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
        self.assertEqual(len(tasks_src.calls), 0)

    def test_known_secret_echo_in_source_payload_rejected(self) -> None:
        """Known secret echoing in normalized source task payload is rejected safely."""
        secret = "LEAKED_GEMINI_KEY_999"
        task = TaskCard(
            id="card-1",
            title=f"Task with {secret}",
            state=TaskState.in_progress,
        )
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
            known_secret_values=[secret],
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.tasks, [])
        trello_rec = next(r for r in result.records if r.provider == SourceProvider.trello)
        self.assertEqual(trello_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(trello_rec.error.category, SourceErrorCategory.malformed_payload)

    def test_safe_repr_no_secret_leak(self) -> None:
        """repr(adapter) does not expose injected known secrets."""
        secret = "SUPER_SECRET_CREDENTIAL_VALUE"
        adapter = CompositeSourcesAdapter(
            task_source=FakeTaskSource(),
            code_source=FakeCodeSource(),
            ci_source=FakeCISource(),
            policy_source=FakePolicySource(),
            known_secret_values=[secret],
        )
        repr_str = repr(adapter)
        self.assertNotIn(secret, repr_str)

    def test_serialization_model_dump(self) -> None:
        """SourceCollectionResult serializes cleanly via model_dump."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        adapter = CompositeSourcesAdapter(
            task_source=FakeTaskSource([task], [task_ev]),
            code_source=FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA),
            ci_source=FakeCISource([ci], [ci_ev]),
            policy_source=FakePolicySource([policy]),
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        data = result.model_dump()
        self.assertTrue(data["snapshot"]["is_complete"])
        self.assertEqual(len(data["records"]), 4)
        self.assertEqual(len(data["evidence"]), 3)
        self.assertNotIn("metrics_artifact", data["snapshot"]["ci_runs"][0])

    def test_actions_metrics_none_baseline_quality_unknown(self) -> None:
        """Actions run with metrics=None evaluates to unknown under Q1 and Q2 policies."""
        ci_run = CIRunResult(
            id="run-1",
            workflow_name="CI",
            commit_sha=SAMPLE_SHA,
            status="success",
            total_tests=None,
            failed_tests=None,
            coverage_percentage=None,
            timestamp=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        )
        real_policy = LocalYamlPolicyAdapter("config/policies.yaml")
        policies = real_policy.get_policies(self.project_id)

        facts = {
            "failed_tests": ci_run.failed_tests,
            "coverage_percentage": ci_run.coverage_percentage,
            "total_tests": ci_run.total_tests,
        }
        policy_result = evaluate_policies(policies, facts)

        self.assertIn("Q1", policy_result.unknown_rule_ids)
        self.assertIn("Q2", policy_result.unknown_rule_ids)
        self.assertEqual(policy_result.recommended_action, "evidencia_insuficiente")

    def test_evidence_deduplication_preserves_all_items_without_dropping(self) -> None:
        """All evidence items are preserved even under 3+ duplicate or pre-prefixed natural collisions."""
        task, _ = _make_sample_task(self.project_id)
        diff, _ = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, _ = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        # Task source emits 3 items with identical id="shared-id"
        ev_t1 = EvidenceItem(
            provenance=EvidenceProvenance(
                id="shared-id",
                source=EvidenceSource.trello,
                project_id=self.project_id,
                timestamp=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            ),
            category="task",
            summary="Trello task 1",
        )
        ev_t2 = EvidenceItem(
            provenance=EvidenceProvenance(
                id="shared-id",
                source=EvidenceSource.trello,
                project_id=self.project_id,
                timestamp=datetime(2026, 10, 5, 12, 1, tzinfo=timezone.utc),
            ),
            category="task",
            summary="Trello task 2",
        )
        ev_t3 = EvidenceItem(
            provenance=EvidenceProvenance(
                id="shared-id",
                source=EvidenceSource.trello,
                project_id=self.project_id,
                timestamp=datetime(2026, 10, 5, 12, 2, tzinfo=timezone.utc),
            ),
            category="task",
            summary="Trello task 3",
        )

        # Code source emits shared-id AND pre-prefixed natural id "github:shared-id"
        ev_c1 = EvidenceItem(
            provenance=EvidenceProvenance(
                id="shared-id",
                source=EvidenceSource.github,
                project_id=self.project_id,
                commit_sha=SAMPLE_SHA,
                timestamp=datetime(2026, 10, 5, 12, 3, tzinfo=timezone.utc),
            ),
            category="diff",
            summary="GitHub diff 1",
        )
        ev_c2 = EvidenceItem(
            provenance=EvidenceProvenance(
                id="github:shared-id",
                source=EvidenceSource.github,
                project_id=self.project_id,
                commit_sha=SAMPLE_SHA,
                timestamp=datetime(2026, 10, 5, 12, 4, tzinfo=timezone.utc),
            ),
            category="diff",
            summary="GitHub natural pre-prefixed",
        )

        # CI source emits shared-id
        ev_ci = EvidenceItem(
            provenance=EvidenceProvenance(
                id="shared-id",
                source=EvidenceSource.github_actions,
                project_id=self.project_id,
                commit_sha=SAMPLE_SHA,
                external_id=ci.id,
                timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
            ),
            category="ci_run",
            summary="CI run 1",
        )

        tasks_src = FakeTaskSource([task], [ev_t1, ev_t2, ev_t3])
        code_src = FakeCodeSource([diff], [ev_c1, ev_c2], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ev_ci])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        # Total items collected must be preserved: 3 (trello) + 2 (github) + 1 (ci) = 6
        self.assertEqual(len(result.evidence), 6)
        ids = [e.provenance.id for e in result.evidence]
        # All assigned IDs must be distinct
        self.assertEqual(len(set(ids)), 6)
        # Natural pre-prefixed ID is preserved
        self.assertIn("github:shared-id", ids)

    def test_ci_evidence_contradicts_run_sha_fails_canonical(self) -> None:
        """When CI evidence contradicts the corresponding run's actual commit SHA, CI fails canonically."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, _ = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        # Evidence claims ALT_SHA while run has SAMPLE_SHA
        bad_ci_ev = EvidenceItem(
            provenance=EvidenceProvenance(
                id="actions-ci-run-1",
                source=EvidenceSource.github_actions,
                project_id=self.project_id,
                commit_sha=ALT_SHA,
                external_id=ci.id,
                timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
            ),
            category="ci_run",
            summary="Contradictory CI run evidence",
        )

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [bad_ci_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        self.assertEqual(result.snapshot.ci_runs, [])  # Unit rollback
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(ci_rec.error.category, SourceErrorCategory.malformed_payload)

    def test_ci_orphan_evidence_fails_canonical(self) -> None:
        """When CI evidence has category=ci_run with unknown external_id, CI fails canonically."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        orphan_ev = EvidenceItem(
            provenance=EvidenceProvenance(
                id="actions-ci-orphan",
                source=EvidenceSource.github_actions,
                project_id=self.project_id,
                commit_sha=SAMPLE_SHA,
                external_id="nonexistent-run-999",
                timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
            ),
            category="ci_run",
            summary="Orphan CI evidence",
        )

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev, orphan_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(ci_rec.error.category, SourceErrorCategory.malformed_payload)

    def test_ci_missing_run_evidence_fails_canonical(self) -> None:
        """When a CI run has no corresponding ci_run evidence, CI fails canonically."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci1, ci_ev1 = _make_sample_ci(self.project_id, SAMPLE_SHA)
        ci2 = CIRunResult(
            id="run-2",
            workflow_name="CI-2",
            commit_sha=SAMPLE_SHA,
            status="success",
        )
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci1, ci2], [ci_ev1])  # ci2 has no evidence
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertFalse(result.snapshot.is_complete)
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.failed)
        self.assertEqual(ci_rec.error.category, SourceErrorCategory.malformed_payload)

    def test_ci_summary_evidence_accepted(self) -> None:
        """When CI emits category!=ci_run summary evidence matching target SHA, it is validated."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci, ci_ev = _make_sample_ci(self.project_id, SAMPLE_SHA)
        summary_ev = EvidenceItem(
            provenance=EvidenceProvenance(
                id="ci_summary",
                source=EvidenceSource.github_actions,
                project_id=self.project_id,
                commit_sha=SAMPLE_SHA,
                timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc),
            ),
            category="ci_summary",
            summary="CI execution query summary",
        )
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci], [ci_ev, summary_ev])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertTrue(result.snapshot.is_complete)
        self.assertEqual(len(result.snapshot.ci_runs), 1)
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.success)

    def test_ci_mixed_matching_and_mismatched_runs(self) -> None:
        """Mixed matching and mismatched runs retain all data and mark snapshot complete for matching target."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        ci_match, ci_ev_match = _make_sample_ci(self.project_id, SAMPLE_SHA)
        ci_mismatch, ci_ev_mismatch = _make_sample_ci(self.project_id, ALT_SHA)
        ci_mismatch.id = "run-2"
        ci_ev_mismatch = EvidenceItem(
            provenance=EvidenceProvenance(
                id="actions-ci-run-2",
                source=EvidenceSource.github_actions,
                project_id=self.project_id,
                commit_sha=ALT_SHA,
                external_id="run-2",
                timestamp=datetime(2026, 10, 5, 12, 6, tzinfo=timezone.utc),
            ),
            category="ci_run",
            summary="Mismatched CI run 2",
        )
        policy = _make_sample_policy()

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        ci_src = FakeCISource([ci_match, ci_mismatch], [ci_ev_match, ci_ev_mismatch])
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=ci_src,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertTrue(result.snapshot.is_complete)
        self.assertEqual(len(result.snapshot.ci_runs), 2)
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.success)

    def test_real_actions_adapter_fake_transport_integration(self) -> None:
        """Integration with real GitHubActionsSourceAdapter and FakeTransport succeeds with no false positives."""
        task, task_ev = _make_sample_task(self.project_id)
        diff, diff_ev = _make_sample_diff(self.project_id, SAMPLE_SHA)
        policy = _make_sample_policy()

        runs_url = "https://api.github.com/repos/owner/repo/actions/runs"
        api_response = HttpResponse(
            status_code=200,
            body={
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 123456,
                        "name": "CI Workflow",
                        "head_sha": SAMPLE_SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "main",
                        "event": "push",
                        "created_at": "2026-10-05T12:00:00Z",
                        "updated_at": "2026-10-05T12:05:00Z",
                        "run_started_at": "2026-10-05T12:01:00Z",
                        "workflow_id": 789,
                    }
                ],
            },
        )
        transport = FakeTransport(responses={runs_url: api_response})
        real_actions = GitHubActionsSourceAdapter(
            owner="owner",
            repo="repo",
            transport=transport,
        )

        tasks_src = FakeTaskSource([task], [task_ev])
        code_src = FakeCodeSource([diff], [diff_ev], resolved_sha=SAMPLE_SHA)
        policy_src = FakePolicySource([policy])

        adapter = CompositeSourcesAdapter(
            task_source=tasks_src,
            code_source=code_src,
            ci_source=real_actions,
            policy_source=policy_src,
        )

        result = adapter.collect_snapshot(self.project_id, self.cutoff)
        self.assertTrue(result.snapshot.is_complete)
        self.assertEqual(len(result.snapshot.ci_runs), 1)
        self.assertEqual(result.snapshot.ci_runs[0].commit_sha, SAMPLE_SHA)
        ci_rec = next(r for r in result.records if r.provider == SourceProvider.github_actions)
        self.assertEqual(ci_rec.status, SourceExecutionStatus.success)


if __name__ == "__main__":
    unittest.main()
