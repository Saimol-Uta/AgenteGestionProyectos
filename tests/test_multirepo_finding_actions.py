"""Public contracts for origin-bound MultiRepo finding decisions."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.control_plane import ControlPlaneRepository
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.finding_actions import router as finding_actions_router
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.api.routes import router as api_router
from agente_gestion.api.security import OperatorSessionStore
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.code_action import BranchExecutionResult
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import FinalReport, RepositoryReportResult
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics


PROJECT_ID = "MULTIREPO-ACTIONS"
RUN_ID = "run-multirepo-actions"
SNAPSHOT_ID = "snapshot-multirepo-actions"
REPORT_ID = "report-multirepo-actions"
OPERATOR_TOKEN = "isolated-test-operator-token-0123456789"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
SOURCE = "def parse(value):\n    return value\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
FILE_PATH = "src/shared.py"


class _FakeGitHubHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        if not self.responses:
            raise AssertionError(f"Unexpected synthetic request: {method} {url}")
        return self.responses.pop(0)


class _RecordingCodeActionPort:
    def __init__(self, execution_mode="sandbox"):
        self.execution_mode = execution_mode
        self.calls = []

    def create_verification_branch(self, **kwargs):
        self.calls.append(kwargs)
        return BranchExecutionResult(
            execution_mode=self.execution_mode,
            branch_name=kwargs["target_branch"],
            branch_url=f"sandbox://branches/{kwargs['target_branch']}",
            commit_sha_applied="d" * 64,
        )


class MultiRepoFindingActionTests(unittest.TestCase):
    def _client(self, root: Path):
        database_path = root / "actions.sqlite"
        storage = SQLiteStorageAdapter(str(database_path))
        identities = (
            RepositoryIdentity(id="frontend", owner="acme", repo="frontend"),
            RepositoryIdentity(id="backend", owner="acme", repo="backend"),
        )
        branches = ("release/frontend", "release/backend")
        findings = []
        source_snippets = []
        repository_reports = []
        repository_snapshots = []
        for identity, branch in zip(identities, branches):
            provenance = RepositoryProvenance(
                repository_identity=identity, commit_sha=COMMIT_SHA, source="multirepo",
            )
            finding_id = f"{identity.id}:f-fake-1"
            finding = CandidateFinding(
                id=finding_id,
                commit_sha=COMMIT_SHA,
                file_path=FILE_PATH,
                line=2,
                evidence="    return value",
                evidence_ids=[COMMIT_SHA],
                suggested_code="    return value.strip()",
                improvement_summary=f"Normalize {identity.id} input.",
                repository_identity=identity,
                provenance=provenance,
            )
            snippet = CodeDiffSnippet(
                repository=f"{identity.owner}/{identity.repo}",
                repository_identity=identity,
                provenance=provenance,
                commit_sha=COMMIT_SHA,
                file_path=FILE_PATH,
                lines=[1, 2],
                content=SOURCE,
                evidence_scope="full_file",
                is_complete=True,
                total_lines=2,
                source_sha256=SOURCE_HASH,
            )
            state = RepositoryAnalysisState(
                identity=identity,
                branch=branch,
                target_commit_sha=COMMIT_SHA,
                commit_resolution_status=CommitResolutionStatus.resolved,
            )
            findings.append(finding)
            source_snippets.append(snippet)
            repository_reports.append(RepositoryReportResult(
                repository_state=state,
                pending_findings=[finding],
            ))
            repository_snapshots.append(RepositorySnapshotResult(
                repository_state=state,
                code_diffs=[snippet],
                is_complete=True,
            ))

        snapshot = WeeklySnapshot(
            id=SNAPSHOT_ID,
            project_id=PROJECT_ID,
            cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
            captured_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            code_diffs=source_snippets,
            repository_results=repository_snapshots,
        )
        storage.save_snapshot(snapshot)
        storage.save_findings(RUN_ID, findings)
        report = FinalReport(
            project_id=PROJECT_ID,
            cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metrics=DeterministicTaskMetrics(total_cards=0, included_cards=0, completed_cards=0),
            pending_findings=findings,
            candidate_findings=findings,
            policy_evaluations=PolicyEvaluationResult(
                evaluations=[], passed_rule_ids=[], failed_rule_ids=[], unknown_rule_ids=[],
                mandatory_failed_ids=[], mandatory_unknown_ids=[], recommended_action="evidencia_insuficiente",
            ),
            recommendation="evidencia_insuficiente",
            summary="Isolated MultiRepo finding action fixture.",
            report_id=REPORT_ID,
            run_id=RUN_ID,
            repository_results=repository_reports,
        )
        storage.save_report(report, report_id=REPORT_ID, run_id=RUN_ID)
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        storage.save_run(ExecutionRun(
            run_id=RUN_ID,
            project_id=PROJECT_ID,
            status=RunStatus.awaiting_human,
            created_at=now,
            updated_at=now,
            snapshot_id=SNAPSHOT_ID,
            config_hash="a" * 64,
            snapshot_hash="b" * 64,
            report_id=REPORT_ID,
        ))

        project = ProjectConfig(
            id=PROJECT_ID,
            name="MultiRepo decision fixture",
            source_mode="fixtures",
            repositories=(
                RepositoryConfiguration(
                    id="frontend", owner="acme", repo="frontend", branch=branches[0],
                ),
                RepositoryConfiguration(
                    id="backend", owner="acme", repo="backend", branch=branches[1],
                ),
            ),
        )
        settings = ApiSettings(
            db_path=str(database_path),
            runs_dir=str(root / "runs"),
            outbox_dir=str(root / "outbox"),
            projects=(project,),
            governance_enabled=True,
            external_writes_enabled=False,
            github_repository="acme/backend",
            github_base_branch="main",
            branches_dir=str(root / "branches"),
            operator_token=OPERATOR_TOKEN,
            operator_project_ids=(PROJECT_ID,),
            allowed_origins=("http://testserver",),
            allowed_hosts=("testserver",),
        )
        app = FastAPI()
        app.state.settings = settings
        app.state.repository = SQLiteQueryRepository(settings)
        app.state.control_plane = ControlPlaneRepository(settings)
        app.state.sessions = OperatorSessionStore(settings)
        port = _RecordingCodeActionPort()
        app.state.code_action_port = port

        @app.exception_handler(ApiProblem)
        async def handle_api_problem(_request: Request, exc: ApiProblem):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

        app.include_router(api_router)
        app.include_router(finding_actions_router)
        return TestClient(app), port, storage, findings

    @staticmethod
    def _authenticate(client):
        login = client.post(
            "/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
            headers={"Origin": "http://testserver"},
        )
        if login.status_code != 200:
            raise AssertionError(login.text)
        return {
            "Origin": "http://testserver",
            "X-CSRF-Token": login.json()["csrf_token"],
        }

    @staticmethod
    def _replace_report_candidate(report, finding):
        def replace(items):
            return [finding if item.id == finding.id else item for item in items]

        repository_results = []
        for result in report.repository_results:
            repository_results.append(result.model_copy(update={
                "confirmed_findings": replace(result.confirmed_findings),
                "rejected_findings": replace(result.rejected_findings),
                "insufficient_findings": replace(result.insufficient_findings),
                "pending_findings": replace(result.pending_findings),
            }))
        return report.model_copy(update={
            "candidate_findings": replace(report.candidate_findings),
            "confirmed_findings": replace(report.confirmed_findings),
            "rejected_findings": replace(report.rejected_findings),
            "insufficient_findings": replace(report.insufficient_findings),
            "pending_findings": replace(report.pending_findings),
            "repository_results": repository_results,
        })

    def test_scoped_finding_ids_are_readable_through_public_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _port, _storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            public_client = TestClient(client.app, raise_server_exceptions=False)
            self.addCleanup(public_client.close)
            finding_id = findings[0].id
            responses = (
                ("list", public_client.get(f"/api/v1/runs/{RUN_ID}/findings")),
                ("detail", public_client.get(f"/api/v1/runs/{RUN_ID}/findings/{finding_id}")),
            )
            observed = [
                {"operation": operation, "status": response.status_code, "body": response.text}
                for operation, response in responses
            ]
            if any(response.status_code != 200 for _operation, response in responses):
                print("SCOPED_FINDING_READBACK_HTTP=" + json.dumps(observed, sort_keys=True))
            self.assertEqual(
                [response.status_code for _operation, response in responses],
                [200, 200],
                json.dumps(observed, sort_keys=True),
            )
            listed = responses[0][1].json()
            detail = responses[1][1].json()
            self.assertIn(finding_id, {item["finding_id"] for item in listed["items"]})
            self.assertEqual(detail["finding_id"], finding_id)

    def test_malformed_finding_ids_remain_rejected_without_business_state_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client, port, storage, _findings = self._client(root)
            self.addCleanup(client.close)
            public_client = TestClient(client.app, raise_server_exceptions=False)
            self.addCleanup(public_client.close)
            malformed_id = "frontend:::f-fake-1"
            before_database = (root / "actions.sqlite").read_bytes()
            before_findings = storage.get_findings(RUN_ID)
            before_audit = storage.list_security_audit_events(PROJECT_ID)

            detail = public_client.get(
                f"/api/v1/runs/{RUN_ID}/findings/{malformed_id}"
            )
            decision = public_client.post(
                f"/api/v1/runs/{RUN_ID}/findings/{malformed_id}/decision",
                headers={"Idempotency-Key": "malformed-finding-0001"},
                json={"decision": "approve", "reason": "Malformed ID must not be actionable."},
            )
            observed = [
                {"operation": "detail", "status": detail.status_code, "body": detail.text},
                {"operation": "decision", "status": decision.status_code, "body": decision.text},
            ]
            self.assertEqual(
                [detail.status_code, decision.status_code], [422, 422],
                json.dumps(observed, sort_keys=True),
            )
            self.assertEqual(storage.get_findings(RUN_ID), before_findings)
            self.assertEqual(storage.list_security_audit_events(PROJECT_ID), before_audit)
            self.assertEqual((root / "actions.sqlite").read_bytes(), before_database)
            self.assertEqual(port.calls, [])

    def test_approval_gate_blocks_sandbox_and_live_without_calling_the_port(self):
        for execution_mode, source_mode, external_writes in (
            ("sandbox", "fixtures", False), ("live", "real", True),
        ):
            with self.subTest(execution_mode=execution_mode), tempfile.TemporaryDirectory() as tmp:
                client, _default_port, storage, findings = self._client(Path(tmp))
                self.addCleanup(client.close)
                port = _RecordingCodeActionPort(execution_mode=execution_mode)
                client.app.state.code_action_port = port
                settings = client.app.state.settings
                project = settings.projects[0].model_copy(update={"source_mode": source_mode})
                client.app.state.settings = settings.model_copy(update={
                    "projects": (project,), "external_writes_enabled": external_writes,
                })
                finding = findings[0]
                auth = self._authenticate(client)
                headers = {**auth, "Idempotency-Key": f"approval-gate-{execution_mode}-0001"}
                path = f"/api/v1/runs/{RUN_ID}/findings/{finding.id}/decision"
                body = {
                    "decision": "approve", "suggested_code_override": "    return value.strip()",
                    "reason": "Un defecto confirmado no revisa el parche correctivo exacto.",
                }
                before_report = storage.get_report(REPORT_ID).model_dump_json()
                before_findings = storage.get_findings(RUN_ID)

                for _attempt in range(2):
                    response = client.post(path, headers=headers, json=body)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
                    self.assertEqual(
                        response.json()["error"]["message"],
                        "No se puede aprobar ni ejecutar esta corrección de GitHub: requiere una "
                        "revisión independiente favorable y vigente, vinculada al parche exacto "
                        "que se aplicaría. Un defecto confirmado, una sintaxis válida o unas "
                        "pruebas aprobadas no la sustituyen.",
                    )

                self.assertEqual(storage.get_report(REPORT_ID).model_dump_json(), before_report)
                self.assertEqual(storage.get_findings(RUN_ID), before_findings)
                self.assertEqual(port.calls, [])
                with closing(sqlite3.connect(Path(tmp) / "actions.sqlite")) as connection:
                    counts = tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                   for table in ("finding_decisions", "finding_decision_requests", "security_audit_log"))
                self.assertEqual(counts, (0, 0, 0))


    def test_reject_and_request_changes_are_scoped_replayable_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, port, _storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            auth = self._authenticate(client)
            cases = (
                (findings[0], "request_changes", "multirepo-changes-0001"),
                (findings[1], "reject", "multirepo-reject-0001"),
            )
            for finding, decision, key in cases:
                with self.subTest(decision=decision):
                    path = f"/api/v1/runs/{RUN_ID}/findings/{finding.id}/decision"
                    headers = {**auth, "Idempotency-Key": key}
                    body = {"decision": decision, "reason": f"Reviewed {finding.id}."}
                    response = client.post(path, headers=headers, json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual(result["repository_id"], finding.repository_identity.id)
                    self.assertEqual(result["source_commit_sha"], COMMIT_SHA)
                    self.assertEqual(result["decision_status"], (
                        "changes_requested" if decision == "request_changes" else "rejected"
                    ))
                    audit = client.get(
                        f"/api/v1/projects/{PROJECT_ID}/audit/{result['audit_event_id']}"
                    )
                    self.assertEqual(audit.status_code, 200, audit.text)
                    self.assertEqual(audit.json()["details"]["finding_id"], finding.id)
                    self.assertEqual(audit.json()["details"]["repository_id"], finding.repository_identity.id)
                    self.assertEqual(audit.json()["details"]["source_commit_sha"], COMMIT_SHA)
                    replay = client.post(path, headers=headers, json=body)
                    self.assertEqual(replay.status_code, 200, replay.text)
                    self.assertTrue(replay.json()["reused"])
            self.assertEqual(port.calls, [])

    def test_changed_repository_configuration_refuses_without_reserving_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, port, _storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            auth = self._authenticate(client)
            settings = client.app.state.settings
            project = settings.projects[0]
            changed_frontend = RepositoryConfiguration(
                id="frontend", owner="attacker", repo="frontend", branch="release/frontend",
            )
            changed_project = project.model_copy(update={
                "repositories": (changed_frontend, project.repositories[1]),
            })
            client.app.state.settings = settings.model_copy(update={"projects": (changed_project,)})
            finding = findings[0]
            path = f"/api/v1/runs/{RUN_ID}/findings/{finding.id}/decision"
            headers = {**auth, "Idempotency-Key": "changed-origin-config-0001"}
            body = {"decision": "approve", "reason": "Only the stored origin is authorized."}
            refused = client.post(path, headers=headers, json=body)
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["error"]["code"], "revision_independiente_requerida")
            self.assertEqual(port.calls, [])

            client.app.state.settings = settings
            retry = client.post(path, headers=headers, json=body)
            self.assertEqual(retry.status_code, 409, retry.text)
            self.assertEqual(retry.json()["error"]["code"], "revision_independiente_requerida")
            self.assertEqual(len(port.calls), 0)

    def test_removed_configuration_keeps_stored_origin_visible_for_nonwriting_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, port, _storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            auth = self._authenticate(client)
            settings = client.app.state.settings
            project = settings.projects[0].model_copy(update={"repositories": ()})
            client.app.state.settings = settings.model_copy(update={
                "projects": (project,), "github_repository": None,
            })
            finding = findings[0]
            path = f"/api/v1/runs/{RUN_ID}/findings/{finding.id}/decision"
            headers = {**auth, "Idempotency-Key": "removed-config-classify-0001"}
            body = {"decision": "reject", "reason": "Keep the historical origin in the decision record."}
            response = client.post(path, headers=headers, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["repository_id"], "frontend")
            self.assertEqual(response.json()["repository_owner"], "acme")
            self.assertEqual(response.json()["repository_name"], "frontend")
            self.assertEqual(response.json()["source_commit_sha"], COMMIT_SHA)
            audit = client.get(
                f"/api/v1/projects/{PROJECT_ID}/audit/{response.json()['audit_event_id']}"
            )
            self.assertEqual(audit.status_code, 200, audit.text)
            self.assertEqual(audit.json()["details"]["finding_id"], finding.id)
            self.assertEqual(audit.json()["details"]["repository_id"], "frontend")
            self.assertEqual(audit.json()["details"]["source_commit_sha"], COMMIT_SHA)
            self.assertEqual(port.calls, [])

    def test_unknown_historical_origin_allows_only_nonwriting_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, port, storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            report = storage.get_report(REPORT_ID)
            unknown = findings[0].model_copy(update={
                "repository_identity": None, "provenance": None,
            })
            report = self._replace_report_candidate(report, unknown).model_copy(update={
                "repository_results": [],
            })
            storage.save_report(report, report_id=REPORT_ID, run_id=RUN_ID)
            storage.save_findings(RUN_ID, list(report.candidate_findings))
            snapshot = storage.get_snapshot(SNAPSHOT_ID)
            storage.save_snapshot(snapshot.model_copy(update={"code_diffs": [], "repository_results": []}))

            auth = self._authenticate(client)
            path = f"/api/v1/runs/{RUN_ID}/findings/{unknown.id}/decision"
            headers = {**auth, "Idempotency-Key": "unknown-historical-origin-0001"}
            approval = client.post(path, headers=headers, json={
                "decision": "approve", "reason": "Approval needs a known repository origin.",
            })
            self.assertEqual(approval.status_code, 409, approval.text)
            self.assertEqual(approval.json()["error"]["code"], "revision_independiente_requerida")
            self.assertEqual(port.calls, [])

            classification = client.post(path, headers=headers, json={
                "decision": "request_changes", "reason": "Classify without a source write.",
            })
            self.assertEqual(classification.status_code, 200, classification.text)
            self.assertEqual(classification.json()["decision_status"], "changes_requested")
            self.assertNotIn("repository_name", classification.json())
            self.assertEqual(port.calls, [])

    def test_ambiguous_and_non_target_provenance_refuse_before_any_action(self):
        for case in ("ambiguous", "non_target_provenance", "conflicting_source_branches"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                client, port, storage, findings = self._client(Path(tmp))
                self.addCleanup(client.close)
                report = storage.get_report(REPORT_ID)
                candidate = findings[0]
                if case == "ambiguous":
                    candidate = candidate.model_copy(update={
                        "repository_identity": None, "provenance": None,
                    })
                    report = self._replace_report_candidate(report, candidate).model_copy(update={
                        "repository_results": [],
                    })
                elif case == "non_target_provenance":
                    provenance = candidate.provenance.model_copy(update={"certifies_target": False})
                    candidate = candidate.model_copy(update={"provenance": provenance})
                    report = self._replace_report_candidate(report, candidate)
                else:
                    snapshot = storage.get_snapshot(SNAPSHOT_ID)
                    frontend = next(
                        item for item in snapshot.repository_results
                        if item.repository_state.identity.id == "frontend"
                    )
                    conflicting = frontend.model_copy(update={
                        "repository_state": frontend.repository_state.model_copy(update={
                            "branch": "release/conflicting",
                        }),
                    })
                    snapshot = snapshot.model_copy(update={
                        "code_diffs": [frontend.code_diffs[0]],
                        "repository_results": [conflicting, frontend],
                    })
                    storage.save_snapshot(snapshot)
                    report = report.model_copy(update={"repository_results": []})
                storage.save_report(report, report_id=REPORT_ID, run_id=RUN_ID)
                storage.save_findings(RUN_ID, list(report.candidate_findings))

                auth = self._authenticate(client)
                path = f"/api/v1/runs/{RUN_ID}/findings/{findings[0].id}/decision"
                response = client.post(path, headers={
                    **auth, "Idempotency-Key": f"{case}-refusal-0001",
                }, json={"decision": "approve", "reason": "Must stay bound to stored origin."})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
                self.assertEqual(port.calls, [])

    def test_missing_full_source_hash_and_missing_excerpt_refuse_without_branch_calls(self):
        for case in ("missing_hash", "missing_excerpt"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                client, port, storage, findings = self._client(Path(tmp))
                self.addCleanup(client.close)
                if case == "missing_hash":
                    snapshot = storage.get_snapshot(SNAPSHOT_ID)
                    partial = snapshot.code_diffs[0].model_copy(update={
                        "evidence_scope": "snippet", "is_complete": False,
                        "source_sha256": None,
                    })
                    repository_results = []
                    for result in snapshot.repository_results:
                        snippets = [partial] if result.repository_state.identity.id == "frontend" else result.code_diffs
                        repository_results.append(result.model_copy(update={"code_diffs": snippets}))
                    storage.save_snapshot(snapshot.model_copy(update={
                        "code_diffs": [partial, *snapshot.code_diffs[1:]],
                        "repository_results": repository_results,
                    }))
                else:
                    report = storage.get_report(REPORT_ID)
                    candidate = findings[0].model_copy(update={"evidence": ""})
                    report = self._replace_report_candidate(report, candidate)
                    storage.save_report(report, report_id=REPORT_ID, run_id=RUN_ID)
                    storage.save_findings(RUN_ID, list(report.candidate_findings))

                auth = self._authenticate(client)
                path = f"/api/v1/runs/{RUN_ID}/findings/{findings[0].id}/decision"
                response = client.post(path, headers={
                    **auth, "Idempotency-Key": f"{case}-refusal-0001",
                }, json={"decision": "approve", "reason": "An exact full source and excerpt are required."})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
                self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "pending")
                self.assertEqual(port.calls, [])

    def test_unsupported_syntax_refuses_before_reserving_or_calling_the_action_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, port, storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            new_path = "src/shared.vue"
            report = storage.get_report(REPORT_ID)
            candidate = findings[0].model_copy(update={"file_path": new_path})
            report = self._replace_report_candidate(report, candidate)
            storage.save_report(report, report_id=REPORT_ID, run_id=RUN_ID)
            storage.save_findings(RUN_ID, list(report.candidate_findings))
            snapshot = storage.get_snapshot(SNAPSHOT_ID)
            snippets = [
                item.model_copy(update={"file_path": new_path})
                if item.repository_identity.id == "frontend" else item
                for item in snapshot.code_diffs
            ]
            repository_results = []
            for result in snapshot.repository_results:
                code_diffs = [
                    item.model_copy(update={"file_path": new_path})
                    if item.repository_identity.id == "frontend" else item
                    for item in result.code_diffs
                ]
                repository_results.append(result.model_copy(update={"code_diffs": code_diffs}))
            storage.save_snapshot(snapshot.model_copy(update={
                "code_diffs": snippets, "repository_results": repository_results,
            }))

            auth = self._authenticate(client)
            path = f"/api/v1/runs/{RUN_ID}/findings/{findings[0].id}/decision"
            response = client.post(path, headers={
                **auth, "Idempotency-Key": "unsupported-syntax-refusal-0001",
            }, json={"decision": "approve", "reason": "Unsupported syntax must fail closed."})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
            self.assertIn("parche exacto", response.json()["error"]["message"])
            self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "pending")
            self.assertEqual(port.calls, [])

    def test_successful_approval_receipt_replay_is_blocked_even_with_bound_origin(self):
        from agente_gestion.application.finding_decisions import FindingDecisionService
        from agente_gestion.api.finding_actions import _configured_origin
        from agente_gestion.domain.security_rbac import SecurityAuditEntry, hash_token_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            client, port, storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            finding = findings[0]
            auth = self._authenticate(client)
            session_cookie = client.cookies.get(client.app.state.settings.session_cookie_name)
            session = client.app.state.sessions.get(session_cookie)
            key = "legacy-approval-replay-0001"
            body = {
                "decision": "approve", "target_branch": "quality/legacy-branch",
                "reason": "Legacy approval request.",
            }
            request = {
                "decision": body["decision"], "suggested_code_override": None,
                "target_branch": body["target_branch"], "reason": body["reason"],
                "actor_id": session.actor,
            }
            settings = client.app.state.settings
            origin = client.app.state.repository.finding_action_context(RUN_ID, finding.id)[-1]
            origin = _configured_origin(origin, settings, settings.projects[0])
            request["origin"] = origin.model_dump(mode="json")
            request_json = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            request_id, event_id = FindingDecisionService._ids(
                PROJECT_ID, RUN_ID, finding.id, key,
            )
            request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
            recorded_at = datetime.now(timezone.utc).isoformat()
            reservation, _ = storage.reserve_finding_decision(
                project_id=PROJECT_ID, run_id=RUN_ID, finding_id=finding.id,
                idempotency_key=key, request_id=request_id, event_id=event_id,
                request_sha256=request_sha256, request_json=request_json,
                decision="approve", actor_id=session.actor, recorded_at=recorded_at,
            )
            self.assertEqual(reservation, "reserved")
            legacy_result = {
                "reused": False, "audit_event_id": f"audit-{event_id}",
                "decision_status": "approved", "branch_name": body["target_branch"],
                "branch_url": "sandbox://branches/quality/legacy-branch",
                "commit_sha_applied": "d" * 64, "suggested_code": finding.suggested_code,
                "improvement_summary": finding.improvement_summary, "execution_mode": "sandbox",
            }
            storage.complete_finding_decision(
                request_id=request_id,
                result_json=json.dumps(legacy_result, sort_keys=True, separators=(",", ":")),
                decision_status="approved", suggested_code=finding.suggested_code,
                improvement_summary=finding.improvement_summary, branch_name=body["target_branch"],
                branch_url="sandbox://branches/quality/legacy-branch", commit_sha_applied="d" * 64,
                audit_entry=SecurityAuditEntry(
                    event_id=f"audit-{event_id}", project_id=PROJECT_ID,
                    actor_id=session.actor, role="project_manager_session",
                    permission_checked="web:finding_decision", resource_id=finding.id,
                    decision="granted", reason="Legacy approval recorded in sandbox.",
                    token_fingerprint=hash_token_fingerprint("synthetic-legacy-session"),
                ),
                recorded_at=recorded_at,
            )

            response = client.post(
                f"/api/v1/runs/{RUN_ID}/findings/{finding.id}/decision",
                headers={**auth, "Idempotency-Key": key}, json=body,
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
            self.assertIn("parche exacto", response.json()["error"]["message"])
            self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "approved")
            self.assertEqual(port.calls, [])

    def test_direct_service_cannot_relabel_another_repository_finding(self):
        from agente_gestion.application.finding_decisions import FindingDecisionError, FindingDecisionService
        from agente_gestion.domain.code_action import FindingActionOrigin

        with tempfile.TemporaryDirectory() as tmp:
            client, port, storage, findings = self._client(Path(tmp))
            self.addCleanup(client.close)
            repository = client.app.state.repository
            front, front_excerpt, front_source, front_repo, front_origin = repository.finding_action_context(
                RUN_ID, findings[0].id,
            )
            backend, backend_excerpt, backend_source, backend_repo, backend_origin = repository.finding_action_context(
                RUN_ID, findings[1].id,
            )
            forged_candidate = backend.model_copy(update={"id": findings[0].id})
            forged_origin = FindingActionOrigin.model_validate({
                **backend_origin.model_dump(mode="python"),
                "finding_id": findings[0].id,
                "configured_for_code_action": True,
            })
            service = FindingDecisionService(storage, port)
            with self.assertRaises(FindingDecisionError) as caught:
                service.decide(
                    project_id=PROJECT_ID, run_id=RUN_ID, finding_id=findings[0].id,
                    candidate=forged_candidate, decision="reject",
                    idempotency_key="crafted-cross-repository-0001", actor_id="test-operator",
                    session_cookie="synthetic-cookie", original_excerpt=backend_excerpt,
                    source=backend_source, repository=backend_repo,
                    base_branch=backend_origin.base_branch or "", origin=forged_origin,
                    reason="A crafted internal object must not bypass persisted binding.",
                )
            self.assertEqual((caught.exception.status_code, caught.exception.code), (409, "fuente_no_verificable"))
            self.assertEqual(port.calls, [])

            tampered_sha = FindingActionOrigin.model_validate({
                **front_origin.model_dump(mode="python"),
                "base_branch": "release/frontend",
                "configured_for_code_action": True,
                "source_sha256": "f" * 64,
            })
            with self.assertRaises(FindingDecisionError) as bad_sha:
                service.decide(
                    project_id=PROJECT_ID, run_id=RUN_ID, finding_id=findings[0].id,
                    candidate=front, decision="reject", idempotency_key="tampered-source-sha-0001",
                    actor_id="test-operator", session_cookie="synthetic-cookie",
                    original_excerpt=front_excerpt, source=front_source, repository=front_repo,
                    base_branch="release/frontend", origin=tampered_sha,
                    reason="A crafted source hash must not be accepted.",
                )
            self.assertEqual(bad_sha.exception.code, "fuente_no_verificable")
            with self.assertRaises(FindingDecisionError) as bad_excerpt:
                service.decide(
                    project_id=PROJECT_ID, run_id=RUN_ID, finding_id=findings[0].id,
                    candidate=front, decision="reject", idempotency_key="tampered-excerpt-0001",
                    actor_id="test-operator", session_cookie="synthetic-cookie",
                    original_excerpt="    return attacker_controlled", source=front_source,
                    repository=front_repo, base_branch="release/frontend",
                    origin=tampered_sha.model_copy(update={"source_sha256": SOURCE_HASH}),
                    reason="A crafted excerpt must not be accepted.",
                )
            self.assertEqual(bad_excerpt.exception.code, "fuente_no_verificable")
            self.assertEqual(port.calls, [])

            auth = self._authenticate(client)
            path = f"/api/v1/runs/{RUN_ID}/findings/{findings[0].id}/decision"
            accepted = client.post(path, headers={
                **auth, "Idempotency-Key": "tampered-source-sha-0001",
            }, json={"decision": "request_changes", "reason": "The stored frontend origin is valid."})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.json()["decision_status"], "changes_requested")
            self.assertEqual(len(port.calls), 0)

    def test_fake_github_refuses_cross_repository_and_changed_head_before_writes(self):
        from agente_gestion.adapters.github_code_action_adapter import CodeActionError, GitHubCodeActionAdapter
        from agente_gestion.domain.code_action import CodeSourceSnapshot, FindingActionOrigin

        identity = RepositoryIdentity(id="frontend", owner="acme", repo="frontend")
        excerpt = "    return value"
        source = CodeSourceSnapshot(
            content=SOURCE, is_complete=True, repository="acme/frontend", commit_sha=COMMIT_SHA,
            file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
        )
        origin = FindingActionOrigin(
            project_id=PROJECT_ID, run_id=RUN_ID, finding_id="frontend:f-fake-1",
            repository_identity=identity, repository="acme/frontend", commit_sha=COMMIT_SHA,
            file_path=FILE_PATH, source_sha256=SOURCE_HASH,
            excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
            base_branch="release/frontend", configured_for_code_action=True, multi_repository=True,
        )
        fake = _FakeGitHubHttp([
            (200, {"permissions": {"push": True}}),
            (200, {"object": {"sha": "f" * 40}}),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            branches_dir = Path(tmp) / "branches"
            adapter = GitHubCodeActionAdapter(
                str(branches_dir), token="synthetic-offline-token", repository="acme/frontend",
                base_branch="release/frontend", external_writes_enabled=True, project_mode="real",
                http_client=fake, source_lookup=lambda _repo, _path: source, origin=origin,
            )
            with self.assertRaises(CodeActionError) as cross_repo:
                adapter.create_verification_branch(
                    repo="acme/backend", base_branch="release/backend", target_branch="quality/scoped-test",
                    file_path=FILE_PATH, original_excerpt=excerpt, suggested_code="    return value.strip()",
                    finding_id="frontend:f-fake-1", actor="synthetic-operator",
                )
            self.assertEqual(cross_repo.exception.code, "fuente_no_verificable")
            self.assertEqual(fake.calls, [])

            with self.assertRaises(CodeActionError) as stale_head:
                adapter.create_verification_branch(
                    repo="acme/frontend", base_branch="release/frontend", target_branch="quality/scoped-test",
                    file_path=FILE_PATH, original_excerpt=excerpt, suggested_code="    return value.strip()",
                    finding_id="frontend:f-fake-1", actor="synthetic-operator",
                )
            self.assertEqual((stale_head.exception.status_code, stale_head.exception.code), (409, "fuente_obsoleta"))
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET"])
            self.assertTrue(all(
                call[1].startswith("https://api.github.com/repos/acme/frontend")
                and "/repos/acme/backend" not in call[1]
                for call in fake.calls
            ))
            self.assertFalse(any(call[0] in {"POST", "PUT", "PATCH", "DELETE"} for call in fake.calls))
            self.assertFalse(branches_dir.exists())

    def test_fake_github_success_and_sandbox_paths_stay_repository_scoped(self):
        import base64
        from agente_gestion.adapters.github_code_action_adapter import GitHubCodeActionAdapter
        from agente_gestion.domain.code_action import CodeSourceSnapshot, FindingActionOrigin

        excerpt = "    return value"
        source_by_repo = {
            "acme/frontend": CodeSourceSnapshot(
                content=SOURCE, is_complete=True, repository="acme/frontend", commit_sha=COMMIT_SHA,
                file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
            ),
            "acme/backend": CodeSourceSnapshot(
                content=SOURCE, is_complete=True, repository="acme/backend", commit_sha=COMMIT_SHA,
                file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
            ),
        }

        def origin(repository_id, repo_name, branch):
            identity = RepositoryIdentity(id=repository_id, owner="acme", repo=repo_name)
            return FindingActionOrigin(
                project_id=PROJECT_ID, run_id=RUN_ID, finding_id=f"{repository_id}:f-fake-1",
                repository_identity=identity, repository=f"acme/{repo_name}", commit_sha=COMMIT_SHA,
                file_path=FILE_PATH, source_sha256=SOURCE_HASH,
                excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                base_branch=branch, configured_for_code_action=True, multi_repository=True,
            )

        fake = _FakeGitHubHttp([
            (200, {"permissions": {"push": True}}),
            (200, {"object": {"sha": COMMIT_SHA}}),
            (200, {
                "encoding": "base64", "content": base64.b64encode(SOURCE.encode()).decode(),
                "sha": "a" * 40,
            }),
            (201, {"ref": "refs/heads/quality/scoped-frontend"}),
            (201, {"commit": {"sha": "d" * 40}}),
        ])
        live_origin = origin("frontend", "frontend", "release/frontend")
        adapter = GitHubCodeActionAdapter(
            token="synthetic-offline-token", repository="acme/frontend", base_branch="release/frontend",
            external_writes_enabled=True, project_mode="real", http_client=fake,
            source_lookup=lambda repo, _path: source_by_repo.get(repo), origin=live_origin,
        )
        result = adapter.create_verification_branch(
            repo="acme/frontend", base_branch="release/frontend", target_branch="quality/scoped-frontend",
            file_path=FILE_PATH, original_excerpt=excerpt, suggested_code="    return value.strip()",
            finding_id="frontend:f-fake-1", actor="synthetic-operator",
        )
        self.assertEqual(result.execution_mode, "live")
        self.assertEqual(result.commit_sha_applied, "d" * 40)
        self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "GET", "POST", "PUT"])
        self.assertTrue(all(
            call[1].startswith("https://api.github.com/repos/acme/frontend")
            and "/repos/acme/backend" not in call[1]
            for call in fake.calls
        ))
        self.assertIn("ref=0123456789abcdef0123456789abcdef01234567", fake.calls[2][1])
        self.assertEqual(fake.calls[3][3]["sha"], COMMIT_SHA)
        self.assertEqual(fake.calls[4][3]["branch"], "quality/scoped-frontend")
        patched = base64.b64decode(fake.calls[4][3]["content"]).decode()
        self.assertEqual(patched, "def parse(value):\n    return value.strip()\n")

        with tempfile.TemporaryDirectory() as tmp:
            branches = Path(tmp) / "branches"
            for repository_id, repo_name, branch in (
                ("frontend", "frontend", "release/frontend"),
                ("backend", "backend", "release/backend"),
            ):
                bound = origin(repository_id, repo_name, branch)
                sandbox = GitHubCodeActionAdapter(
                    str(branches), repository=f"acme/{repo_name}", base_branch=branch,
                    external_writes_enabled=False, project_mode="fixtures",
                    source_lookup=lambda repo, _path: source_by_repo.get(repo), origin=bound,
                )
                sandbox.create_verification_branch(
                    repo=f"acme/{repo_name}", base_branch=branch, target_branch="quality/shared",
                    file_path=FILE_PATH, original_excerpt=excerpt, suggested_code="    return value.strip()",
                    finding_id=f"{repository_id}:f-fake-1", actor="synthetic-operator",
                )
            for repository_id, repo_name in (("frontend", "frontend"), ("backend", "backend")):
                scope = hashlib.sha256(f"acme/{repo_name}".encode()).hexdigest()[:16]
                sandbox_file = branches / "repositories" / scope / "quality" / "shared" / FILE_PATH
                self.assertEqual(
                    sandbox_file.read_text(encoding="utf-8"),
                    "def parse(value):\n    return value.strip()\n",
                )
            self.assertNotEqual(
                hashlib.sha256(b"acme/frontend").hexdigest()[:16],
                hashlib.sha256(b"acme/backend").hexdigest()[:16],
            )


if __name__ == "__main__":
    unittest.main()
