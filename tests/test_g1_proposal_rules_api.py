"""Isolated HTTP walkthrough for the G1 fix.

A web-initiated analysis must load the owner's proposal rules through the same
validated ``DomainProposalRuleSet`` contract the CLI already uses, publish the
resulting proposals, and keep every execution gate closed by default.

Every source here is simulated: no network, no LLM provider, no external write.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import create_app
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.run import RunStatus
from agente_gestion.ports.source_collection import SourceCollectionResult
from agente_gestion.domain.snapshot import WeeklySnapshot

TIMEZONE = ZoneInfo("America/Guayaquil")
ORIGIN = "http://127.0.0.1:5173"
OPERATOR_TOKEN = "g1-fixture-operator-token-never-use-in-production"
PROJECT = "G1TEST"
OTHER_PROJECT = "G1OTHER"
TASK_ID = "T-G1"
CARD_ID = "card-g1-1"
DUE_DATE = datetime(2026, 10, 14, 17, tzinfo=TIMEZONE)
PROPOSED_DUE = "2026-10-18T17:00:00"


def _task_card(task_id: str = TASK_ID, card_id: str = CARD_ID) -> TaskCard:
    return TaskCard(
        id=task_id,
        title="Tarea con fecha vencida",
        list_id="list-prog",
        list_name="En curso",
        state=TaskState.in_progress,
        due_date=DUE_DATE,
        external_id=card_id,
        is_included=True,
        observed_fields=["due_date", "state", "list_name", "title"],
    )


def _task_evidence(project_id: str, card: TaskCard) -> EvidenceItem:
    payload = card.model_dump(mode="json")
    payload["task_id"] = card.id
    payload["card_id"] = card.external_id
    payload["observed_fields"] = list(card.observed_fields or [])
    return EvidenceItem(
        provenance=EvidenceProvenance(
            id=f"trello-task-{card.external_id}",
            source=EvidenceSource.trello,
            project_id=project_id,
            external_id=card.external_id,
            timestamp=DUE_DATE,
            payload=payload,
        ),
        category="task",
        summary=f"Tarjeta simulada {card.id}",
    )


class SelectingFakeProvider(FakeProvider):
    """Simulated provider that behaves like a grounded Monitoring model.

    The shared :class:`FakeProvider` never invents a selection, and that
    contract stays untouched. This double only stands in for a real model that
    reads the grounded choices the backend computed and picks one per task and
    field. It can never select a change the deterministic candidate set did not
    offer.
    """

    def generate(self, request):
        result = super().generate(request)
        choices = request.evidences.get("proposal_choices", [])
        if not choices:
            return result
        selected: list[str] = []
        seen: set[tuple[str, str]] = set()
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            choice_id = choice.get("choice_id")
            if not isinstance(choice_id, str) or not choice_id:
                continue
            target = (str(choice.get("task_id")), str(choice.get("field")))
            if target in seen:
                continue
            seen.add(target)
            selected.append(choice_id)
        if not selected:
            return result
        structured = dict(result.structured_data or {})
        structured["selected_choice_ids"] = selected
        return result.model_copy(update={"structured_data": structured})


class SimulatedCollectionSource:
    """Simulated collection that emits task evidence like the real board adapter."""

    def __init__(self, project_id: str, card: TaskCard, captured_at: datetime | None = None):
        self._project_id = project_id
        self._card = card
        self._captured_at = captured_at

    def collect_snapshot(self, project_id, cutoff_datetime, commit_sha=None):
        cutoff = self._captured_at or cutoff_datetime
        snapshot = WeeklySnapshot(
            id=f"snap-{project_id}",
            project_id=project_id,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=[self._card],
            code_diffs=[],
            ci_runs=[],
            policies=[],
            is_complete=True,
            # The real board collection reports a task source identity and a
            # complete task enumeration; the technical review requires both.
            task_source_identity="trello-sha256:" + "0" * 64,
            tasks_complete=True,
        )
        return SourceCollectionResult(
            snapshot=snapshot,
            evidence=[_task_evidence(project_id, self._card)],
        )


def _rules_document(project_id: str, task_ids: list[str] | None = None) -> dict:
    return {
        "project_id": project_id,
        "rules": [
            {
                "rule_id": "rule-extend-due",
                "task_ids": task_ids or [TASK_ID],
                "field": "due_date",
                "allowed_values": [PROPOSED_DUE],
                "rationale": "La tarea bloqueada necesita una fecha de entrega revisada.",
                "expected_impact": "Reduce el riesgo de vencimiento de la entrega activa.",
            }
        ],
    }


class _G1Harness:
    """Shared, deterministic setup for the web proposal rules walkthrough."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="g1-proposal-rules-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.card = _task_card()

    # ---------------------------------------------------------------- helpers

    def _write_rules(self, document: dict, name: str = "rules.json") -> str:
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def _write_config(self, rules_path: str | None, project_rules_path: str | None = None) -> str:
        lines = [
            "project:",
            f"  id: {PROJECT}",
            "  timezone: America/Guayaquil",
            "allow_remote_ai: false",
            "external_writes_enabled: false",
            "execution:",
            "  due_soon_days: 7",
            "providers:",
            "  remote:",
            "    model: fake-model-v1",
            "sources:",
            "  mode: fixtures",
        ]
        if rules_path:
            lines.append(f"monitoring_rules_path: {rules_path}")
        if project_rules_path:
            lines.append(f"  monitoring_rules_path: {project_rules_path}")
        path = self.root / "config.yaml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _client(self, config_path: str, project: ProjectConfig, authenticate: bool = True) -> TestClient:
        settings = ApiSettings(
            db_path=str(self.root / "agente.db"),
            runs_dir=str(self.root / "runs"),
            outbox_dir=str(self.root / "outbox"),
            job_ledger_path=str(self.root / "jobs.sqlite"),
            branches_dir=str(self.root / "branches"),
            analysis_config_path=config_path,
            projects=(project,),
            analysis_enabled=True,
            governance_enabled=True,
            operator_token=SecretStr(OPERATOR_TOKEN),
            operator_actor="gestora-g1-fixture",
            operator_project_ids=(PROJECT,),
            external_writes_enabled=False,
        )
        client = TestClient(create_app(settings), base_url="http://127.0.0.1")
        self.addCleanup(client.close)
        self.csrf = None
        if authenticate:
            login = client.post(
                "/api/v1/auth/login", json={"token": OPERATOR_TOKEN}, headers={"Origin": ORIGIN},
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.csrf = login.json()["csrf_token"]
        return client

    def _headers(self, **extra: str) -> dict:
        headers = {"Origin": ORIGIN}
        if self.csrf is not None:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(extra)
        return headers

    def _run_analysis(self, client: TestClient) -> dict:
        """Prepare, confirm and await one web analysis job over real HTTP."""
        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True},
            headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        preparation = prepared.json()
        confirmed = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/confirm",
            json={
                "preparation_id": preparation["preparation_id"],
                "configuration_fingerprint": preparation["configuration_fingerprint"],
                "confirm_analysis": True,
                "remote_consent": False,
            },
            headers=self._headers(**{"Idempotency-Key": "g1-analysis-key-0001"}),
        )
        self.assertEqual(confirmed.status_code, 202, confirmed.text)
        job_id = confirmed.json()["job"]["job_id"]
        for _ in range(120):
            polled = client.get(f"/api/v1/analysis/jobs/{job_id}")
            self.assertEqual(polled.status_code, 200, polled.text)
            view = polled.json()
            if view["status"] in {"completed", "failed"}:
                self.assertEqual(view["status"], "completed", view)
                return view
            import time
            time.sleep(0.1)
        self.fail("The analysis job did not finish in time.")

    def _proposals(self, client: TestClient) -> dict:
        response = client.get(f"/api/v1/projects/{PROJECT}/proposals?page_size=100")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _with_simulated_source(self, captured_at: datetime | None = None):
        """Swap only the simulated collaborators; the wiring stays real."""
        source = SimulatedCollectionSource(PROJECT, self.card, captured_at=captured_at)
        return patch.multiple(
            "agente_gestion.cli",
            FixtureSourcesAdapter=lambda fixtures_dir=None, **kwargs: source,
            FakeProvider=SelectingFakeProvider,
        )

    # ------------------------------------------------------------------ tests


