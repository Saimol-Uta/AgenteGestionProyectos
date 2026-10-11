"""Offline capture-bound proposal integration, without authorization or execution."""
import json
import hashlib
import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import test_orchestrator, test_monitoring_proposals
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.run import RunStatus, compute_sha256
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.action_proposal import validate_action_proposal


class OrchestratorProposalsTests(unittest.TestCase):
    def setUp(self):
        self.base = test_orchestrator.OrchestratorTestCase()
        self.base.setUp()
        self.addCleanup(self.base.tearDown)
        fixture = test_monitoring_proposals.MonitoringProposalsTests()
        fixture.setUp()
        self.snapshot = self.base._sample_snapshot_no_code()
        self.snapshot.project_id = "P"
        self.snapshot.tasks = [fixture.task]
        self.evidence = [fixture.evidence]
        self.rules = DomainProposalRuleSet.model_validate(fixture.rules)
        self.collection = SimpleNamespace(snapshot=self.snapshot, evidence=self.evidence,
                                          target_commit_sha=None, records=[])
        self.source = SimpleNamespace(collect_snapshot=Mock(return_value=self.collection))
        self.store = self.base.sqlite
        self.orch = ProjectOrchestrator(self.store, self.base.artifacts,
            self.base.fake_provider, self.base.fake_provider,
            aprobador_provider=self.base.fake_provider, source_adapter=self.source,
            config=OrchestratorConfig(project_id="P"))

    def execute(self, **kwargs):
        self.base.fake_provider.enqueue_responses([GenerationResult(
            status=GenerationStatus.complete, structured_data=dict(summary="Weekly assessment.",
                referenced_task_ids=["T"], evidence_ids=["T"], selected_choice_ids=["choice-0"]),
            provider="fake_simulated", model="fake", duration_seconds=0.01)])
        return self.orch.execute(proposal_rules=self.rules, **kwargs)

    def summary(self, run):
        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertIsNotNone(self.store.get_report(run.report_id))
        return json.loads(run.stages["proposals"].output_summary)

    def test_persist_exact_capture_and_cache_and_rule_change(self):
        original_save = self.store.save_snapshot_with_capture_receipt
        def raw_receipt(snapshot, **kwargs):
            raw = json.dumps(snapshot.model_dump(mode="json"), indent=2)
            with patch.object(type(snapshot), "model_dump_json", return_value=raw):
                return original_save(snapshot, **kwargs)
        with patch.object(self.store, "save_snapshot_with_capture_receipt", side_effect=raw_receipt):
            run = self.execute()
        self.assertEqual(self.summary(run)["saved"], 1)
        proposal, evidence = self.store.list_action_proposals("P")[0]
        capture = self.store.get_snapshot_capture(proposal.observed_capture_id)
        self.assertEqual(validate_action_proposal(proposal, capture, evidence), [])
        self.assertNotEqual(capture.capture_id, compute_sha256(self.snapshot))
        self.assertEqual(capture.snapshot, self.snapshot)
        with closing(sqlite3.connect(self.base.db_path)) as conn:
            raw = conn.execute("SELECT data_json FROM snapshot_captures WHERE capture_id = ?",
                               (capture.capture_id,)).fetchone()[0]
        self.assertEqual(capture.capture_id, hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual((proposal.review_status, proposal.authorization_status, proposal.execution_status),
                         ("pending", "not_authorized", "not_executed"))
        self.assertEqual(self.orch.execute(proposal_rules=self.rules).run_id, run.run_id)
        self.rules.rules[0].rationale = "Changed owner reason"
        changed = self.execute()
        self.assertNotEqual(changed.config_hash, run.config_hash)
        self.assertEqual(len(self.store.list_action_proposals("P")), 2)

    def test_legacy_hash_cache_and_no_fake_activity(self):
        with patch.object(self.base.fake_provider, "generate",
                          wraps=self.base.fake_provider.generate) as generate:
            run = self.orch.execute(snapshot=self.snapshot)
        self.assertEqual([c.args[0].role for c in generate.call_args_list], ["elaborador", "aprobador"])
        self.assertEqual(run.config_hash, self.orch.config.to_hash())
        self.assertEqual(self.orch.execute(snapshot=self.snapshot).run_id, run.run_id)
        self.assertEqual(self.store.list_action_proposals("P"), [])
        self.assertEqual(self.summary(run)["saved"], 0)

    def test_receipt_unavailable_once_and_arbitrary_error_no_fallback(self):
        for capability in (None, Mock(side_effect=NotImplementedError)):
            with self.subTest(capability=capability), patch.object(self.store,
                    "save_snapshot_with_capture_receipt", capability), patch.object(self.store,
                    "save_snapshot", wraps=self.store.save_snapshot) as legacy:
                run = self.execute()
                self.assertIn("capture_unavailable", self.summary(run)["limitations"])
                legacy.assert_called_once()
                run.status = RunStatus.failed
                self.store.save_run(run)
        with patch.object(self.store, "save_snapshot_with_capture_receipt",
                side_effect=RuntimeError("private failure")), patch.object(self.store, "save_snapshot") as legacy:
            with self.assertRaises(RuntimeError):
                self.execute()
            legacy.assert_not_called()

    def test_evidence_missing_foreign_and_stale_collection(self):
        for mode in ("missing", "foreign", "stale"):
            with self.subTest(mode=mode):
                if mode == "missing":
                    self.collection.evidence = []
                elif mode == "foreign":
                    self.collection.evidence = self.evidence
                    self.evidence[0].provenance.project_id = "other"
                else:
                    self.orch._last_collection_result = self.collection
                run = self.execute(**({"snapshot": self.snapshot} if mode == "stale" else {}))
                self.assertEqual(self.summary(run)["saved"], 0)
                run.status = RunStatus.failed
                self.store.save_run(run)

    def test_evidence_change_and_rule_normalization(self):
        run = self.execute()
        self.rules.rules[0].allowed_values.reverse()
        self.assertEqual(self.orch.execute(proposal_rules=self.rules).run_id, run.run_id)
        self.evidence[0].summary = "Updated observation"
        changed = self.execute()
        self.assertNotEqual(changed.config_hash, run.config_hash)
        self.assertEqual(len(self.store.list_action_proposals("P")), 2)

    def test_forged_draft_cannot_override_canonical_owner_or_states(self):
        from agente_gestion.agents.context import monitoring_choices
        from agente_gestion.domain.report import MonitoringDraft
        choices, _ = monitoring_choices("P", self.snapshot.tasks,
            [t.model_dump(mode="json") for t in self.snapshot.tasks], self.rules, self.evidence)
        for mutation in ({"rationale": "model owner"}, {"current_value": "completada"},
                         {"authorization_status": "authorized"}):
            forged = choices[0].model_copy(update=mutation)
            with patch("agente_gestion.application.orchestrator.ProjectMonitoringAgent.run",
                       return_value=MonitoringDraft(summary="Weekly assessment.", requested_changes=[forged])):
                run = self.orch.execute(proposal_rules=self.rules)
            self.assertEqual(self.summary(run)["failures"][0]["codes"], ["invalid_choice"])
            self.assertEqual(self.store.list_action_proposals("P"), [])
            run.status = RunStatus.failed
            self.store.save_run(run)

    def test_agent_cannot_mutate_authoritative_rules_or_evidence(self):
        from agente_gestion.agents.context import monitoring_choices
        from agente_gestion.domain.report import MonitoringDraft
        def mutate(**kwargs):
            kwargs["proposal_rules"].rules[0].rationale = "Injected owner"
            kwargs["task_evidence"][0].summary = "Injected evidence"
            choices, _ = monitoring_choices("P", self.snapshot.tasks,
                [t.model_dump(mode="json") for t in self.snapshot.tasks],
                kwargs["proposal_rules"], kwargs["task_evidence"])
            return MonitoringDraft(summary="Weekly assessment.", requested_changes=choices[:1])
        with patch("agente_gestion.application.orchestrator.ProjectMonitoringAgent.run", side_effect=mutate):
            run = self.orch.execute(proposal_rules=self.rules)
        self.assertEqual(self.summary(run)["failures"][0]["codes"], ["invalid_choice"])
        self.assertEqual(self.rules.rules[0].rationale, "Owner reason")
        self.assertEqual(self.evidence[0].summary, "Observed")

    def test_actual_catalogue_conflict_preserves_frozen_submission(self):
        run = self.execute()
        proposal, evidence = self.store.list_action_proposals("P")[0]
        changed = proposal.model_copy(update={"rationale": "Different body"})
        with self.assertRaises(ValueError):
            self.store.save_action_proposal(changed, evidence)
        self.assertEqual(self.store.get_action_proposal(proposal.proposal_id), (proposal, evidence))
        run.status = RunStatus.failed
        self.store.save_run(run)
        self.evidence[0].provenance.payload["state"] = "completada"
        self.assertEqual(self.summary(self.execute())["saved"], 0)

    def test_partial_retry_identical_frozen_body_and_catalogue_failure(self):
        run = self.execute()
        first = self.store.list_action_proposals("P")
        run.status = RunStatus.failed
        self.store.save_run(run)
        retried = self.execute()
        self.assertNotEqual(retried.run_id, run.run_id)
        self.assertEqual(self.summary(retried)["saved"], 1)
        self.assertEqual(self.store.list_action_proposals("P"), first)
        for error, code in [(NotImplementedError(), "catalogue_unavailable"),
                            (ValueError("private conflict"), "catalogue_conflict")]:
            retried.status = RunStatus.failed
            self.store.save_run(retried)
            with patch.object(self.store, "save_action_proposal", side_effect=error):
                retried = self.execute()
            summary = self.summary(retried)
            self.assertEqual(summary["saved"], 0)
            self.assertIn(code, summary["failures"][0]["codes"])
            self.assertNotIn("private", json.dumps(summary))
