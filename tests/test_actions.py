"""Tests for GitHub Actions read-only CI source adapter (T4)."""

from datetime import datetime, timezone
import unittest
from typing import Any

from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.domain.code import CIRunResult, match_ci_for_commit
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.domain.policies import (
    QualityRule,
    RuleEvaluationStatus,
    evaluate_policies,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import CISourcePort

# Import under test - will fail RED before implementation exists
from agente_gestion.adapters.sources.actions import (
    GitHubActionsSourceAdapter,
    DEFAULT_MAX_RUNS,
    MAX_ACTIONS_RUNS_CEILING,
)


class FakeTransport(HttpGetTransportPort):
    """Deterministic fake HTTP GET transport capturing requests and routing responses."""

    def __init__(
        self,
        responses: dict[str, HttpResponse | Exception] | None = None,
    ):
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        call_record = {
            "url": url,
            "params": dict(params) if params else None,
            "headers": dict(headers) if headers else None,
            "timeout_seconds": timeout_seconds,
        }
        self.calls.append(call_record)

        # Route matching: exact url with params or base url
        resp = self.responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        if resp is not None:
            return resp

        # Check by base URL without query
        base_url = url.split("?")[0]
        resp = self.responses.get(base_url)
        if isinstance(resp, Exception):
            raise resp
        if resp is not None:
            return resp

        return HttpResponse(status_code=200, body={"total_count": 0, "workflow_runs": []})


def _make_run_payload(
    run_id: int = 1001,
    workflow_name: str = "CI Tests",
    head_sha: str = "a" * 40,
    status: str = "completed",
    conclusion: str | None = "success",
    head_branch: str | None = "main",
    event: str = "push",
    created_at: str = "2026-10-01T12:00:00Z",
    updated_at: str = "2026-10-01T12:05:00Z",
    run_started_at: str | None = "2026-10-01T12:00:10Z",
    workflow_id: int = 42,
) -> dict[str, Any]:
    """Helper creating a valid GitHub Actions workflow run dictionary."""
    return {
        "id": run_id,
        "name": workflow_name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "head_branch": head_branch,
        "event": event,
        "created_at": created_at,
        "updated_at": updated_at,
        "run_started_at": run_started_at,
        "workflow_id": workflow_id,
        "url": f"https://api.github.com/repos/octo-org/octo-repo/actions/runs/{run_id}",
        "html_url": f"https://github.com/octo-org/octo-repo/actions/runs/{run_id}",
    }


class TestGitHubActionsSourceAdapter(unittest.TestCase):
    """Comprehensive test suite for GitHubActionsSourceAdapter."""

    def setUp(self):
        self.owner = "test-org"
        self.repo = "test-repo"
        self.token = "ghp_secret_token_value_12345"
        self.valid_sha_1 = "a" * 40
        self.valid_sha_2 = "b" * 40
        self.runs_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/actions/runs"

    # 1. Port contract and representation
    def test_implements_ci_source_port(self):
        transport = FakeTransport()
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        self.assertIsInstance(adapter, CISourcePort)
        self.assertIsNone(adapter.last_target_commit_sha)
        self.assertFalse(adapter.last_collection_complete)

    def test_safe_repr_hides_token(self):
        transport = FakeTransport()
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            commit_sha=self.valid_sha_1,
            transport=transport,
        )
        rep = repr(adapter)
        self.assertIn(self.owner, rep)
        self.assertIn(self.repo, rep)
        self.assertNotIn(self.token, rep)

    # 2. Configuration validation and secret echo checks
    def test_invalid_owner_repo_rejected(self):
        for bad_owner in ("", "   ", "owner/with/slash", "owner\nnewline", "owner;inject"):
            with self.subTest(owner=bad_owner):
                with self.assertRaises(SourceError) as ctx:
                    GitHubActionsSourceAdapter(owner=bad_owner, repo=self.repo)
                self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        for bad_repo in ("", "   ", "repo/slash", "repo\rnewline", ".", ".."):
            with self.subTest(repo=bad_repo):
                with self.assertRaises(SourceError) as ctx:
                    GitHubActionsSourceAdapter(owner=self.owner, repo=bad_repo)
                self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_invalid_budget_limits_rejected(self):
        for bad_runs in (-1, 0, 1001, "10", True):
            with self.subTest(max_runs=bad_runs):
                with self.assertRaises(SourceError) as ctx:
                    GitHubActionsSourceAdapter(
                        owner=self.owner, repo=self.repo, max_runs=bad_runs  # type: ignore
                    )
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_credential_echo_in_config_rejected(self):
        # Token in owner
        with self.assertRaises(SourceError) as ctx:
            GitHubActionsSourceAdapter(
                owner=f"org-{self.token}",
                repo=self.repo,
                token=self.token,
            )
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Token in repo
        with self.assertRaises(SourceError) as ctx:
            GitHubActionsSourceAdapter(
                owner=self.owner,
                repo=f"repo-{self.token}",
                token=self.token,
            )
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Token in constructor commit_sha
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, token=self.token)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results(f"proj-{self.token}", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    # 3. Target SHA requirements (No floating branch/HEAD, full 40/64 hex required)
    def test_missing_commit_sha_raises_configuration_without_network(self):
        transport = FakeTransport()
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=None)
        self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
        # Invariant: NEVER touch network when target SHA is missing
        self.assertEqual(len(transport.calls), 0)

    def test_invalid_commit_sha_format_raises_without_network(self):
        transport = FakeTransport()
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        for bad_sha in ("short", "a" * 39, "g" * 40, "a" * 41, "HEAD", "main", "a" * 40 + "\n"):
            with self.subTest(bad_sha=bad_sha):
                with self.assertRaises(SourceError) as ctx:
                    adapter.get_ci_results("project-1", commit_sha=bad_sha)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
                self.assertEqual(len(transport.calls), 0)

    def test_commit_sha_from_constructor_used_when_method_omits_it(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            commit_sha=self.valid_sha_1,
            transport=transport,
        )
        runs = adapter.get_ci_results("project-1", commit_sha=None)
        self.assertEqual(runs, [])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["params"]["head_sha"], self.valid_sha_1)

    def test_method_commit_sha_overrides_constructor_sha(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            commit_sha=self.valid_sha_1,
            transport=transport,
        )
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_2)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["params"]["head_sha"], self.valid_sha_2)

    def test_sha_hex_case_normalized_to_lowercase(self):
        mixed_sha = ("A" * 20) + ("b" * 20)
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        adapter.get_ci_results("project-1", commit_sha=mixed_sha)
        self.assertEqual(transport.calls[0]["params"]["head_sha"], mixed_sha.lower())

    # 4. HTTP Headers and query parameters
    def test_headers_and_query_parameters(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            transport=transport,
        )
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        call = transport.calls[0]
        self.assertEqual(call["url"], self.runs_url)
        self.assertEqual(
            call["params"],
            {"head_sha": self.valid_sha_1, "per_page": 100, "page": 1},
        )
        self.assertEqual(call["headers"]["Accept"], "application/vnd.github+json")
        self.assertEqual(call["headers"]["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {self.token}")

    def test_public_repo_no_token_omits_authorization_header(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=None,
            transport=transport,
        )
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        headers = transport.calls[0]["headers"]
        self.assertNotIn("Authorization", headers)

    # 5. Preserving returned runs, sorting, and SHA mismatch handling
    def test_preserves_actual_head_sha_mismatch_and_exact_match(self):
        """
        ACTUAL returned run.head_sha must be validated and preserved even if mismatch,
        NEVER overwrite/drop mismatch as if matched. Downstream match_ci_for_commit
        protects reviewed code.
        """
        mismatched_sha = "c" * 40
        matching_run = _make_run_payload(
            run_id=101,
            workflow_name="Unit Tests",
            head_sha=self.valid_sha_1,
            updated_at="2026-10-01T12:00:00Z",
        )
        mismatched_run = _make_run_payload(
            run_id=102,
            workflow_name="Integration Tests",
            head_sha=mismatched_sha,
            updated_at="2026-10-01T12:10:00Z",
        )

        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 2, "workflow_runs": [matching_run, mismatched_run]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        runs = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        self.assertEqual(len(runs), 2)
        # Runs must be sorted newest first by updated_at (102 at 12:10:00 is newer than 101 at 12:00:00)
        self.assertEqual(runs[0].id, "102")
        self.assertEqual(runs[0].commit_sha, mismatched_sha)  # preserved, not overwritten!
        self.assertEqual(runs[1].id, "101")
        self.assertEqual(runs[1].commit_sha, self.valid_sha_1)

        # Invariant: downstream exact match check finds ONLY the matching run
        matched = match_ci_for_commit(runs, self.valid_sha_1)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, "101")
        self.assertEqual(matched.commit_sha, self.valid_sha_1)

        # Invariant: downstream exact match check for mismatched_sha does NOT certify valid_sha_1
        matched_other = match_ci_for_commit(runs, mismatched_sha)
        self.assertIsNotNone(matched_other)
        self.assertEqual(matched_other.id, "102")

        # Check evidence records requested and actual SHA + match boolean
        evidence = adapter.get_evidence("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(evidence), 2)
        # First evidence is for run 102
        ev_102 = next(e for e in evidence if e.provenance.external_id == "102")
        self.assertEqual(ev_102.provenance.payload["requested_target_sha"], self.valid_sha_1)
        self.assertEqual(ev_102.provenance.payload["head_sha"], mismatched_sha)
        self.assertFalse(ev_102.provenance.payload["is_target_match"])

        # Second evidence is for run 101
        ev_101 = next(e for e in evidence if e.provenance.external_id == "101")
        self.assertEqual(ev_101.provenance.payload["requested_target_sha"], self.valid_sha_1)
        self.assertEqual(ev_101.provenance.payload["head_sha"], self.valid_sha_1)
        self.assertTrue(ev_101.provenance.payload["is_target_match"])

    # 6. Status and conclusion normalization
    def test_status_conclusion_normalization(self):
        cases = [
            ("completed", "success", "success"),
            ("completed", "failure", "failure"),
            ("completed", "cancelled", "cancelled"),
            ("completed", "skipped", "skipped"),
            ("completed", "neutral", "neutral"),
            ("completed", "timed_out", "timed_out"),
            ("in_progress", None, "in_progress"),
            ("queued", None, "queued"),
            ("waiting", None, "waiting"),
        ]

        runs_payload = [
            _make_run_payload(
                run_id=200 + i,
                workflow_name=f"Workflow {i}",
                head_sha=self.valid_sha_1,
                status=st,
                conclusion=conc,
                updated_at=f"2026-10-01T12:{i:02d}:00Z",
            )
            for i, (st, conc, _) in enumerate(cases)
        ]

        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": len(runs_payload), "workflow_runs": runs_payload},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        results = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        self.assertEqual(len(results), len(cases))
        results_by_id = {r.id: r for r in results}
        for i, (st, conc, expected_norm) in enumerate(cases):
            run = results_by_id[str(200 + i)]
            self.assertEqual(run.status, expected_norm)

        # Provenance retains BOTH status and conclusion
        evidence = adapter.get_evidence("project-1", commit_sha=self.valid_sha_1)
        ev_by_id = {e.provenance.external_id: e for e in evidence}
        for i, (st, conc, _) in enumerate(cases):
            ev = ev_by_id[str(200 + i)]
            self.assertEqual(ev.provenance.payload["status"], st)
            self.assertEqual(ev.provenance.payload["conclusion"], conc)

    # 7. Honest metrics (ALWAYS None) and QualityEvaluator demonstration
    def test_honest_ci_metrics_always_none(self):
        run_data = _make_run_payload(
            run_id=301,
            workflow_name="Tests",
            head_sha=self.valid_sha_1,
            status="completed",
            conclusion="success",
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [run_data]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        results = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        run = results[0]
        # Invariant: Even for conclusion='success', metrics must be None!
        self.assertIsNone(run.total_tests)
        self.assertIsNone(run.failed_tests)
        self.assertIsNone(run.coverage_percentage)
        self.assertIsNone(run.timestamp)

    def test_policy_evaluator_demonstration_mandatory_q1_q2_yield_evidencia_insuficiente(self):
        """
        Demonstrate that with unknown CI test counts and coverage,
        mandatory baseline rules Q1 and Q2 evaluate to 'unknown',
        resulting in recommended_action='evidencia_insuficiente'.
        """
        run_data = _make_run_payload(
            run_id=302,
            workflow_name="Tests",
            head_sha=self.valid_sha_1,
            status="completed",
            conclusion="success",
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [run_data]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        results = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        run = results[0]

        # Extract facts from CIRunResult
        facts = {
            "failed_tests": run.failed_tests,
            "coverage_percentage": run.coverage_percentage,
            "total_tests": run.total_tests,
        }

        # Baseline mandatory policy rules
        q1_rule = QualityRule(
            id="Q1",
            description="No failed tests",
            metric_key="failed_tests",
            operator="<=",
            threshold=0,
            source="ci",
            mandatory=True,
        )
        q2_rule = QualityRule(
            id="Q2",
            description="Test coverage >= 80%",
            metric_key="coverage_percentage",
            operator=">=",
            threshold=80.0,
            source="ci",
            mandatory=True,
        )

        policy_result = evaluate_policies([q1_rule, q2_rule], facts)

        # Invariant: Neither Q1 nor Q2 passes; both are unknown
        self.assertEqual(policy_result.passed_rule_ids, [])
        self.assertEqual(policy_result.failed_rule_ids, [])
        self.assertIn("Q1", policy_result.unknown_rule_ids)
        self.assertIn("Q2", policy_result.unknown_rule_ids)
        self.assertIn("Q1", policy_result.mandatory_unknown_ids)
        self.assertIn("Q2", policy_result.mandatory_unknown_ids)
        self.assertEqual(policy_result.recommended_action, "evidencia_insuficiente")

    # 8. Timestamps, sorting, and naive timestamp rejection
    def test_utc_aware_iso_timestamps_and_sorting(self):
        run_old = _make_run_payload(
            run_id=401,
            workflow_name="Old",
            head_sha=self.valid_sha_1,
            updated_at="2026-10-01T10:00:00Z",
        )
        run_new = _make_run_payload(
            run_id=402,
            workflow_name="New",
            head_sha=self.valid_sha_1,
            updated_at="2026-10-01T12:00:00+02:00",  # 10:00:00 UTC, but higher run ID
        )
        run_newest = _make_run_payload(
            run_id=403,
            workflow_name="Newest",
            head_sha=self.valid_sha_1,
            updated_at="2026-10-01T15:00:00Z",
        )

        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 3, "workflow_runs": [run_old, run_newest, run_new]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        results = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        # Sorted newest first: 403 (15:00) then 402 (10:00 tiebreak id=402) then 401 (10:00 tiebreak id=401)
        self.assertEqual([r.id for r in results], ["403", "402", "401"])

    def test_naive_timestamp_rejected(self):
        # Naive timestamp without 'Z' or offset
        bad_run = _make_run_payload(
            run_id=404,
            head_sha=self.valid_sha_1,
            updated_at="2026-10-01T10:00:00",  # missing tz
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [bad_run]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    # 9. Pagination, total_count ceiling, and Link header validation
    def test_pagination_success_with_valid_rel_next_link(self):
        page1_runs = [
            _make_run_payload(run_id=i + 1, head_sha=self.valid_sha_1, updated_at=f"2026-10-01T10:{i % 60:02d}:00Z")
            for i in range(100)
        ]
        page2_runs = [
            _make_run_payload(run_id=101 + i, head_sha=self.valid_sha_1, updated_at=f"2026-10-01T11:{i % 60:02d}:00Z")
            for i in range(25)
        ]

        link_header = (
            f'<https://api.github.com/repos/{self.owner}/{self.repo}/actions/runs?'
            f'head_sha={self.valid_sha_1}&page=2&per_page=100>; rel="next"'
        )

        def handler(url: str, params=None, headers=None, timeout_seconds=None):
            page = (params or {}).get("page", 1)
            if page == 1:
                return HttpResponse(
                    status_code=200,
                    body={"total_count": 125, "workflow_runs": page1_runs},
                    headers={"link": link_header},
                )
            elif page == 2:
                return HttpResponse(
                    status_code=200,
                    body={"total_count": 125, "workflow_runs": page2_runs},
                    headers={},
                )
            raise RuntimeError("Unexpected page")

        class RoutingTransport(HttpGetTransportPort):
            def __init__(self):
                self.calls = []

            def get(self, url, params=None, headers=None, timeout_seconds=None):
                self.calls.append({"url": url, "params": params, "headers": headers})
                return handler(url, params, headers, timeout_seconds)

        transport = RoutingTransport()
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        runs = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)

        self.assertEqual(len(runs), 125)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0]["params"]["page"], 1)
        self.assertEqual(transport.calls[1]["params"]["page"], 2)

    def test_total_count_ceiling_1000_fails_limit_exceeded(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1000, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)

    def test_total_count_changes_during_pagination_fails(self):
        page1_runs = [
            _make_run_payload(run_id=i + 1, head_sha=self.valid_sha_1) for i in range(100)
        ]
        link_header = (
            f'<https://api.github.com/repos/{self.owner}/{self.repo}/actions/runs?'
            f'head_sha={self.valid_sha_1}&page=2&per_page=100>; rel="next"'
        )

        class InconsistentTransport(HttpGetTransportPort):
            def __init__(self):
                self.page = 1

            def get(self, url, params=None, headers=None, timeout_seconds=None):
                if self.page == 1:
                    self.page += 1
                    return HttpResponse(
                        status_code=200,
                        body={"total_count": 110, "workflow_runs": page1_runs},
                        headers={"link": link_header},
                    )
                return HttpResponse(
                    status_code=200,
                    body={"total_count": 120, "workflow_runs": [_make_run_payload(run_id=200)]},  # Changed!
                    headers={},
                )

        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, transport=InconsistentTransport()
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_missing_rel_next_before_declared_total_fails(self):
        page1_runs = [
            _make_run_payload(run_id=i + 1, head_sha=self.valid_sha_1) for i in range(100)
        ]
        # Total is 150, but no Link header provided on page 1!
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 150, "workflow_runs": page1_runs},
                    headers={},  # Missing link!
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_malicious_link_header_rejected(self):
        page1_runs = [
            _make_run_payload(run_id=i + 1, head_sha=self.valid_sha_1) for i in range(100)
        ]
        # Foreign origin in rel="next"
        bad_link = '<https://evil.com/actions/runs?page=2>; rel="next"'
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 120, "workflow_runs": page1_runs},
                    headers={"link": bad_link},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_duplicate_run_ids_rejected(self):
        run1 = _make_run_payload(run_id=999, head_sha=self.valid_sha_1)
        run2 = _make_run_payload(run_id=999, head_sha=self.valid_sha_1)  # Duplicate!
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 2, "workflow_runs": [run1, run2]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    # 10. Secret echo in response payloads
    def test_secret_echo_in_payload_rejected(self):
        leaky_run = _make_run_payload(
            run_id=501,
            workflow_name=f"Workflow with {self.token}",  # Token echo!
            head_sha=self.valid_sha_1,
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [leaky_run]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, token=self.token, transport=transport
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    # 11. Canonical API provenance URL
    def test_canonical_api_provenance_url(self):
        run_data = _make_run_payload(
            run_id=601,
            workflow_name="CI",
            head_sha=self.valid_sha_1,
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [run_data]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        evidence = adapter.get_evidence("project-1", commit_sha=self.valid_sha_1)

        expected_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/actions/runs/601"
        self.assertEqual(evidence[0].provenance.payload["url"], expected_url)
        self.assertEqual(evidence[0].provenance.source, EvidenceSource.github_actions)
        self.assertEqual(evidence[0].provenance.external_id, "601")
        self.assertEqual(evidence[0].category, "ci_run")

    # 12. Transport error attribution
    def test_transport_error_attribution(self):
        class FailingTransport(HttpGetTransportPort):
            def get(self, url, params=None, headers=None, timeout_seconds=None):
                # Error originating from another provider must be attributed to github_actions
                raise SourceError(SourceProvider.github, SourceErrorCategory.network)

        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, transport=FailingTransport()
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)

    # 13. Cache lifecycle and A-B-A metadata refresh
    def test_cache_lifecycle_and_aba_metadata_republication(self):
        run_a = _make_run_payload(run_id=701, head_sha=self.valid_sha_1)
        run_b = _make_run_payload(run_id=702, head_sha=self.valid_sha_2)

        transport = FakeTransport()

        def handler(url, params=None, headers=None, timeout_seconds=None):
            head_sha = (params or {}).get("head_sha")
            if head_sha == self.valid_sha_1:
                return HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [run_a]},
                )
            elif head_sha == self.valid_sha_2:
                return HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [run_b]},
                )
            raise RuntimeError("Unexpected sha")

        transport.get = handler  # type: ignore

        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        # Query A
        res_a = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(res_a), 1)
        self.assertEqual(res_a[0].id, "701")
        self.assertEqual(adapter.last_target_commit_sha, self.valid_sha_1)
        self.assertTrue(adapter.last_collection_complete)

        # Mutate returned list: cache must remain intact (deep copy)
        res_a[0].workflow_name = "MUTATED"
        res_a_check = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(res_a_check[0].workflow_name, "CI Tests")

        # Query B
        res_b = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_2)
        self.assertEqual(len(res_b), 1)
        self.assertEqual(res_b[0].id, "702")
        self.assertEqual(adapter.last_target_commit_sha, self.valid_sha_2)

        # Query A again (cache hit) -> MUST republish A's metadata, not stale B!
        res_a2 = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(res_a2[0].id, "701")
        self.assertEqual(adapter.last_target_commit_sha, self.valid_sha_1)

        # Invalidate cache for project
        adapter.invalidate_cache("project-1")
        # Invalidate all
        adapter.invalidate_cache()
        self.assertIsNone(adapter.last_target_commit_sha)
        self.assertFalse(adapter.last_collection_complete)

    def test_refresh_invalidates_and_refetches(self):
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(transport.calls), 1)

        # Cache hit: does not call transport
        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(transport.calls), 1)

        # Refresh: forces re-fetch
        adapter.refresh("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(transport.calls), 2)

    def test_get_evidence_without_prior_cache_raises_configuration(self):
        transport = FakeTransport()
        adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_evidence("project-1", commit_sha=None)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_http_error_mappings(self):
        cases = [
            (SourceError(SourceProvider.github_actions, SourceErrorCategory.not_found), SourceErrorCategory.not_found),
            (SourceError(SourceProvider.github_actions, SourceErrorCategory.authentication), SourceErrorCategory.authentication),
            (SourceError(SourceProvider.github_actions, SourceErrorCategory.rate_limit), SourceErrorCategory.rate_limit),
            (SourceError(SourceProvider.github_actions, SourceErrorCategory.network), SourceErrorCategory.network),
            (SourceError(SourceProvider.github_actions, SourceErrorCategory.timeout), SourceErrorCategory.timeout),
        ]
        for injected_err, expected_cat in cases:
            with self.subTest(err=injected_err):
                transport = FakeTransport(responses={self.runs_url: injected_err})
                adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
                with self.assertRaises(SourceError) as ctx:
                    adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
                self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
                self.assertEqual(ctx.exception.category, expected_cat)

    def test_malformed_response_structures(self):
        malformed_bodies = [
            [],  # not a dict
            {"total_count": True, "workflow_runs": []},  # total_count is bool
            {"total_count": -5, "workflow_runs": []},  # negative total_count
            {"total_count": 1, "workflow_runs": "not-a-list"},  # runs not list
            {"total_count": 1, "workflow_runs": ["not-a-dict"]},  # row not dict
            {"total_count": 1, "workflow_runs": [_make_run_payload(run_id=-1)]},  # negative run_id
            {"total_count": 1, "workflow_runs": [_make_run_payload(run_id=True)]},  # bool run_id
            {"total_count": 1, "workflow_runs": [_make_run_payload(workflow_id=-1)]},  # negative workflow_id
            {"total_count": 1, "workflow_runs": [_make_run_payload(workflow_id=True)]},  # bool workflow_id
            {"total_count": 1, "workflow_runs": [_make_run_payload(head_sha="invalid-sha")]},  # bad sha
            {"total_count": 1, "workflow_runs": [_make_run_payload(updated_at=None)]},  # missing updated_at
        ]
        for bad_body in malformed_bodies:
            with self.subTest(bad_body=bad_body):
                transport = FakeTransport(
                    responses={
                        self.runs_url: HttpResponse(
                            status_code=200,
                            body=bad_body,
                        )
                    }
                )
                adapter = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
                with self.assertRaises(SourceError) as ctx:
                    adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_secret_echo_in_branch_event_and_project_rejected(self):
        # Secret in head_branch
        bad_run1 = _make_run_payload(head_branch=f"feature/{self.token}")
        transport1 = FakeTransport(
            responses={self.runs_url: HttpResponse(status_code=200, body={"total_count": 1, "workflow_runs": [bad_run1]})}
        )
        adapter1 = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, token=self.token, transport=transport1)
        with self.assertRaises(SourceError) as ctx:
            adapter1.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        # Secret in event
        bad_run2 = _make_run_payload(event=f"push_{self.token}")
        transport2 = FakeTransport(
            responses={self.runs_url: HttpResponse(status_code=200, body={"total_count": 1, "workflow_runs": [bad_run2]})}
        )
        adapter2 = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, token=self.token, transport=transport2)
        with self.assertRaises(SourceError) as ctx:
            adapter2.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_budget_limits_during_fetch(self):
        # Exceeds max_total_bytes
        huge_response = HttpResponse(
            status_code=200,
            body={"total_count": 0, "workflow_runs": []},
            bytes_received=2000,
        )
        transport = FakeTransport(responses={self.runs_url: huge_response})
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, max_total_bytes=1000, transport=transport
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)

    # 14. Finding 1: Optional date falsey value rejection
    def test_optional_date_falsey_values_rejected(self):
        falsey_values = [False, 0, "", [], {}]
        for field in ("created_at", "run_started_at"):
            for bad_val in falsey_values:
                with self.subTest(field=field, bad_val=bad_val):
                    payload = _make_run_payload(head_sha=self.valid_sha_1)
                    payload[field] = bad_val
                    transport = FakeTransport(
                        responses={
                            self.runs_url: HttpResponse(
                                status_code=200,
                                body={"total_count": 1, "workflow_runs": [payload]},
                            )
                        }
                    )
                    adapter = GitHubActionsSourceAdapter(
                        owner=self.owner, repo=self.repo, transport=transport
                    )
                    with self.assertRaises(SourceError) as ctx:
                        adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
                    self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
                    self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_optional_date_absent_or_null_allowed(self):
        payload = _make_run_payload(head_sha=self.valid_sha_1)
        payload["created_at"] = None
        payload["run_started_at"] = None
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [payload]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, transport=transport
        )
        runs = adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(runs), 1)
        evidence = adapter.get_evidence("project-1", commit_sha=self.valid_sha_1)
        self.assertIsNone(evidence[0].provenance.payload["created_at"])
        self.assertIsNone(evidence[0].provenance.payload["run_started_at"])

    # 15. Finding 2: Numeric workflow_id and date credential echo checks
    def test_workflow_id_credential_echo_rejected(self):
        num_token = "987654"
        payload = _make_run_payload(head_sha=self.valid_sha_1, workflow_id=987654)
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [payload]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, token=num_token, transport=transport
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_date_credential_echo_rejected(self):
        date_token = "2026-10-01"
        payload = _make_run_payload(
            head_sha=self.valid_sha_1,
            updated_at=f"{date_token}T12:00:00Z",
        )
        transport = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [payload]},
                )
            }
        )
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, token=date_token, transport=transport
        )
        with self.assertRaises(SourceError) as ctx:
            adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    # 16. Finding 3: Configured max_text_length enforcement
    def test_max_text_length_enforcement_on_variable_metadata(self):
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner, repo=self.repo, max_text_length=15
        )
        # Variable fields: name, head_branch, event, status, conclusion
        fields_to_test = [
            ("name", "A" * 16),
            ("head_branch", "B" * 16),
            ("event", "C" * 16),
            ("status", "D" * 16),
            ("conclusion", "E" * 16),
        ]
        for field, long_val in fields_to_test:
            with self.subTest(field=field):
                payload = _make_run_payload(head_sha=self.valid_sha_1)
                payload[field] = long_val
                transport = FakeTransport(
                    responses={
                        self.runs_url: HttpResponse(
                            status_code=200,
                            body={"total_count": 1, "workflow_runs": [payload]},
                        )
                    }
                )
                adapter._transport = transport
                with self.assertRaises(SourceError) as ctx:
                    adapter.get_ci_results("project-1", commit_sha=self.valid_sha_1)
                self.assertEqual(ctx.exception.provider, SourceProvider.github_actions)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)

    # 17. Finding 4: Constructor SHA aliases and contradiction handling
    def test_constructor_target_sha_aliases_validation(self):
        # Case equivalent passes
        adapter = GitHubActionsSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            commit_sha=self.valid_sha_1.upper(),
            target_commit_sha=self.valid_sha_1.lower(),
        )
        self.assertEqual(adapter.commit_sha, self.valid_sha_1.lower())

        # Contradictory SHA raises configuration
        with self.assertRaises(SourceError) as ctx:
            GitHubActionsSourceAdapter(
                owner=self.owner,
                repo=self.repo,
                commit_sha=self.valid_sha_1,
                target_commit_sha=self.valid_sha_2,
            )
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Invalid alias type or format rejected
        for bad_alias in ("", "   ", " " + self.valid_sha_1, 12345, False, "short"):
            with self.subTest(bad_alias=bad_alias):
                with self.assertRaises(SourceError) as ctx:
                    GitHubActionsSourceAdapter(
                        owner=self.owner,
                        repo=self.repo,
                        commit_sha=bad_alias,  # type: ignore
                        target_commit_sha=self.valid_sha_1,
                    )
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    # 18. Finding 5: Terminal Link contradiction handling
    def test_terminal_link_contradiction_rejected(self):
        # declared total count is 0, but Link header has rel="next"
        link_next = '<https://api.github.com/repos/test-org/test-repo/actions/runs?head_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&page=2&per_page=100>; rel="next"'
        transport0 = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 0, "workflow_runs": []},
                    headers={"link": link_next},
                )
            }
        )
        adapter0 = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport0)
        with self.assertRaises(SourceError) as ctx:
            adapter0.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        # declared total count reached (1), but Link header has rel="next"
        payload = _make_run_payload(head_sha=self.valid_sha_1)
        transport1 = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [payload]},
                    headers={"link": link_next},
                )
            }
        )
        adapter1 = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport1)
        with self.assertRaises(SourceError) as ctx:
            adapter1.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        # declared total count reached (1), Link header has only rel="prev" (no next) -> valid!
        link_prev = '<https://api.github.com/repos/test-org/test-repo/actions/runs?head_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&page=1&per_page=100>; rel="prev"'
        transport2 = FakeTransport(
            responses={
                self.runs_url: HttpResponse(
                    status_code=200,
                    body={"total_count": 1, "workflow_runs": [payload]},
                    headers={"link": link_prev},
                )
            }
        )
        adapter2 = GitHubActionsSourceAdapter(owner=self.owner, repo=self.repo, transport=transport2)
        runs = adapter2.get_ci_results("project-1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()

