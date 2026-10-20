"""Provider-aware verifier budgets preserve whole evidence and explicit local limits."""

import hashlib
import unittest

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.patch_review import PatchCandidate, effective_patch


class RecordingVerifierProvider(FakeProvider):
    """Offline diagnostic verdict, explicitly not a real model response."""

    def __init__(self, context_tokens=128000):
        super().__init__(context_tokens=context_tokens)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.schema_name == "patch_review_output_v1":
            payload = {"reviews": [{
                "finding_id": "C1", "verdict": "insufficient",
                "reason": "Deterministic budget test, not a real patch review.",
                "evidence_ids": ["a" * 40],
            }]}
        else:
            payload = {"verifications": [{
                "finding_id": "C1", "verdict": "insufficient",
                "reason": "Deterministic budget test, not a real defect verdict.",
                "evidence_ids": ["a" * 40],
            }]}
        return GenerationResult(
            structured_data=payload, status=GenerationStatus.complete,
            provider="fake_simulated", model=self.model, duration_seconds=0,
        )


class VerifierContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.content = "def parse(value):\n    return value\n" + "# surrounding source context\n" * 600
        self.sha = "a" * 40
        self.source = CodeDiffSnippet(
            repository="acme/backend", commit_sha=self.sha, file_path="src/sample.py",
            lines=list(range(1, len(self.content.splitlines()) + 1)),
            content=self.content, evidence_scope="full_file", is_complete=True,
            total_lines=len(self.content.splitlines()),
            source_sha256=hashlib.sha256(self.content.encode()).hexdigest(),
        )
        self.finding = CandidateFinding(
            id="C1", file_path=self.source.file_path, line=2, commit_sha=self.sha,
            claim="A diagnostic claim requiring independent review.",
            evidence="    return value", evidence_ids=[self.sha], confidence=0.9,
        )

    def test_complete_source_above_generic_default_reaches_configured_provider(self):
        provider = RecordingVerifierProvider()
        results = VerifierAgent(provider).run("P", [self.finding], [self.source], evidence_ids=[self.sha])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].verdict.value, "insufficient")
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.evidences["code_snippets"][0]["content"], self.content)
        self.assertGreater(request.evidences["context_metadata"]["budget_tokens"], 4096)
        self.assertFalse(request.evidences["context_metadata"]["is_truncated"])

    def test_explicit_local_budget_still_refuses_before_any_provider_call(self):
        provider = RecordingVerifierProvider()
        agent = VerifierAgent(provider, context_builder=ContextBuilder(default_budget_tokens=4096))
        with self.assertRaises(ContextBudgetExceededError):
            agent.run("P", [self.finding], [self.source], evidence_ids=[self.sha])
        self.assertEqual(provider.requests, [])

    def test_small_provider_window_still_refuses_instead_of_truncating(self):
        provider = RecordingVerifierProvider(context_tokens=4096)
        with self.assertRaises(ContextBudgetExceededError):
            VerifierAgent(provider).run("P", [self.finding], [self.source], evidence_ids=[self.sha])
        self.assertEqual(provider.requests, [])

    def test_patch_review_uses_provider_budget_without_changing_patch_bytes(self):
        provider = RecordingVerifierProvider()
        corrected = self.content.replace("return value", "return value.strip()", 1)
        _, patch_hash = effective_patch(self.content, self.content, corrected)
        patch = PatchCandidate(
            finding_id="C1", repository="acme/backend", commit_sha=self.sha,
            file_path=self.source.file_path, original_sha256=self.source.source_sha256,
            patch_sha256=patch_hash, original_excerpt=self.content, suggested_code=corrected,
        )
        outcome = VerifierAgent(provider).review_patch(
            project_id="P", finding=self.finding, patch=patch,
            patched_region=corrected, evidence_ids=[self.sha],
        )
        self.assertEqual(outcome.verdict.value, "insufficient")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].evidences["patch"]["suggested_code"], corrected)


if __name__ == "__main__":
    unittest.main()
