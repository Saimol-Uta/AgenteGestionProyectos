"""G2: revision independiente del parche exacto y puerta de aprobacion.

El mecanismo no relaja la proteccion: la aprobacion exige una revision
favorable y vigente, vinculada al parche exacto que se aplicaria. Un defecto
confirmado, una sintaxis valida o unas pruebas aprobadas no la sustituyen, y
cualquier cambio en el parche, el archivo, el repositorio o el commit deja la
revision sin efecto.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.application.finding_decisions import (
    APPROVAL_REVIEW_REQUIRED_CODE,
    FindingDecisionError,
    FindingDecisionService,
)
from agente_gestion.application.patch_review import (
    PatchReviewService,
    favorable_patch_review,
    latest_patch_review,
)
from agente_gestion.domain.code_action import CodeSourceSnapshot, FindingActionOrigin
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.patch_review import (
    PatchReviewOutcome,
    PatchReviewVerdict,
    effective_patch,
)


PROJECT_ID = "PROJ"
RUN_ID = "run-1"
FINDING_ID = "backend::abc123::CQ-001"
REPOSITORY = "acme/backend"
COMMIT_SHA = "a" * 40
FILE_PATH = "src/infrastructure/database/despacho-store.ts"
BASE_BRANCH = "develop"
EXCERPT = "const total = rows.length;\n"
SUGGESTED = "const total = rows.length + 1;\n"
SOURCE_CONTENT = "export function run() {\n" + EXCERPT + "  return total;\n}\n"


class _StubVerifier:
    """Returns a prepared verdict without calling any model."""

    def __init__(self, verdict: PatchReviewVerdict = PatchReviewVerdict.favorable,
                 reason: str = "La correccion resuelve el defecto sin alterar el contexto."):
        self.verdict = verdict
        self.reason = reason
        self.calls: list[dict[str, Any]] = []

    def review_patch(self, *, project_id, finding, patch, patched_region, evidence_ids=None, **kwargs):
        self.calls.append({
            "project_id": project_id, "finding_id": finding.id, "patch": patch,
            "patched_region": patched_region, "evidence_ids": list(evidence_ids or []),
        })
        return PatchReviewOutcome(
            verdict=self.verdict, reason=self.reason, evidence_ids=list(evidence_ids or []),
        )


class _RecordingCodeActionPort:
    """Records every branch creation so a refusal can be proven inert."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create_verification_branch(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("The action port must not be reached without a favorable review")