class G1WebProposalRulesTests(_G1Harness, unittest.TestCase):
    """Web-initiated analysis must publish owner rules as proposals."""

    def test_web_analysis_with_owner_rules_publishes_and_lists_proposals(self):
        """A web-initiated analysis loads owner rules and publishes real proposals."""
        rules_path = self._write_rules(_rules_document(PROJECT))
        config_path = self._write_config(rules_path)
        project = ProjectConfig(
            id=PROJECT, name="Proyecto G1", source_mode="fixtures",
            monitoring_rules_path=rules_path,
        )
        client = self._client(config_path, project)

        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True}, headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(
            prepared.json()["preflight"]["proposal_rules_status"], "configured",
            prepared.text,
        )

        with self._with_simulated_source():
            self._run_analysis(client)

        listing = self._proposals(client)
        self.assertEqual(listing["total"], 1, listing)
        summary = listing["items"][0]
        self.assertEqual(summary["task_id"], TASK_ID)
        self.assertEqual(summary["field"], "due_date")
        self.assertEqual(summary["execution_status"], "not_executed")

        detail = client.get(
            f"/api/v1/projects/{PROJECT}/proposals/{summary['proposal_id']}"
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["task_id"], TASK_ID)

    def test_shared_yaml_rules_path_is_accepted_like_the_cli(self):
        """The top-level YAML key keeps working exactly as the CLI reads it."""
        rules_path = self._write_rules(_rules_document(PROJECT))
        config_path = self._write_config(rules_path)
        project = ProjectConfig(id=PROJECT, name="Proyecto G1", source_mode="fixtures")
        client = self._client(config_path, project)

        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True}, headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(
            prepared.json()["preflight"]["proposal_rules_status"], "configured",
            prepared.text,
        )

    def test_analysis_without_rules_keeps_running_and_publishes_nothing(self):
        """No rules must not block the analysis and must not invent proposals."""
        config_path = self._write_config(None)
        project = ProjectConfig(id=PROJECT, name="Proyecto G1", source_mode="fixtures")
        client = self._client(config_path, project)

        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True}, headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(
            prepared.json()["preflight"]["proposal_rules_status"], "not_configured",
            prepared.text,
        )
        self.assertEqual(prepared.json()["eligible"], True, prepared.text)

        with self._with_simulated_source():
            self._run_analysis(client)

        self.assertEqual(self._proposals(client)["total"], 0)

    def test_invalid_rules_file_reports_invalid_without_publishing(self):
        """A malformed rules file is reported and never produces a proposal."""
        broken = self.root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        config_path = self._write_config(str(broken))
        project = ProjectConfig(id=PROJECT, name="Proyecto G1", source_mode="fixtures")
        client = self._client(config_path, project)

        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True}, headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(
            prepared.json()["preflight"]["proposal_rules_status"], "invalid",
            prepared.text,
        )

        with self._with_simulated_source():
            self._run_analysis(client)

        self.assertEqual(self._proposals(client)["total"], 0)

    def test_rules_of_another_project_never_reach_this_project(self):
        """A rules file written for another project is not applied here."""
        foreign_path = self._write_rules(_rules_document(OTHER_PROJECT), "foreign.json")
        config_path = self._write_config(foreign_path)
        project = ProjectConfig(
            id=PROJECT, name="Proyecto G1", source_mode="fixtures",
            monitoring_rules_path=foreign_path,
        )
        client = self._client(config_path, project)

        prepared = client.post(
            f"/api/v1/projects/{PROJECT}/analysis/prepare",
            json={"request_analysis": True}, headers=self._headers(),
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(
            prepared.json()["preflight"]["proposal_rules_status"], "not_configured",
            prepared.text,
        )

        with self._with_simulated_source():
            self._run_analysis(client)

        self.assertEqual(self._proposals(client)["total"], 0)

    def test_rule_naming_a_task_outside_the_project_is_discarded(self):
        """A rule that names a task absent from the project yields no proposal."""
        rules_path = self._write_rules(_rules_document(PROJECT, task_ids=["T-OTHER"]))
        config_path = self._write_config(rules_path)
        project = ProjectConfig(
            id=PROJECT, name="Proyecto G1", source_mode="fixtures",
            monitoring_rules_path=rules_path,
        )
        client = self._client(config_path, project)

        with self._with_simulated_source():
            self._run_analysis(client)

        self.assertEqual(self._proposals(client)["total"], 0)


class G1ProposalExecutionGateTests(_G1Harness, unittest.TestCase):
    """Every execution gate must stay closed for the proposals the web publishes."""

    def _publish(self, rules_path: str | None = None, authenticate: bool = True,
                 captured_at: datetime | None = None) -> tuple[TestClient, str]:
        rules_path = rules_path or self._write_rules(_rules_document(PROJECT))
        config_path = self._write_config(rules_path)
        project = ProjectConfig(
            id=PROJECT, name="Proyecto G1", source_mode="fixtures",
            monitoring_rules_path=rules_path,
        )
        client = self._client(config_path, project, authenticate=authenticate)
        with self._with_simulated_source(captured_at=captured_at):
            self._run_analysis(client)
        listing = self._proposals(client)
        self.assertEqual(listing["total"], 1, listing)
        return client, listing["items"][0]["proposal_id"]

    def _decide(self, client: TestClient, csrf: str | None, proposal_id: str,
                body: dict, key: str):
        headers = {"Origin": ORIGIN, "Idempotency-Key": key}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        return client.post(
            f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}/execute",
            json=body, headers=headers,
        )

    def test_published_proposal_starts_unauthorized_and_unexecuted(self):
        """A published proposal is a recommendation, never an authorization."""
        client, proposal_id = self._publish()
        detail = client.get(f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body["review_status"], "pending")
        self.assertEqual(body["authorization_status"], "not_authorized")
        self.assertEqual(body["execution_status"], "not_executed")
        self.assertFalse(body["technical_review"]["execution_authorized"])
        self.assertEqual(body["technical_review"]["technical_status"], "ready")

    def test_proposal_without_manager_session_cannot_be_executed(self):
        """No authenticated human decision means no execution at all."""
        client, proposal_id = self._publish()
        client.cookies.clear()

        denied = self._decide(client, None, proposal_id, {
            "decision": "approve_and_execute", "reason": "Intento sin sesión.",
        }, "g1-no-session-key")
        self.assertEqual(denied.status_code, 401, denied.text)
        detail = client.get(f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}")
        self.assertEqual(detail.json()["execution_status"], "not_executed")
        self.assertEqual(detail.json()["authorization_status"], "not_authorized")

    def test_proposal_without_csrf_token_cannot_be_executed(self):
        """A request without the CSRF proof is refused before any decision."""
        client, proposal_id = self._publish()
        denied = client.post(
            f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}/execute",
            json={"decision": "approve_and_execute", "reason": "Intento sin CSRF."},
            headers={"Origin": ORIGIN, "Idempotency-Key": "g1-no-csrf-key"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        detail = client.get(f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}")
        self.assertEqual(detail.json()["execution_status"], "not_executed")

    def test_stale_proposal_is_blocked_and_cannot_be_executed(self):
        """A proposal whose capture is no longer the latest stays blocked."""
        observed_at = datetime(2026, 3, 1, 12, tzinfo=TIMEZONE)
        storage = SQLiteStorageAdapter(str(self.root / "agente.db"))
        storage.save_snapshot_with_capture_receipt(WeeklySnapshot(
            id="later-capture",
            project_id=PROJECT,
            cutoff_datetime=observed_at + timedelta(days=1),
            captured_at=observed_at + timedelta(days=1),
            tasks=[self.card],
            code_diffs=[],
            ci_runs=[],
            policies=[],
            is_complete=True,
            task_source_identity="trello-sha256:" + "1" * 64,
            tasks_complete=True,
        ))

        client, proposal_id = self._publish(captured_at=observed_at)

        detail = client.get(f"/api/v1/projects/{PROJECT}/proposals/{proposal_id}")
        self.assertEqual(detail.json()["freshness"], "stale")
        self.assertEqual(detail.json()["technical_review"]["technical_status"], "blocked")

        with patch("agente_gestion.application.proposal_execution.TrelloActionAdapter") as trello_port:
            response = self._decide(client, self.csrf, proposal_id, {
                "decision": "approve_and_execute",
                "reason": "Intento sobre una propuesta obsoleta.",
            }, "g1-stale-key")
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt["execution"]["status"], "DENIED")
        self.assertIn("technical_review_blocked", receipt["execution"]["reasons"])
        self.assertFalse(receipt["execution"]["live_state_changed"])
        trello_port.assert_not_called()

    def test_catalogue_refuses_a_proposal_whose_evidence_is_insufficient(self):
        """The store itself rejects a submission whose evidence cannot be verified."""
        client, proposal_id = self._publish()
        storage = SQLiteStorageAdapter(str(self.root / "agente.db"))
        stored = storage.get_action_proposal(proposal_id)
        self.assertIsNotNone(stored)
        proposal, evidence = stored
        broken = proposal.model_copy(update={
            "proposal_id": "proposal-g1-insufficient-evidence",
            "evidence_ids": ["evidence-that-does-not-exist"],
        })
        with self.assertRaises(ValueError):
            storage.save_action_proposal(broken, [])

    def test_rule_without_supporting_evidence_publishes_nothing(self):
        """A rule the observed evidence does not support yields no proposal."""
        card = self.card.model_copy(update={"due_date": DUE_DATE + timedelta(days=5)})
        self.card = card
        original = SimulatedCollectionSource.collect_snapshot

        def mismatched(self_source, project_id, cutoff_datetime, commit_sha=None):
            result = original(self_source, project_id, cutoff_datetime, commit_sha)
            payload = dict(result.evidence[0].provenance.payload)
            payload["due_date"] = (DUE_DATE + timedelta(days=9)).isoformat()
            result.evidence[0].provenance.payload.update(payload)
            return result

        rules_path = self._write_rules(_rules_document(PROJECT))
        config_path = self._write_config(rules_path)
        project = ProjectConfig(
            id=PROJECT, name="Proyecto G1", source_mode="fixtures",
            monitoring_rules_path=rules_path,
        )
        client = self._client(config_path, project)
        with patch.object(SimulatedCollectionSource, "collect_snapshot", mismatched):
            with self._with_simulated_source():
                self._run_analysis(client)

        self.assertEqual(self._proposals(client)["total"], 0)


if __name__ == "__main__":
    unittest.main()
