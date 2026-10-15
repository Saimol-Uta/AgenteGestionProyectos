from datetime import datetime, timezone
import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.adapters.sources.trello_action_adapter import TrelloActionAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import create_app
from agente_gestion.api.proposal_execution import router as proposal_execution_router
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.application.proposal_execution import (
    ProposalExecutionError,
    ProposalExecutionService,
)
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState


class TrelloPriorityAdapterTests(unittest.TestCase):
    LABELS = {"alta": "label-high", "media": "label-medium", "baja": "label-low"}

    def test_priority_read_put_and_readback_preserve_unrelated_labels(self):
        adapter = TrelloActionAdapter(
            board_id="board-1", api_key="fixture-key", token="fixture-token",
            priority_labels=self.LABELS,
        )
        get_before = MagicMock(status_code=200)
        get_before.json.return_value = {
            "idBoard": "board-1", "idLabels": ["other-label", "label-low"],
        }
        get_for_write = MagicMock(status_code=200)
        get_for_write.json.return_value = {
            "idBoard": "board-1", "idLabels": ["other-label", "label-low"],
        }
        get_after = MagicMock(status_code=200)
        get_after.json.return_value = {
            "idBoard": "board-1", "idLabels": ["other-label", "label-high"],
        }
        with patch("requests.get", side_effect=[get_before, get_for_write, get_after]) as mock_get, \
             patch("requests.put", return_value=MagicMock(status_code=200)) as mock_put:
            self.assertEqual(adapter.read_card_field("card-1", "priority"), "baja")
            self.assertTrue(adapter.apply_card_field_update("card-1", "priority", "alta"))
            observed = adapter.read_card_field("card-1", "priority")

        self.assertEqual(observed, "alta")
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_put.call_count, 1)
        self.assertEqual(mock_put.call_args.kwargs["json"], {"idLabels": ["other-label", "label-high"]})
        self.assertEqual(mock_put.call_args.kwargs["timeout"], 10.0)

    def test_missing_or_incomplete_mapping_is_rejected_before_put(self):
        with patch("requests.put") as mock_put:
            with self.assertRaises(ValueError):
                TrelloActionAdapter(
                    board_id="board-1", api_key="fixture-key", token="fixture-token",
                    priority_labels={"alta": "label-high"},
                )
        mock_put.assert_not_called()

    def test_write_permission_is_read_only_and_rejects_observers_or_denied_access(self):
        adapter = TrelloActionAdapter(board_id="board-1", api_key="fixture-key", token="fixture-token")
        for role, expected in (("normal", True), ("admin", True), ("observer", False)):
            with self.subTest(role=role):
                payloads = [
                    {"id": "member-1"}, {"id": "board-1", "closed": False},
                    [{"idMember": "member-1", "memberType": role, "deactivated": False}],
                ]
                responses = [MagicMock(status_code=200) for _ in payloads]
                for response, payload in zip(responses, payloads):
                    response.json.return_value = payload
                with patch("requests.get", side_effect=responses) as read, patch("requests.put") as write:
                    self.assertEqual(adapter.has_write_permission(), expected)
                self.assertEqual(read.call_count, 3)
                self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in read.call_args_list))
                write.assert_not_called()
        with patch("requests.get", return_value=MagicMock(status_code=403)) as read, patch("requests.put") as write:
            self.assertFalse(adapter.has_write_permission())
        self.assertEqual(read.call_count, 1)
        write.assert_not_called()

    def test_existing_direct_execution_contract_still_rejects_unmapped_priority(self):
        adapter = TrelloActionAdapter(board_id="board-1", api_key="fixture-key", token="fixture-token")
        self.assertNotIn("priority", adapter.supported_fields())


class ProposalExecutionServiceTests(unittest.TestCase):
    def test_priority_map_normalizes_spanish_and_domain_names(self):
        normalized = ProposalExecutionService.validate_priority_mapping({
            "alta": "label-high", "media": "label-medium", "baja": "label-low",
        })
        self.assertEqual(normalized, {"high": "label-high", "medium": "label-medium", "low": "label-low"})
        self.assertEqual(ProposalExecutionService.execution_mode_for(
            source_mode="fixtures", external_writes_enabled=True,
            trello_api_key=None, trello_token=None,
        ), "sandbox")
        self.assertEqual(ProposalExecutionService.execution_mode_for(
            source_mode="real", external_writes_enabled=True,
            trello_api_key="fixture-key", trello_token=None,
        ), "sandbox")