class _ReviewProvider(FakeProvider):
    """Simulated provider that returns one patch review."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__()
        self.payload = payload

    def generate(self, request):
        return GenerationResult(
            status=GenerationStatus.complete, structured_data=self.payload,
            model=self.model, provider=self.provider_name, duration_seconds=0.0,
        )


def _candidate() -> CandidateFinding:
    return CandidateFinding(
        id=FINDING_ID, rule_id=None, category="otros", file_path=FILE_PATH, line=2,
        commit_sha=COMMIT_SHA, evidence=EXCERPT, suggested_fix="corregir el total",
        evidence_ids=["ev-1"], suggested_code=SUGGESTED,
    )


def _source() -> CodeSourceSnapshot:
    return CodeSourceSnapshot(
        content=SOURCE_CONTENT, is_complete=True, repository=REPOSITORY,
        commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        source_sha256=hashlib.sha256(SOURCE_CONTENT.encode("utf-8")).hexdigest(),
        total_lines=len(SOURCE_CONTENT.splitlines()),
    )


class PatchReviewMechanismTests(unittest.TestCase):
    """The review is recorded, bound to the exact patch, and gates the approval."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.storage = SQLiteStorageAdapter(db_path=str(self.root / "agente.db"))
        self.candidate = _candidate()
        self.source = _source()
        self.patch_sha256 = effective_patch(SOURCE_CONTENT, EXCERPT, SUGGESTED)[1]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _review(self, verifier: _StubVerifier | None = None) -> Any:
        service = PatchReviewService(self.storage, verifier or _StubVerifier())
        return service.review(
            project_id=PROJECT_ID, run_id=RUN_ID, finding=self.candidate, source=self.source,
            original_excerpt=EXCERPT, suggested_code=SUGGESTED, repository=REPOSITORY,
            commit_sha=COMMIT_SHA, file_path=FILE_PATH, evidence_ids=["ev-1"],
        )

    def test_the_review_records_the_verdict_and_the_digest_of_the_exact_patch(self):
        review = self._review()
        self.assertEqual(review.verdict, PatchReviewVerdict.favorable)
        self.assertEqual(review.patch_sha256, self.patch_sha256)
        self.assertEqual(review.original_sha256, hashlib.sha256(SOURCE_CONTENT.encode()).hexdigest())
        self.assertEqual(review.finding_id, FINDING_ID)
        self.assertEqual(review.repository, REPOSITORY)
        self.assertEqual(review.commit_sha, COMMIT_SHA)
        self.assertEqual(review.file_path, FILE_PATH)
        self.assertEqual(review.reviewed_by, "verifier_agent")

        stored = latest_patch_review(self.storage, PROJECT_ID, FINDING_ID)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.patch_sha256, self.patch_sha256)

    def test_the_review_is_bound_to_the_patch_and_not_to_the_defect(self):
        self._review()
        identity = {
            "project_id": PROJECT_ID, "finding_id": FINDING_ID,
            "patch_sha256": self.patch_sha256, "repository": REPOSITORY,
            "commit_sha": COMMIT_SHA, "file_path": FILE_PATH,
        }
        for changed in (
            {"patch_sha256": "b" * 64},
            {"file_path": "src/otro.ts"},
            {"repository": "acme/otro"},
            {"commit_sha": "b" * 40},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(favorable_patch_review(self.storage, **{**identity, **changed}))

    def test_an_unfavorable_or_insufficient_review_does_not_authorize(self):
        for verdict in (PatchReviewVerdict.unfavorable, PatchReviewVerdict.insufficient):
            with self.subTest(verdict=verdict):
                self._review(_StubVerifier(verdict=verdict, reason="La correccion no resuelve el defecto."))
                self.assertIsNone(favorable_patch_review(
                    self.storage, project_id=PROJECT_ID, finding_id=FINDING_ID,
                    patch_sha256=self.patch_sha256, repository=REPOSITORY,
                    commit_sha=COMMIT_SHA, file_path=FILE_PATH,
                ))

    def test_a_later_unfavorable_review_withdraws_the_favorable_one(self):
        self._review()
        self.assertIsNotNone(favorable_patch_review(
            self.storage, project_id=PROJECT_ID, finding_id=FINDING_ID,
            patch_sha256=self.patch_sha256, repository=REPOSITORY,
            commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        ))
        self._review(_StubVerifier(verdict=PatchReviewVerdict.unfavorable, reason="Revisión posterior."))
        self.assertIsNone(favorable_patch_review(
            self.storage, project_id=PROJECT_ID, finding_id=FINDING_ID,
            patch_sha256=self.patch_sha256, repository=REPOSITORY,
            commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        ))

    def test_the_reviews_are_append_only(self):
        self._review()
        connection = self.storage._get_connection()
        try:
            with self.assertRaises(Exception):
                connection.execute("UPDATE patch_reviews SET verdict='favorable'")
        finally:
            connection.close()

    def test_the_approval_is_refused_without_a_favorable_review(self):
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        with self.assertRaises(FindingDecisionError) as context:
            service.decide(
                project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
                candidate=self.candidate, decision="approve", idempotency_key="idem-approve-1",
                actor_id="gestor", session_cookie=None, original_excerpt=EXCERPT,
                source=self.source, repository=REPOSITORY, base_branch=BASE_BRANCH,
            )
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(port.calls, [])

    def test_the_approval_is_refused_when_the_patch_changes(self):
        self._review()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        with self.assertRaises(FindingDecisionError) as context:
            service.decide(
                project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
                candidate=self.candidate, decision="approve", idempotency_key="idem-approve-2",
                actor_id="gestor", session_cookie=None, original_excerpt=EXCERPT,
                source=self.source, repository=REPOSITORY, base_branch=BASE_BRANCH,
                suggested_code_override="const total = rows.length + 2;\n",
            )
        self.assertEqual(context.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(port.calls, [])

    def test_a_confirmed_defect_is_not_a_review_of_the_correction(self):
        """The defect verification and the patch review are two different things."""
        verifier = _StubVerifier()
        self._review(verifier)
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(verifier.calls[0]["patch"].patch_sha256, self.patch_sha256)
        self.assertIn("const total = rows.length + 1;", verifier.calls[0]["patched_region"])


class PatchReviewAgentTests(unittest.TestCase):
    """The independent verifier judges the correction with its own output."""

    def _agent(self, payload: dict[str, Any]) -> VerifierAgent:
        return VerifierAgent(provider=_ReviewProvider(payload), model="simulated")

    def _candidate(self) -> CandidateFinding:
        return _candidate()

    def test_the_agent_returns_the_independent_verdict(self):
        agent = self._agent({"reviews": [{
            "finding_id": FINDING_ID, "verdict": "favorable",
            "reason": "La correccion corrige el total sin alterar el resto.",
            "evidence_ids": ["ev-1"],
        }]})
        candidate = self._candidate()
        _, patch_sha256 = effective_patch(SOURCE_CONTENT, EXCERPT, SUGGESTED)
        from agente_gestion.domain.patch_review import PatchCandidate
        patch = PatchCandidate(
            finding_id=FINDING_ID, repository=REPOSITORY, commit_sha=COMMIT_SHA,
            file_path=FILE_PATH,
            original_sha256=hashlib.sha256(SOURCE_CONTENT.encode()).hexdigest(),
            patch_sha256=patch_sha256, original_excerpt=EXCERPT, suggested_code=SUGGESTED,
        )
        outcome = agent.review_patch(
            project_id=PROJECT_ID, finding=candidate, patch=patch,
            patched_region=SOURCE_CONTENT, evidence_ids=["ev-1"],
        )
        self.assertEqual(outcome.verdict, PatchReviewVerdict.favorable)
        self.assertIn("corrige el total", outcome.reason)

    def test_the_agent_falls_back_to_insufficient_without_a_usable_verdict(self):
        agent = self._agent({"reviews": []})
        candidate = self._candidate()
        from agente_gestion.domain.patch_review import PatchCandidate
        patch = PatchCandidate(
            finding_id=FINDING_ID, repository=REPOSITORY, commit_sha=COMMIT_SHA,
            file_path=FILE_PATH,
            original_sha256=hashlib.sha256(SOURCE_CONTENT.encode()).hexdigest(),
            patch_sha256="c" * 64, original_excerpt=EXCERPT, suggested_code=SUGGESTED,
        )
        outcome = agent.review_patch(
            project_id=PROJECT_ID, finding=candidate, patch=patch,
            patched_region=SOURCE_CONTENT, evidence_ids=[],
        )
        self.assertEqual(outcome.verdict, PatchReviewVerdict.insufficient)

    def test_a_review_of_another_finding_fails_closed(self):
        """A malformed review is refused instead of authorizing anything."""
        from agente_gestion.application.generation import ValidationError
        agent = self._agent({"reviews": [{
            "finding_id": "otro-hallazgo", "verdict": "favorable", "reason": "No corresponde.",
        }]})
        candidate = self._candidate()
        from agente_gestion.domain.patch_review import PatchCandidate
        patch = PatchCandidate(
            finding_id=FINDING_ID, repository=REPOSITORY, commit_sha=COMMIT_SHA,
            file_path=FILE_PATH,
            original_sha256=hashlib.sha256(SOURCE_CONTENT.encode()).hexdigest(),
            patch_sha256="c" * 64, original_excerpt=EXCERPT, suggested_code=SUGGESTED,
        )
        with self.assertRaises(ValidationError):
            agent.review_patch(
                project_id=PROJECT_ID, finding=candidate, patch=patch,
                patched_region=SOURCE_CONTENT, evidence_ids=[],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
