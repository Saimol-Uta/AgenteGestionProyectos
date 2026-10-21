"""Seed the existing full pipeline with a scoped rule and deterministic provider."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from tests.test_corrective_comments import setup_comment, TEXT, CARD


def seed(root):
    root = Path(root)
    storage = SQLiteStorageAdapter(str(root / "comments.sqlite"))
    card, capture, evidence, _ = setup_comment(storage)
    rules = DomainProposalRuleSet.model_validate_json(
        (Path(__file__).resolve().parents[1] / "config/rules/hormigestion-dashboard-validation.json").read_text())
    assert rules.rules[0].allowed_values == [TEXT]
    fake = FakeProvider()
    fake.enqueue_responses([GenerationResult(status=GenerationStatus.complete, provider="fake_simulated",
        model="offline-comment-sandbox", duration_seconds=0,
        structured_data={"summary": f"Seguimiento de validación de {CARD}.", "referenced_task_ids": [CARD],
                         "evidence_ids": [CARD], "selected_choice_ids": ["choice-0"]})])
    source = SimpleNamespace(collect_snapshot=Mock(return_value=SimpleNamespace(
        snapshot=capture.snapshot, evidence=evidence[:1], target_commit_sha=None, records=[])))
    orchestrator = ProjectOrchestrator(storage, ArtifactStorageAdapter(str(root / "runs")), fake, fake,
        aprobador_provider=fake, source_adapter=source, config=OrchestratorConfig(project_id="HORMIGESTION"))
    run = orchestrator.execute(proposal_rules=rules)
    diagnostic = json.loads(run.stages["proposals"].output_summary)
    assert diagnostic["saved"] == 1 and diagnostic["technical_ready"] == 1, diagnostic
    proposal = storage.list_action_proposals("HORMIGESTION")[0][0]
    return storage, proposal, diagnostic