class ProposalExecutionApiTests(unittest.TestCase):
    PROJECT = "board-1"
    OPERATOR_TOKEN = "fixture-operator-token-with-more-than-32-characters"
    LABELS = {"alta": "label-high", "media": "label-medium", "baja": "label-low"}

    def setUp(self):
        permission = patch.object(TrelloActionAdapter, "has_write_permission", return_value=True)
        permission.start()
        self.addCleanup(permission.stop)

    def _client(self, *, source_mode="fixtures", external_writes_enabled=False,
                priority_labels=None, field="due_date", operator_project_ids=None,
                authenticate=True):
        temporary = tempfile.TemporaryDirectory()
        database = str(Path(temporary.name) / "phase2.sqlite")
        storage = SQLiteStorageAdapter(database)
        cutoff = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        card = TaskCard(
            id="card-1", external_id="card-1", title="Actualizar la base",
            state=TaskState.in_progress, list_name="En curso", is_included=True,
            due_date=datetime(2026, 10, 14, 17, tzinfo=timezone.utc),
            priority="media", assignee_ids=["dev-1"],
            observed_fields=["due_date", "priority", "assignee_ids"],
        )
        snapshot = WeeklySnapshot(
            id="snap-1", project_id=self.PROJECT, cutoff_datetime=cutoff,
            captured_at=cutoff, tasks=[card], task_source_identity="fixture:board-1",
            tasks_complete=True,
        )
        capture = storage.save_snapshot_with_capture_receipt(snapshot)
        values = {
            "due_date": (card.due_date, datetime(2026, 10, 18, 17, tzinfo=timezone.utc)),
            "priority": ("media", "alta"),
            "assignee_ids": (["dev-1"], ["dev-2"]),
        }
        current, proposed = values[field]
        evidence_values = {
            "task_id": card.id, "card_id": card.id,
            "due_date": card.due_date.isoformat(), "priority": card.priority,
            "assignee_ids": card.assignee_ids, "observed_fields": ["due_date", "priority", "assignee_ids"],
        }
        evidence = EvidenceItem(
            category="task", summary="Hecho de tarea verificado",
            provenance=EvidenceProvenance(
                id="ev-1", source="trello", project_id=self.PROJECT, external_id=card.id,
                timestamp=cutoff, payload=evidence_values,
            ),
        )
        proposal = ActionProposal(
            proposal_id="proposal-1", project_id=self.PROJECT, task_id=card.id,
            observed_capture_id=capture.capture_id, field=field,
            current_value=current, proposed_value=proposed, evidence_ids=["ev-1"],
            rationale="Propuesta técnica", expected_impact="Mejora planificación",
        )
        storage.save_action_proposal(proposal, [evidence])
        settings = ApiSettings(
            db_path=database,
            projects=(ProjectConfig(id=self.PROJECT, name="Proyecto fixture", source_mode=source_mode),),
            governance_enabled=True,
            operator_token=SecretStr(self.OPERATOR_TOKEN),
            operator_actor="gestora-verificada",
            operator_project_ids=(self.PROJECT,) if operator_project_ids is None else operator_project_ids,
            external_writes_enabled=external_writes_enabled,
            trello_api_key=SecretStr("fixture-key") if external_writes_enabled else None,
            trello_token=SecretStr("fixture-token") if external_writes_enabled else None,
            trello_priority_labels={self.PROJECT: priority_labels or {}},
            trello_board_ids={self.PROJECT: self.PROJECT},
        )
        app = create_app(settings)
        app.include_router(proposal_execution_router)
        client = TestClient(app, base_url="http://127.0.0.1")
        csrf_token = None
        if authenticate:
            login = client.post("/api/v1/auth/login", json={"token": self.OPERATOR_TOKEN},
                                headers={"Origin": "http://127.0.0.1:5173"})
            self.assertEqual(login.status_code, 200, login.text)
            csrf_token = login.json()["csrf_token"]
        return temporary, client, csrf_token, storage

    def _execute(self, client, csrf, *, key="idem-1", body=None):
        return client.post(
            f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1/execute",
            json=body or {"decision": "approve_and_execute", "reason": "Aprobado por la gestora"},
            headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": csrf,
                     "Idempotency-Key": key},
        )

    def _assert_human_reason_is_auditable(self, storage, client, response, reason):
        execution_id = response.json()["execution"]["execution_id"]
        proposal_id = response.json()["proposal_id"]
        connection = storage._get_connection()
        try:
            execution = connection.execute(
                "SELECT human_reason FROM proposal_executions WHERE execution_id=?", (execution_id,),
            ).fetchone()
            audit = connection.execute(
                "SELECT human_reason, reason FROM security_audit_log "
                "WHERE project_id=? AND resource_id=? AND permission_checked='web:proposal_execute'",
                (self.PROJECT, proposal_id),
            ).fetchone()
            request = connection.execute(
                "SELECT request_sha256 FROM proposal_execution_idempotency "
                "WHERE project_id=? AND proposal_id=?", (self.PROJECT, proposal_id),
            ).fetchone()
            self.assertEqual(execution["human_reason"], reason)
            self.assertEqual(audit["human_reason"], reason)
            self.assertTrue(audit["reason"].startswith("request_sha256="))
            self.assertRegex(request["request_sha256"], r"^[0-9a-f]{64}$")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE proposal_executions SET human_reason='tampered' WHERE execution_id=?",
                    (execution_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM security_audit_log WHERE resource_id=? AND permission_checked='web:proposal_execute'",
                    (proposal_id,),
                )
        finally:
            connection.close()
        audit_detail = client.get(
            f"/api/v1/projects/{self.PROJECT}/audit/{execution_id}"
        )
        self.assertEqual(audit_detail.status_code, 200, audit_detail.text)
        self.assertEqual(audit_detail.json()["details"]["human_reason"], reason)

    def test_proposal_session_advertises_only_the_unified_and_preserved_governance_capabilities(self):
        temporary, client, _csrf, _storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            response = client.get("/api/v1/auth/session")
            self.assertEqual(response.status_code, 200, response.text)
            capabilities = set(response.json()["capabilities"])
            self.assertTrue({
                "governance:proposal_execute", "quality:decide", "notifications:email_send",
            }.issubset(capabilities))
            self.assertTrue({
                "governance:proposal_review", "governance:dry_run_authorization", "governance:dry_run",
            }.isdisjoint(capabilities))
        finally:
            client.close()

    def test_legacy_proposal_mutation_routes_are_not_public_aliases(self):
        temporary, client, csrf, _storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            for suffix in ("review", "dry-run-authorization", "dry-runs"):
                with self.subTest(route=suffix):
                    response = client.post(
                        f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1/{suffix}",
                        json={},
                        headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": csrf,
                                 "Idempotency-Key": "retired-route-01"},
                    )
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertEqual(response.json()["error"]["code"], "recurso_no_encontrado")
        finally:
            client.close()

    def test_missing_or_blank_human_reason_is_rejected_without_state_change(self):
        temporary, client, csrf, storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            before = client.get(
                f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1"
            ).json()
            for body in (
                {"decision": "approve_and_execute"},
                {"decision": "approve_and_execute", "reason": "  "},
            ):
                response = self._execute(client, csrf, body=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"]["code"], "validacion_invalida")
            after = client.get(
                f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1"
            ).json()
            self.assertEqual(after, before)
            self.assertEqual(storage.list_proposal_executions(self.PROJECT), [])
            connection = storage._get_connection()
            try:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM security_audit_log WHERE permission_checked='web:proposal_execute'"
                ).fetchone()[0], 0)
            finally:
                connection.close()
        finally:
            client.close()

    def test_human_reasons_are_exact_immutable_and_public_for_each_decision(self):
        cases = (
            ({"decision": "approve_and_execute"}, "  Aprobar: evidencia revisada\nsin cambio.  "),
            ({"decision": "modify_and_execute", "modified_due_date": "2026-10-20"},
             "  Ajuste solicitado — fecha confirmada.  "),
            ({"decision": "reject"}, "Rechazo: el fundamento no aplica."),
        )
        for index, (decision, reason) in enumerate(cases):
            with self.subTest(decision=decision["decision"]):
                temporary, client, csrf, storage = self._client()
                self.addCleanup(temporary.cleanup)
                body = {**decision, "reason": reason}
                key = f"reason-case-{index}"
                try:
                    response = self._execute(client, csrf, key=key, body=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    replay = self._execute(client, csrf, key=key, body=body)
                    self.assertTrue(replay.json()["replayed"])
                    self.assertEqual(len(storage.list_proposal_executions(
                        self.PROJECT, response.json()["proposal_id"])), 1)
                    self._assert_human_reason_is_auditable(storage, client, response, reason)
                finally:
                    client.close()

    def test_real_project_without_credentials_uses_sandbox_without_provider_requests(self):
        temporary, client, csrf, _storage = self._client(source_mode="real", external_writes_enabled=True)
        self.addCleanup(temporary.cleanup)
        self.addCleanup(client.close)
        client.app.state.settings = client.app.state.settings.model_copy(update={
            "trello_api_key": None, "trello_token": None,
        })
        with patch("requests.get") as read, patch("requests.put") as write:
            response = self._execute(client, csrf)
            replay = self._execute(client, csrf)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_mode"], "sandbox")
        self.assertEqual(response.json()["execution"]["status"], "SIMULATED")
        self.assertFalse(response.json()["execution"]["live_state_changed"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        read.assert_not_called()
        write.assert_not_called()

    def test_real_project_without_write_permission_uses_sandbox_before_mutation(self):
        temporary, client, csrf, _storage = self._client(source_mode="real", external_writes_enabled=True)
        self.addCleanup(temporary.cleanup)
        self.addCleanup(client.close)
        with patch.object(TrelloActionAdapter, "has_write_permission", return_value=False, create=True) as permission, \
             patch("requests.get") as read, patch("requests.put") as write:
            response = self._execute(client, csrf)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_mode"], "sandbox")
        self.assertEqual(response.json()["execution"]["status"], "SIMULATED")
        self.assertFalse(response.json()["execution"]["live_state_changed"])
        permission.assert_called_once_with()
        read.assert_not_called()
        write.assert_not_called()

    def test_fixture_execute_is_explicitly_simulated_and_replay_is_durable(self):
        temporary, client, csrf, storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            response = self._execute(client, csrf)
            replay = self._execute(client, csrf)
            conflict = self._execute(client, csrf, body={
                "decision": "approve_and_execute", "reason": "Una solicitud diferente",
            })
            other_key = self._execute(client, csrf, key="idem-2")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["proposal_id"], "proposal-1")
            self.assertEqual(response.json()["execution_mode"], "sandbox")
            self.assertEqual(response.json()["execution"]["status"], "SIMULATED")
            self.assertFalse(response.json()["execution"]["live_state_changed"])
            self.assertEqual(response.json()["execution"]["actor_id"], "gestora-verificada")
            self.assertEqual(response.json()["technical_review"]["technical_status"], "ready")
            self.assertFalse(response.json()["technical_review"]["execution_authorized"])
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(other_key.status_code, 409)
            self.assertEqual(len(storage.list_proposal_executions(self.PROJECT, "proposal-1")), 1)
            connection = storage._get_connection()
            try:
                audit = connection.execute(
                    "SELECT actor_id, decision, permission_checked, token_fingerprint "
                    "FROM security_audit_log WHERE project_id=? AND resource_id=?",
                    (self.PROJECT, "proposal-1"),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(audit["actor_id"], "gestora-verificada")
            self.assertEqual(audit["decision"], "granted")
            self.assertEqual(audit["permission_checked"], "web:proposal_execute")
            self.assertTrue(audit["token_fingerprint"].startswith("sha256:"))
        finally:
            client.close()

    def test_modified_decision_keeps_original_and_persists_derived_proposal(self):
        temporary, client, csrf, storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            response = self._execute(client, csrf, body={
                "decision": "modify_and_execute", "modified_due_date": "2026-10-20",
                "reason": "Reprogramación revisada",
            })
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertNotEqual(body["proposal_id"], "proposal-1")
            self.assertEqual(body["original_proposal_id"], "proposal-1")
            self.assertEqual(body["proposal"]["proposed_value"], "2026-10-20T00:00:00-05:00")
            original = storage.get_action_proposal("proposal-1")[0]
            derived = storage.get_action_proposal(body["proposal_id"])[0]
            self.assertEqual(original.proposed_value, datetime(2026, 10, 18, 17, tzinfo=timezone.utc))
            self.assertEqual(derived.rationale, "Reprogramación revisada")
        finally:
            client.close()

    def test_modified_priority_and_assignees_use_the_exact_requested_fields(self):
        temporary, client, csrf, storage = self._client(field="priority", priority_labels=self.LABELS)
        self.addCleanup(temporary.cleanup)
        try:
            response = self._execute(client, csrf, body={
                "decision": "modify_and_execute", "modified_priority": "high",
                "reason": "Prioridad revisada",
            })
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["execution"]["status"], "SIMULATED")
            self.assertEqual(payload["execution"]["verification_status"], "verified")
            self.assertEqual(storage.get_action_proposal(payload["proposal_id"])[0].proposed_value, "high")
        finally:
            client.close()

        temporary, client, csrf, storage = self._client(field="assignee_ids")
        self.addCleanup(temporary.cleanup)
        try:
            response = self._execute(client, csrf, body={
                "decision": "modify_and_execute", "modified_assignee_ids": ["dev-3"],
                "reason": "Responsable confirmado",
            })
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["execution"]["status"], "SIMULATED")
            self.assertEqual(storage.get_action_proposal(payload["proposal_id"])[0].proposed_value, ["dev-3"])
        finally:
            client.close()

    def test_reject_is_a_real_human_decision_without_provider_calls(self):
        temporary, client, csrf, storage = self._client(
            source_mode="real", external_writes_enabled=True,
        )
        self.addCleanup(temporary.cleanup)
        with patch("requests.get") as mock_get, patch("requests.put") as mock_put:
            response = self._execute(client, csrf, body={
                "decision": "reject", "reason": "La evidencia requiere corrección",
            })
        try:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["execution_mode"], "sandbox")
            self.assertEqual(response.json()["execution"]["status"], "DENIED")
            self.assertEqual(storage.get_proposal_review_state(self.PROJECT, "proposal-1")[2].review_status,
                             "rejected")
            mock_get.assert_not_called()
            mock_put.assert_not_called()
        finally:
            client.close()

    def test_body_cannot_supply_actor_or_grant(self):
        temporary, client, csrf, _storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            response = self._execute(client, csrf, body={
                "decision": "approve_and_execute", "reason": "Revisión humana",
                "actor_id": "forged", "grant_token": "forged",
            })
            self.assertEqual(response.status_code, 422)
        finally:
            client.close()

    def test_live_trello_board_binding_fails_closed_before_write_and_preserves_public_state(self):
        for board_value in (None, "", "another-board"):
            with self.subTest(id_board=board_value):
                temporary, client, csrf, _storage = self._client(
                    source_mode="real", external_writes_enabled=True,
                )
                self.addCleanup(temporary.cleanup)
                live_card = {"due": "2026-10-14T17:00:00Z"}
                before_state = dict(live_card)
                tasks_before = client.get(
                    f"/api/v1/projects/{self.PROJECT}/tasks"
                )
                self.assertEqual(tasks_before.status_code, 200, tasks_before.text)

                def read_card():
                    payload = {
                        "due": live_card["due"], "start": None,
                        "idMembers": [], "idLabels": [],
                    }
                    if board_value is not None:
                        payload["idBoard"] = board_value
                    return payload

                get_response = MagicMock(status_code=200)
                get_response.json.side_effect = read_card

                def mutate_card(*, json, **_kwargs):
                    live_card.update(json)
                    return MagicMock(status_code=200)

                try:
                    with patch("requests.get", return_value=get_response), \
                         patch("requests.put", side_effect=mutate_card) as mock_put:
                        response = self._execute(client, csrf, key=f"board-check-{len(str(board_value))}")
                        tasks_after = client.get(
                            f"/api/v1/projects/{self.PROJECT}/tasks"
                        )

                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["execution"]["status"], "FAILED")
                    self.assertEqual(live_card, before_state)
                    self.assertEqual(tasks_after.status_code, 200, tasks_after.text)
                    self.assertEqual(tasks_after.json(), tasks_before.json())
                    mock_put.assert_not_called()
                finally:
                    client.close()

    def test_live_priority_execution_puts_mapped_id_label_and_verifies_readback(self):
        temporary, client, csrf, _storage = self._client(
            source_mode="real", external_writes_enabled=True,
            priority_labels=self.LABELS, field="priority",
        )
        self.addCleanup(temporary.cleanup)
        before = MagicMock(status_code=200)
        before.json.return_value = {"idBoard": self.PROJECT, "idLabels": ["other-label", "label-medium"]}
        for_write = MagicMock(status_code=200)
        for_write.json.return_value = {"idBoard": self.PROJECT, "idLabels": ["other-label", "label-medium"]}
        after = MagicMock(status_code=200)
        after.json.return_value = {"idBoard": self.PROJECT, "idLabels": ["other-label", "label-high"]}
        with patch("requests.get", side_effect=[before, for_write, after]) as mock_get, \
             patch("requests.put", return_value=MagicMock(status_code=200)) as mock_put:
            response = self._execute(client, csrf)
        try:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["execution_mode"], "live")
            self.assertEqual(response.json()["execution"]["status"], "EXECUTED")
            self.assertEqual(response.json()["execution"]["verification_status"], "verified")
            self.assertEqual(mock_get.call_count, 3)
            self.assertEqual(mock_put.call_args.kwargs["json"], {"idLabels": ["other-label", "label-high"]})
        finally:
            client.close()

    def test_missing_priority_map_is_rejected_before_any_provider_mutation(self):
        temporary, client, csrf, storage = self._client(field="priority")
        self.addCleanup(temporary.cleanup)
        try:
            with patch("requests.put") as mock_put:
                response = self._execute(client, csrf)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "mapa_prioridad_requerido")
            mock_put.assert_not_called()
            self.assertEqual(storage.get_proposal_review_state(self.PROJECT, "proposal-1")[2].version, 0)
        finally:
            client.close()

    def test_live_put_failure_is_not_reported_as_sandbox_success(self):
        temporary, client, csrf, storage = self._client(
            source_mode="real", external_writes_enabled=True,
        )
        self.addCleanup(temporary.cleanup)
        before = MagicMock(status_code=200)
        before.json.return_value = {
            "idBoard": self.PROJECT, "due": "2026-10-14T17:00:00Z",
            "start": None, "idMembers": [], "idLabels": [],
        }
        with patch("requests.get", return_value=before), \
             patch("requests.put", return_value=MagicMock(status_code=500)) as mock_put:
            response = self._execute(client, csrf)
            client.app.state.settings = client.app.state.settings.model_copy(update={
                "external_writes_enabled": False, "trello_api_key": None, "trello_token": None,
            })
            replay = self._execute(client, csrf)
            original, evidence = storage.get_action_proposal("proposal-1")
            storage.save_action_proposal(original.model_copy(update={
                "proposal_id": "proposal-2", "proposed_value": datetime(2026, 10, 22, 17, tzinfo=timezone.utc),
            }), evidence)
            concurrent = client.post(
                f"/api/v1/projects/{self.PROJECT}/proposals/proposal-2/execute",
                json={"decision": "approve_and_execute", "reason": "Revisión humana"},
                headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": csrf,
                         "Idempotency-Key": "idem-2"},
            )
        try:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["execution_mode"], "live")
            self.assertEqual(response.json()["execution"]["status"], "FAILED")
            self.assertEqual(response.json()["execution"]["verification_status"], "unknown")
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(replay.json()["execution_mode"], "live")
            self.assertEqual(concurrent.status_code, 409)
            self.assertEqual(concurrent.json()["error"]["code"], "recurso_de_tarea_incierto")
            self.assertEqual(mock_put.call_count, 1)
        finally:
            client.close()

    def test_execute_preserves_origin_csrf_and_project_scope_guards(self):
        temporary, client, csrf, storage = self._client()
        self.addCleanup(temporary.cleanup)
        path = f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1/execute"
        body = {"decision": "approve_and_execute", "reason": "Revisión humana"}
        try:
            wrong_origin = client.post(
                path, json=body,
                headers={"Origin": "https://attacker.invalid", "X-CSRF-Token": csrf,
                         "Idempotency-Key": "wrong-origin-01"},
            )
            self.assertEqual(wrong_origin.status_code, 403)
            self.assertEqual(wrong_origin.json()["error"]["code"], "origen_no_permitido")
            self.assertEqual(len(storage.list_proposal_executions(self.PROJECT)), 0)
        finally:
            client.close()

        temporary, client, csrf, storage = self._client(operator_project_ids=("other-project",))
        self.addCleanup(temporary.cleanup)
        try:
            outside_scope = self._execute(client, csrf)
            self.assertEqual(outside_scope.status_code, 403)
            self.assertEqual(outside_scope.json()["error"]["code"], "proyecto_fuera_de_sesion")
            self.assertEqual(len(storage.list_proposal_executions(self.PROJECT)), 0)
        finally:
            client.close()

    def test_csrf_and_idempotency_header_are_mandatory(self):
        temporary, client, csrf, _storage = self._client()
        self.addCleanup(temporary.cleanup)
        try:
            without_csrf = client.post(
                f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1/execute",
                json={"decision": "approve_and_execute", "reason": "Revisión humana"},
                headers={"Origin": "http://127.0.0.1:5173", "Idempotency-Key": "idem-1"},
            )
            self.assertEqual(without_csrf.status_code, 403)
            without_key = client.post(
                f"/api/v1/projects/{self.PROJECT}/proposals/proposal-1/execute",
                json={"decision": "approve_and_execute", "reason": "Revisión humana"},
                headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": csrf},
            )
            self.assertEqual(without_key.status_code, 422)
        finally:
            client.close()


