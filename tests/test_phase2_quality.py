"""Behavioral contracts for Phase 2 quality findings and governed decisions."""

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import (
    CandidateFinding,
    SuggestedCodeValidationStatus,
    VerificationVerdict,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from tests.web_fixtures import (
    COMMIT_SHA,
    FINDING_ID,
    OPERATOR_TOKEN,
    PROJECT_ID,
    REPORT_ID,
    RUN_ID,
    build_web_api_fixture,
)


class Phase2QualityTests(unittest.TestCase):
    def test_finding_contract_carries_a_complete_example_and_pending_decision_state(self):
        finding = CandidateFinding(
            id="finding-code-example", commit_sha="a" * 40, file_path="src/example.py", line=1,
            suggested_code="return normalized(value)",
            improvement_summary="Normaliza el valor antes de procesarlo.",
        )
        self.assertEqual(finding.suggested_code, "return normalized(value)")
        self.assertEqual(finding.improvement_summary, "Normaliza el valor antes de procesarlo.")
        self.assertEqual(finding.decision_status, "pending")
        self.assertIsNone(finding.branch_name)
        self.assertIsNone(finding.branch_url)
        self.assertIsNone(finding.commit_sha_applied)

    def test_quality_agent_returns_deterministic_code_example_and_summary(self):
        snippet = CodeDiffSnippet(
            repository="acme/project", commit_sha="a" * 40, file_path="src/example.py",
            lines=[1], content="return value / total",
        )
        findings = CodeQualityAgent(FakeProvider()).run(
            "project", MonitoringDraft(summary="Revisión semanal"), [snippet],
            rules=[QualityRule(
                id="Q3", description="Evitar división por cero", metric_key="open_critical_findings",
                operator="==", threshold=0,
            )],
            evidence_ids=["a" * 40],
        )
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].suggested_code)
        self.assertTrue(findings[0].improvement_summary)

    def test_verifier_does_not_confirm_invalid_full_file_replacement(self):
        source = "def parse(value):\n    return value\n"
        snippet = CodeDiffSnippet(
            repository="acme/project", commit_sha="a" * 40, file_path="src/example.py",
            lines=[1, 2], content=source, evidence_scope="full_file", is_complete=True,
            total_lines=2, source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        )
        finding = CandidateFinding(
            id="syntax-finding", claim="La función debe conservar una expresión válida.",
            commit_sha="a" * 40, file_path="src/example.py", line=2,
            evidence="    return value", suggested_code="    return (",
            improvement_summary="Mantener sintaxis válida.", evidence_ids=["a" * 40],
        )
        result = VerifierAgent(FakeProvider()).run(
            "project", [finding], [snippet], evidence_ids=["a" * 40],
        )
        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0].verdict, VerificationVerdict.confirmed)

    def test_verifier_never_confirms_suggestion_without_complete_source(self):
        finding = CandidateFinding(
            id="snippet-only", claim="La expresión debe controlar el divisor cero.",
            commit_sha="a" * 40, file_path="src/example.py", line=2,
            evidence="    return value", suggested_code="    return value or 0",
            improvement_summary="Evita un divisor vacío.", evidence_ids=["a" * 40],
        )
        partial = CodeDiffSnippet(
            repository="acme/project", commit_sha="a" * 40, file_path="src/example.py",
            lines=[2], content="    return value\n", evidence_scope="diff", is_complete=False,
        )
        result = VerifierAgent(FakeProvider()).run(
            "project", [finding], [partial], evidence_ids=["a" * 40],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].verdict, VerificationVerdict.confirmed)
        self.assertEqual(result[0].suggested_code_validation_status, SuggestedCodeValidationStatus.not_validated)

    def test_fresh_finding_storage_preserves_unified_suggestions_and_branch_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = SQLiteStorageAdapter(str(Path(tmp) / "fresh.sqlite"))
            finding = CandidateFinding(
                id="finding-fresh", commit_sha="a" * 40, file_path="src/example.py", line=1,
                suggested_code="return safe_value", improvement_summary="Resumen seguro",
            )
            storage.save_findings("run-fresh", [finding])
            storage.update_finding_metadata(
                "run-fresh", "finding-fresh", decision_status="approved",
                branch_name="quality/finding-fresh", branch_url="sandbox://branches/quality/finding-fresh",
                commit_sha_applied="b" * 64,
            )
            persisted = storage.get_findings("run-fresh")[0]
            self.assertEqual(persisted["suggested_code"], "return safe_value")
            self.assertEqual(persisted["improvement_summary"], "Resumen seguro")
            self.assertEqual(persisted["decision_status"], "approved")
            self.assertEqual(persisted["branch_name"], "quality/finding-fresh")
            self.assertEqual(persisted["commit_sha_applied"], "b" * 64)

    def test_query_projects_persisted_fields_without_report_json_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_web_api_fixture(Path(tmp))
            self.addCleanup(fixture.client.close)
            storage = SQLiteStorageAdapter(str(fixture.database_path))
            report = storage.get_report(REPORT_ID)
            finding = report.candidate_findings[0].model_copy(update={
                "suggested_code": "return normalized(value)",
                "improvement_summary": "Normaliza el valor antes de procesarlo.",
                "decision_status": "changes_requested",
                "branch_name": "quality/review-existing",
                "branch_url": "sandbox://branches/quality/review-existing",
                "commit_sha_applied": "b" * 64,
            })
            changed_report = report.model_copy(update={
                "candidate_findings": [finding], "insufficient_findings": [finding],
            })
            storage.save_report(changed_report, report_id=REPORT_ID, run_id=RUN_ID)
            storage.update_finding_metadata(
                RUN_ID, FINDING_ID, suggested_code="", improvement_summary="",
                decision_status="pending", branch_name="", branch_url="", commit_sha_applied="",
            )
            settings = fixture.client.app.state.settings
            stored_view = SQLiteQueryRepository(settings).finding_detail(RUN_ID, FINDING_ID)
            self.assertEqual(stored_view.suggested_code, "")
            self.assertEqual(stored_view.improvement_summary, "")
            self.assertEqual(stored_view.decision_status, "pending")
            self.assertIsNone(stored_view.branch_name)
            self.assertIsNone(stored_view.branch_url)
            self.assertIsNone(stored_view.commit_sha_applied)
            action_candidate, _excerpt, _source, _repository, _origin = SQLiteQueryRepository(
                settings,
            ).finding_action_context(RUN_ID, FINDING_ID)
            self.assertEqual(action_candidate.suggested_code, "")
            self.assertEqual(action_candidate.improvement_summary, "")

            storage.update_finding_metadata(
                RUN_ID, FINDING_ID, suggested_code="return safe(value)",
                improvement_summary="Valor normalizado de forma segura.",
                decision_status="approved", branch_name="quality/new-branch",
                branch_url="sandbox://quality/new-branch", commit_sha_applied="c" * 64,
            )
            normalized_view = SQLiteQueryRepository(settings).finding_detail(RUN_ID, FINDING_ID)
            self.assertEqual(normalized_view.suggested_code, "return safe(value)")
            self.assertEqual(normalized_view.decision_status, "approved")
            self.assertEqual(normalized_view.branch_name, "quality/new-branch")

    def _action_fixture(self, root: Path):
        from agente_gestion.api.finding_actions import router as finding_actions_router
        from agente_gestion.domain.code import CodeDiffSnippet

        fixture = build_web_api_fixture(root, governance_enabled=True)
        self.addCleanup(fixture.client.close)
        app = fixture.client.app
        settings = app.state.settings.model_copy(update={
            "github_repository": "acme/h5-test",
            "github_base_branch": "main",
            "external_writes_enabled": False,
        })
        app.state.settings = settings
        app.state.repository = SQLiteQueryRepository(settings)
        app.include_router(finding_actions_router)
        storage = SQLiteStorageAdapter(str(fixture.database_path))
        snapshot = storage.get_snapshot("snapshot-current")
        source = "def parse_input(value):\n    return value\n"
        full_file = CodeDiffSnippet(
            repository="acme/h5-test", commit_sha=COMMIT_SHA, file_path="src/api/input.py",
            lines=[1, 2], content=source, evidence_scope="full_file", is_complete=True,
            total_lines=2, source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        )
        storage.save_snapshot(snapshot.model_copy(update={"code_diffs": [full_file]}))
        report = storage.get_report(REPORT_ID)
        finding = report.candidate_findings[0].model_copy(update={
            "commit_sha": COMMIT_SHA, "file_path": "src/api/input.py", "line": 2,
            "evidence": "    return value", "suggested_code": "    return value.strip()",
            "improvement_summary": "Normaliza el valor de entrada.",
        })
        storage.save_findings(RUN_ID, [finding])
        storage.update_finding_metadata(
            RUN_ID, FINDING_ID, suggested_code=finding.suggested_code,
            improvement_summary=finding.improvement_summary,
        )
        storage.save_report(report.model_copy(update={
            "candidate_findings": [finding], "insufficient_findings": [finding],
        }), report_id=REPORT_ID, run_id=RUN_ID)
        stored_candidate, _excerpt, _source, _repository, _origin = SQLiteQueryRepository(
            fixture.client.app.state.settings,
        ).finding_action_context(RUN_ID, FINDING_ID)
        self.assertEqual(stored_candidate.suggested_code, "    return value.strip()")
        self.assertEqual(stored_candidate.improvement_summary, "Normaliza el valor de entrada.")
        return fixture, storage

    def _configured_action_origin(self, fixture, origin):
        from agente_gestion.api.finding_actions import _configured_origin

        settings = fixture.client.app.state.settings
        project = next(item for item in settings.projects if item.id == PROJECT_ID)
        return _configured_origin(origin, settings, project)

    def test_approve_without_complete_source_is_rejected_before_any_durable_or_branch_write(self):
        from agente_gestion.api.app import create_app
        from agente_gestion.api.finding_actions import router as finding_actions_router
        from agente_gestion.domain.code import CodeDiffSnippet
        from fastapi.testclient import TestClient

        for source_mode in ("missing", "partial"):
            with self.subTest(source_mode=source_mode), tempfile.TemporaryDirectory() as tmp:
                fixture, storage = self._action_fixture(Path(tmp))
                snapshot = storage.get_snapshot("snapshot-current")
                if source_mode == "missing":
                    snippets = []
                else:
                    snippets = [CodeDiffSnippet(
                        repository="acme/h5-test", commit_sha=COMMIT_SHA,
                        file_path="src/api/input.py", lines=[2],
                        content="    return value\n", evidence_scope="diff", is_complete=False,
                    )]
                storage.save_snapshot(snapshot.model_copy(update={"code_diffs": snippets}))
                report_before = storage.get_report(REPORT_ID).model_dump_json()
                branches_dir = Path(tmp) / "branches"
                settings = fixture.client.app.state.settings.model_copy(update={
                    "branches_dir": str(branches_dir),
                })
                app = create_app(settings)
                app.include_router(finding_actions_router)
                client = TestClient(app)
                login = client.post(
                    "/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                    headers={"Origin": "http://testserver"},
                )
                self.assertEqual(login.status_code, 200, login.text)
                response = client.post(
                    f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
                    headers={
                        "Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"],
                        "Idempotency-Key": f"incomplete-{source_mode}-0001",
                    },
                    json={
                        "decision": "approve", "suggested_code_override": "    return value.strip()",
                        "target_branch": "quality/incomplete-source", "reason": "Validación de fuente completa.",
                    },
                )
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["error"]["code"], "fuente_no_verificable")
                self.assertEqual(storage.get_report(REPORT_ID).model_dump_json(), report_before)
                self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "pending")
                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    decisions = connection.execute("SELECT count(*) FROM finding_decisions").fetchone()[0]
                    requests = connection.execute("SELECT count(*) FROM finding_decision_requests").fetchone()[0]
                    audits = connection.execute(
                        "SELECT count(*) FROM security_audit_log WHERE permission_checked='web:finding_decision'",
                    ).fetchone()[0]
                self.assertEqual((decisions, requests, audits), (0, 0, 0))
                self.assertFalse(branches_dir.exists())
                client.close()

    def test_protected_decision_is_durable_idempotent_and_leaves_report_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, storage = self._action_fixture(Path(tmp))
            branches_dir = Path(tmp) / "branches"
            settings = fixture.client.app.state.settings.model_copy(update={
                "branches_dir": str(branches_dir),
            })
            # The application factory is explicit; no global settings or repository are changed.
            from agente_gestion.api.app import create_app
            app = create_app(settings)
            app.include_router(__import__(
                "agente_gestion.api.finding_actions", fromlist=["router"],
            ).router)
            from fastapi.testclient import TestClient
            client = TestClient(app)
            app.state.code_action_port = None
            before_report = storage.get_report(REPORT_ID).model_dump_json()

            unauthenticated = client.post(
                f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
                json={"decision": "request_changes"},
                headers={"Origin": "http://testserver", "Idempotency-Key": "phase2-key-0001"},
            )
            self.assertEqual(unauthenticated.status_code, 401)
            self.assertEqual(unauthenticated.json()["error"]["code"], "sesion_requerida")
            login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                headers={"Origin": "http://testserver"})
            self.assertEqual(login.status_code, 200, login.text)
            session_view = client.get("/api/v1/auth/session")
            self.assertIn("quality:decide", session_view.json()["capabilities"])
            csrf = login.json()["csrf_token"]
            headers = {
                "Origin": "http://testserver", "X-CSRF-Token": csrf,
                "Idempotency-Key": "phase2-key-0001",
            }
            path = f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision"
            body = {
                "decision": "approve", "suggested_code_override": "    return value.strip()",
                "target_branch": "quality/finding-candidate-001-phase2",
                "reason": "Aprobación explícita del gestor.",
            }
            missing_csrf = client.post(path, headers={
                "Origin": "http://testserver", "Idempotency-Key": "phase2-key-0002",
            }, json=body)
            self.assertEqual(missing_csrf.status_code, 403)
            self.assertEqual(missing_csrf.json()["error"]["code"], "csrf_invalido")
            wrong_origin = client.post(path, headers={
                **headers, "Origin": "https://attacker.invalid",
                "Idempotency-Key": "phase2-key-0004",
            }, json=body)
            self.assertEqual(wrong_origin.status_code, 403)
            self.assertEqual(wrong_origin.json()["error"]["code"], "origen_no_permitido")
            invalid_branch = client.post(path, headers=headers, json={
                **body, "target_branch": "../unsafe",
            })
            self.assertEqual(invalid_branch.status_code, 422)
            invalid_code = client.post(path, headers=headers, json={
                **body, "suggested_code_override": "    return (",
            })
            self.assertEqual(invalid_code.status_code, 422, invalid_code.text)
            self.assertEqual(invalid_code.json()["error"]["code"], "codigo_sugerido_invalido")
            self.assertFalse(branches_dir.exists())
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                counts = tuple(connection.execute(
                    f"SELECT count(*) FROM {table}",
                ).fetchone()[0] for table in (
                    "finding_decisions", "finding_decision_requests", "security_audit_log",
                ))
            self.assertEqual(counts, (0, 0, 0))
            created = client.post(path, headers=headers, json=body)
            self.assertEqual(created.status_code, 200, created.text)
            result = created.json()
            self.assertEqual(result["decision_status"], "approved")
            self.assertEqual(result["execution_mode"], "sandbox")
            self.assertEqual(result["branch_name"], "quality/finding-candidate-001-phase2")
            self.assertEqual(result["suggested_code"], body["suggested_code_override"])
            self.assertTrue(result["commit_sha_applied"])
            audit_event = client.get(
                f"/api/v1/projects/{PROJECT_ID}/audit/{result['audit_event_id']}",
            )
            self.assertEqual(audit_event.status_code, 200, audit_event.text)
            self.assertEqual(audit_event.json()["event_id"], result["audit_event_id"])
            patched_file = (Path(tmp) / "branches" / "quality" / "finding-candidate-001-phase2"
                            / "src" / "api" / "input.py")
            self.assertEqual(patched_file.read_text(encoding="utf-8"),
                             "def parse_input(value):\n    return value.strip()\n")
            replay = client.post(path, headers=headers, json=body)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()["reused"])
            conflict = client.post(path, headers=headers, json={**body, "reason": "otro motivo"})
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["error"]["code"], "idempotencia_conflictiva")
            other_key = client.post(path, headers={**headers, "Idempotency-Key": "phase2-key-0003"}, json=body)
            self.assertEqual(other_key.status_code, 409)
            self.assertEqual(other_key.json()["error"]["code"], "aprobacion_ya_registrada")
            self.assertEqual(storage.get_report(REPORT_ID).model_dump_json(), before_report)
            finding_row = storage.get_findings(RUN_ID)[0]
            self.assertEqual(finding_row["decision_status"], "approved")
            self.assertEqual(finding_row["suggested_code"], body["suggested_code_override"])
            self.assertEqual(finding_row["branch_name"], result["branch_name"])
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                decisions = connection.execute("SELECT count(*) FROM finding_decisions").fetchone()[0]
                requests = connection.execute("SELECT count(*) FROM finding_decision_requests").fetchone()[0]
                audits = connection.execute(
                    "SELECT count(*) FROM security_audit_log WHERE permission_checked='web:finding_decision'",
                ).fetchone()[0]
            self.assertEqual((decisions, requests, audits), (1, 1, 1))
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE finding_decisions SET decision_status='rejected'")
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM finding_decisions")
            client.close()

    def test_request_changes_records_without_calling_branch_port(self):
        from agente_gestion.application.finding_decisions import FindingDecisionService
        from agente_gestion.api.repository import SQLiteQueryRepository

        class UnexpectedPort:
            def create_verification_branch(self, **_kwargs):
                raise AssertionError("non-approval decision must not create a branch")

        with tempfile.TemporaryDirectory() as tmp:
            fixture, storage = self._action_fixture(Path(tmp))
            candidate, excerpt, source, repo, origin = SQLiteQueryRepository(
                fixture.client.app.state.settings,
            ).finding_action_context(RUN_ID, FINDING_ID)
            result = FindingDecisionService(storage, UnexpectedPort()).decide(
                project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
                candidate=candidate, decision="request_changes", idempotency_key="changes-key-0001",
                actor_id="gestor-local", session_cookie="cookie-fingerprint-only",
                original_excerpt=excerpt, source=source, repository=repo, base_branch="main",
                reason="Ajustar el ejemplo antes de aprobar.", origin=origin,
            )
            self.assertEqual(result["decision_status"], "changes_requested")
            self.assertEqual(result["execution_mode"], "sandbox")
            self.assertIsNone(result["branch_name"])
            self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "changes_requested")

    def test_failure_is_durably_replayed_without_a_second_code_action(self):
        from agente_gestion.adapters.github_code_action_adapter import CodeActionError
        from agente_gestion.application.finding_decisions import FindingDecisionError, FindingDecisionService
        from agente_gestion.api.repository import SQLiteQueryRepository

        class FailingPort:
            def __init__(self):
                self.calls = 0
            def create_verification_branch(self, **_kwargs):
                self.calls += 1
                raise CodeActionError("github_no_disponible", "GitHub no respondió.", 503)

        with tempfile.TemporaryDirectory() as tmp:
            fixture, storage = self._action_fixture(Path(tmp))
            candidate, excerpt, source, repo, origin = SQLiteQueryRepository(
                fixture.client.app.state.settings,
            ).finding_action_context(RUN_ID, FINDING_ID)
            origin = self._configured_action_origin(fixture, origin)
            port = FailingPort()
            service = FindingDecisionService(storage, port)
            args = {
                "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
                "candidate": candidate, "decision": "approve", "idempotency_key": "failure-key-0001",
                "actor_id": "gestor-local", "session_cookie": "cookie-fingerprint-only",
                "original_excerpt": excerpt, "source": source, "repository": repo,
                "base_branch": "main", "suggested_code_override": None,
                "target_branch": "quality/failure-replay", "reason": "Revisión explícita",
                "origin": origin,
            }
            for _ in range(2):
                with self.assertRaises(FindingDecisionError) as caught:
                    service.decide(**args)
                self.assertEqual((caught.exception.status_code, caught.exception.code),
                                 (503, "github_no_disponible"))
            self.assertEqual(port.calls, 1)
            self.assertEqual(storage.get_findings(RUN_ID)[0]["decision_status"], "failed")
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                rows = connection.execute(
                    "SELECT state, error_code FROM finding_decision_requests WHERE finding_id=?",
                    (FINDING_ID,),
                ).fetchall()
                decisions = connection.execute("SELECT count(*) FROM finding_decisions").fetchone()[0]
            self.assertEqual(rows, [("failed", "github_no_disponible")])
            self.assertEqual(decisions, 1)

    def test_concurrent_decision_is_refused_while_first_action_is_reserved(self):
        from agente_gestion.application.finding_decisions import FindingDecisionError, FindingDecisionService
        from agente_gestion.api.repository import SQLiteQueryRepository
        from agente_gestion.domain.code_action import BranchExecutionResult

        class BlockingPort:
            def __init__(self):
                self.started = Event()
                self.release = Event()
                self.calls = 0
            def create_verification_branch(self, **_kwargs):
                self.calls += 1
                self.started.set()
                if not self.release.wait(5):
                    raise TimeoutError("test release timed out")
                return BranchExecutionResult(
                    execution_mode="sandbox", branch_name="quality/concurrent",
                    branch_url="sandbox://branches/quality/concurrent", commit_sha_applied="d" * 64,
                )

        with tempfile.TemporaryDirectory() as tmp:
            fixture, storage = self._action_fixture(Path(tmp))
            candidate, excerpt, source, repo, origin = SQLiteQueryRepository(
                fixture.client.app.state.settings,
            ).finding_action_context(RUN_ID, FINDING_ID)
            origin = self._configured_action_origin(fixture, origin)
            port = BlockingPort()
            service = FindingDecisionService(storage, port)
            base = {
                "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
                "candidate": candidate, "decision": "approve", "actor_id": "gestor-local",
                "session_cookie": "cookie-fingerprint-only", "original_excerpt": excerpt,
                "source": source, "repository": repo, "base_branch": "main",
                "suggested_code_override": None, "reason": None, "origin": origin,
            }
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(service.decide, **{
                    **base, "idempotency_key": "concurrent-key-0001",
                    "target_branch": "quality/concurrent",
                })
                self.assertTrue(port.started.wait(5))
                with self.assertRaises(FindingDecisionError) as caught:
                    service.decide(**{
                        **base, "idempotency_key": "concurrent-key-0002",
                        "target_branch": "quality/concurrent-other",
                    })
                self.assertEqual(caught.exception.code, "decision_en_curso")
                port.release.set()
                self.assertEqual(first.result(timeout=5)["decision_status"], "approved")
            self.assertEqual(port.calls, 1)

    def test_decision_requires_idempotency_key_and_manager_project_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, _storage = self._action_fixture(Path(tmp))
            client = fixture.client
            login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                headers={"Origin": "http://testserver"})
            csrf = login.json()["csrf_token"]
            base = f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision"
            response = client.post(base, headers={
                "Origin": "http://testserver", "X-CSRF-Token": csrf,
            }, json={"decision": "request_changes"})
            self.assertEqual(response.status_code, 422)

            # A configured but out-of-session project is rejected before any decision is stored.
            from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
            settings = ApiSettings(
                db_path=str(fixture.database_path), governance_enabled=True,
                projects=(ProjectConfig(id=PROJECT_ID, name="Proyecto", source_mode="fixtures"),),
                operator_token=OPERATOR_TOKEN, operator_project_ids=("OTHER",),
                allowed_origins=("http://testserver",), allowed_hosts=("testserver",),
            )
            scoped = create_app(settings)
            from agente_gestion.api.finding_actions import router as finding_actions_router
            scoped.include_router(finding_actions_router)
            scoped_client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(scoped)
            login = scoped_client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                       headers={"Origin": "http://testserver"})
            self.assertEqual(login.status_code, 200, login.text)
            denied = scoped_client.post(base, headers={
                "Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"],
                "Idempotency-Key": "phase2-scope-0001",
            }, json={"decision": "request_changes"})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"]["code"], "proyecto_fuera_de_sesion")
            scoped_client.close()

            disabled = create_app(fixture.client.app.state.settings.model_copy(update={
                "governance_enabled": False,
            }))
            from agente_gestion.api.finding_actions import router as finding_actions_router
            disabled.include_router(finding_actions_router)
            disabled_client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(disabled)
            login = disabled_client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                         headers={"Origin": "http://testserver"})
            response = disabled_client.post(base, headers={
                "Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"],
                "Idempotency-Key": "phase2-disabled-0001",
            }, json={"decision": "request_changes"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "gobernanza_no_habilitada")
            disabled_client.close()


if __name__ == "__main__":
    unittest.main()