class ProposalExecutionSchemaMigrationTests(unittest.TestCase):
    def test_reason_columns_are_added_without_replacing_existing_append_only_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "pre-reason.sqlite")
            with sqlite3.connect(database) as connection:
                connection.executescript("""
                    CREATE TABLE proposal_executions (
                        execution_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                        project_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                        status TEXT NOT NULL, verification_status TEXT NOT NULL,
                        actor_id TEXT, field TEXT, recorded_at TEXT NOT NULL,
                        request_json TEXT NOT NULL, result_json TEXT NOT NULL
                    );
                    CREATE TABLE security_audit_log (
                        event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                        actor_id TEXT NOT NULL, role TEXT NOT NULL,
                        permission_checked TEXT NOT NULL, resource_id TEXT,
                        decision TEXT NOT NULL, reason TEXT NOT NULL,
                        token_fingerprint TEXT NOT NULL, recorded_at TEXT NOT NULL
                    );
                    INSERT INTO proposal_executions VALUES
                        ('old-execution', 'old-request', 'board-1', 'proposal-1',
                         'FAILED', 'unknown', 'operator', 'due_date', '2026-10-12T00:00:00+00:00', '{}', '{}');
                    INSERT INTO security_audit_log VALUES
                        ('old-event', 'board-1', 'operator', 'local_operator',
                         'web:proposal_execute', 'proposal-1', 'denied', 'request_sha256=old',
                         'sha256:0123456789abcdef', '2026-10-12T00:00:00+00:00');
                """)

            SQLiteStorageAdapter(database)
            storage = SQLiteStorageAdapter(database)
            connection = storage._get_connection()
            try:
                execution_columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(proposal_executions)")}
                audit_columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(security_audit_log)")}
                execution = connection.execute(
                    "SELECT request_id FROM proposal_executions WHERE execution_id='old-execution'",
                ).fetchone()
                audit = connection.execute(
                    "SELECT reason FROM security_audit_log WHERE event_id='old-event'",
                ).fetchone()
                triggers = {row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'")}
            finally:
                connection.close()

            self.assertIn("human_reason", execution_columns)
            self.assertIn("human_reason", audit_columns)
            self.assertEqual(execution["request_id"], "old-request")
            self.assertEqual(audit["reason"], "request_sha256=old")
            self.assertIn("proposal_executions_no_UPDATE", triggers)
            self.assertIn("security_audit_log_no_DELETE", triggers)


if __name__ == "__main__":
    unittest.main()
