"""Tests for VerifierAgent full_file validation, integrity, escalation and language support (Fase A)."""

import hashlib
import unittest
from unittest.mock import MagicMock
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.agents.verifier import (
    VerifierAgent,
    _claim_verification_preflight,
    _suggestion_verification_issue,
)
from agente_gestion.application.escalation import EscalationGate, EscalationPolicy
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.domain.code import CodeDiffSnippet, validate_source_syntax
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingCategory,
    FindingVerification,
    SuggestedCodeValidationStatus,
    VerificationVerdict,
)
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.repositories import RepositoryIdentity, RepositoryProvenance


class VerifierPhaseATestCase(unittest.TestCase):
    def setUp(self):
        self.commit_sha = "a" * 40
        self.repo_name = "test-owner/test-repo"
        self.repo_identity = RepositoryIdentity(owner="test-owner", repo="test-repo", id="backend-repo")
        self.provenance = RepositoryProvenance(
            repository_identity=self.repo_identity,
            commit_sha=self.commit_sha,
            source="github_code_review",
            certifies_target=True,
        )

        # Valid python source content
        self.py_content = (
            "def calculate_total(items):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        total += item.price\n"
            "    return total\n"
        )
        self.py_hash = hashlib.sha256(self.py_content.encode("utf-8")).hexdigest()
        self.py_lines = list(range(1, 6))

        # Full file snippet
        self.valid_full_file = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/service.py",
            diff_side="NEW",
            lines=self.py_lines,
            content=self.py_content,
            evidence_scope="full_file",
            is_complete=True,
            total_lines=5,
            source_sha256=self.py_hash,
            repository_identity=self.repo_identity,
            provenance=self.provenance,
        )

        # Candidate finding with patch in valid python file
        self.valid_finding = CandidateFinding(
            id="CQ-001",
            category=FindingCategory.manejo_recursos.value,
            file_path="src/service.py",
            line=4,
            commit_sha=self.commit_sha,
            claim="Item price could be negative or None",
            evidence="        total += item.price\n",
            suggested_code="        total += (item.price or 0)\n",
            improvement_summary="Safely handle null or missing item price",
            evidence_ids=[self.commit_sha],
            confidence=0.9,
            rule_id="Q3",
            repository_identity=self.repo_identity,
            provenance=self.provenance,
        )

        self.rule = QualityRule(
            id="Q3",
            description="Zero critical defects",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )

    # --------------------------------------------------------------------------
    # Prueba A1: Archivo completo válido
    # --------------------------------------------------------------------------
    def test_a1_valid_full_file_accepted_by_preflight_and_invokes_verifier(self):
        # 1. Preflight accepts
        issue = _suggestion_verification_issue(self.valid_finding, [self.valid_full_file])
        self.assertIsNone(issue, f"Preflight rejected valid full_file: {issue}")

        # 2. FakeProvider config
        fake_provider = FakeProvider()
        fake_provider.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "verifications": [
                        {
                            "finding_id": "CQ-001",
                            "verdict": "confirmed",
                            "reason": "Parche sintácticamente válido y seguro sobre el archivo completo.",
                            "evidence_ids": [self.commit_sha],
                        }
                    ]
                },
                provider="fake_gemini",
                model="gemini-3.5-flash-lite",
                duration_seconds=0.05,
            )
        ])
        fake_provider.generate = MagicMock(wraps=fake_provider.generate)

        agent = VerifierAgent(provider=fake_provider, model="gemini-3.5-flash-lite")
        verifications = agent.run(
            project_id="proj-test",
            findings=[self.valid_finding],
            snippets=[self.valid_full_file],
            rules=[self.rule],
            evidence_ids=[self.commit_sha],
        )

        # 3. Invoked effectively
        self.assertEqual(fake_provider.generate.call_count, 1)

        # 4. Result check
        self.assertEqual(len(verifications), 1)
        v = verifications[0]
        self.assertEqual(v.finding_id, "CQ-001")
        self.assertEqual(v.verdict, VerificationVerdict.confirmed)
        self.assertIn(self.commit_sha, v.evidence_ids)
        self.assertEqual(v.verified_by, "verifier_agent")

    # --------------------------------------------------------------------------
    # Prueba A2: Evidencia parcial y desacoplamiento de validación de código
    # --------------------------------------------------------------------------
    def test_a2_partial_snippet_covering_line_invokes_verifier_with_unvalidated_code(self):
        partial_snippet = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/service.py",
            diff_side="NEW",
            lines=[3, 4],
            content="    for item in items:\n        total += item.price\n",
            evidence_scope="snippet",  # Not full_file
            is_complete=False,
            total_lines=5,
        )

        # 1. Patch validation rejects because full_file is missing
        patch_issue = _suggestion_verification_issue(self.valid_finding, [partial_snippet])
        self.assertEqual(patch_issue, "Se requiere un único archivo completo para validar el código sugerido.")

        # 2. But claim preflight accepts because line 4 is covered
        claim_issue = _claim_verification_preflight(self.valid_finding, [partial_snippet])
        self.assertIsNone(claim_issue)

        # 3. VerifierAgent invokes LLM to verify claim
        fake_provider = FakeProvider()
        fake_provider.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "verifications": [
                        {
                            "finding_id": "CQ-001",
                            "verdict": "confirmed",
                            "reason": "Defecto confirmado en las líneas visibles.",
                            "evidence_ids": [self.commit_sha],
                        }
                    ]
                },
                provider="fake_gemini",
                model="gemini-3.5-flash-lite",
                duration_seconds=0.05,
            )
        ])
        fake_provider.generate = MagicMock(wraps=fake_provider.generate)
        agent = VerifierAgent(provider=fake_provider, model="gemini-3.5-flash-lite")
        verifications = agent.run(
            project_id="proj-test",
            findings=[self.valid_finding],
            snippets=[partial_snippet],
            rules=[self.rule],
            evidence_ids=[self.commit_sha],
        )

        self.assertEqual(fake_provider.generate.call_count, 1)
        self.assertEqual(len(verifications), 1)
        self.assertEqual(verifications[0].verdict, VerificationVerdict.confirmed)
        self.assertEqual(verifications[0].suggested_code_validation_status, SuggestedCodeValidationStatus.not_validated)

    def test_a2_partial_snippet_line_out_of_bounds_rejected_zero_llm_calls(self):
        out_of_bounds_snippet = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/service.py",
            diff_side="NEW",
            lines=[1, 2],  # Candidate line is 4
            content="def calculate_total(items):\n    total = 0\n",
            evidence_scope="snippet",
            is_complete=False,
            total_lines=5,
        )

        fake_provider = FakeProvider()
        fake_provider.generate = MagicMock(wraps=fake_provider.generate)
        agent = VerifierAgent(provider=fake_provider, model="gemini-3.5-flash-lite")
        with self.assertRaises(ValueError):
            agent.run(
                project_id="proj-test",
                findings=[self.valid_finding],
                snippets=[out_of_bounds_snippet],
                rules=[self.rule],
                evidence_ids=[self.commit_sha],
            )

        self.assertEqual(fake_provider.generate.call_count, 0)

    def test_a2_file_absence_claim_on_partial_snippet_rejected_zero_llm_calls(self):
        partial_snippet = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/service.py",
            diff_side="NEW",
            lines=[3, 4],
            content="    for item in items:\n        total += item.price\n",
            evidence_scope="snippet",
            is_complete=False,
            total_lines=5,
        )
        absence_finding = self.valid_finding.model_copy(update={"claim_basis": "file_absence"})

        claim_issue = _claim_verification_preflight(absence_finding, [partial_snippet])
        self.assertEqual(claim_issue, "Una afirmación de ausencia a nivel de archivo requiere un archivo completo y verificable.")

        fake_provider = FakeProvider()
        fake_provider.generate = MagicMock(wraps=fake_provider.generate)
        agent = VerifierAgent(provider=fake_provider, model="gemini-3.5-flash-lite")
        verifications = agent.run(
            project_id="proj-test",
            findings=[absence_finding],
            snippets=[partial_snippet],
            rules=[self.rule],
            evidence_ids=[self.commit_sha],
        )

        self.assertEqual(fake_provider.generate.call_count, 0)
        self.assertEqual(len(verifications), 1)
        self.assertEqual(verifications[0].verdict, VerificationVerdict.insufficient)
        self.assertEqual(verifications[0].reason, "Una afirmación de ausencia a nivel de archivo requiere un archivo completo y verificable.")

    # --------------------------------------------------------------------------
    # Prueba A3: Integridad de archivos
    # --------------------------------------------------------------------------
    def test_a3_integrity_hash_mismatch(self):
        # We test that _suggestion_verification_issue detects tampered content if sha256 doesn't match
        # CodeDiffSnippet itself validates source_sha256 in validate_coverage validator.
        with self.assertRaises(ValueError):
            CodeDiffSnippet(
                repository=self.repo_name,
                commit_sha=self.commit_sha,
                file_path="src/service.py",
                diff_side="NEW",
                lines=self.py_lines,
                content=self.py_content,
                evidence_scope="full_file",
                is_complete=True,
                total_lines=5,
                source_sha256="0" * 64,  # Incorrect hash
            )

    def test_a3_integrity_commit_sha_mismatch(self):
        other_sha = "b" * 40
        different_commit_file = self.valid_full_file.model_copy(update={"commit_sha": other_sha})
        issue = _suggestion_verification_issue(self.valid_finding, [different_commit_file])
        self.assertEqual(issue, "Se requiere un único archivo completo para validar el código sugerido.")

    def test_a3_integrity_file_path_mismatch(self):
        different_file = self.valid_full_file.model_copy(update={"file_path": "src/other.py"})
        issue = _suggestion_verification_issue(self.valid_finding, [different_file])
        self.assertEqual(issue, "Se requiere un único archivo completo para validar el código sugerido.")

    def test_a3_integrity_truncated_or_incomplete(self):
        # Snippet validation requires is_complete=True and not truncated for full_file
        with self.assertRaises(ValueError):
            CodeDiffSnippet(
                repository=self.repo_name,
                commit_sha=self.commit_sha,
                file_path="src/service.py",
                diff_side="NEW",
                lines=self.py_lines,
                content=self.py_content,
                evidence_scope="full_file",
                is_complete=False,
                total_lines=5,
                source_sha256=self.py_hash,
            )

    def test_a3_integrity_excerpt_not_unique_or_missing(self):
        # Excerpt missing in file
        finding_missing_excerpt = self.valid_finding.model_copy(update={"evidence": "non_existent_code()\n"})
        issue = _suggestion_verification_issue(finding_missing_excerpt, [self.valid_full_file])
        self.assertEqual(issue, "El fragmento original no coincide de forma única con el archivo completo.")

        # Excerpt repeated multiple times in file
        repeated_content = "x = 1\nx = 1\n"
        repeated_hash = hashlib.sha256(repeated_content.encode("utf-8")).hexdigest()
        repeated_file = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/repeat.py",
            diff_side="NEW",
            lines=[1, 2],
            content=repeated_content,
            evidence_scope="full_file",
            is_complete=True,
            total_lines=2,
            source_sha256=repeated_hash,
        )
        finding_repeated = self.valid_finding.model_copy(update={
            "file_path": "src/repeat.py",
            "evidence": "x = 1\n",
        })
        issue = _suggestion_verification_issue(finding_repeated, [repeated_file])
        self.assertEqual(issue, "El fragmento original no coincide de forma única con el archivo completo.")

    def test_a3_integrity_suggested_code_invalid_syntax(self):
        # Replacement introduces Python syntax error
        finding_broken_syntax = self.valid_finding.model_copy(update={
            "suggested_code": "        total += (def broken syntax\n"
        })
        issue = _suggestion_verification_issue(finding_broken_syntax, [self.valid_full_file])
        self.assertEqual(issue, "El código sugerido rompe la sintaxis del archivo completo.")

    def test_a3_integrity_unsupported_language_syntax(self):
        # Rust or TS or Go file
        rs_content = "fn main() {\n    println!(\"Hello\");\n}\n"
        rs_hash = hashlib.sha256(rs_content.encode("utf-8")).hexdigest()
        rs_file = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/main.rs",
            diff_side="NEW",
            lines=[1, 2, 3],
            content=rs_content,
            evidence_scope="full_file",
            is_complete=True,
            total_lines=3,
            source_sha256=rs_hash,
        )
        rs_finding = self.valid_finding.model_copy(update={
            "file_path": "src/main.rs",
            "evidence": "    println!(\"Hello\");\n",
            "suggested_code": "    println!(\"Hello World\");\n",
        })
        issue = _suggestion_verification_issue(rs_finding, [rs_file])
        self.assertEqual(issue, "Validación sintáctica no soportada para el lenguaje de este archivo.")

    # --------------------------------------------------------------------------
    # Prueba A4: Escalamiento diferencial
    # --------------------------------------------------------------------------
    def test_a4_differential_escalation_and_repo_isolation(self):
        gate = EscalationGate(EscalationPolicy())

        # High severity with mandatory rule -> must escalate
        high_finding = CandidateFinding(
            id="backend::CQ-001",
            category=FindingCategory.inyeccion_sql.value,
            file_path="src/db.py",
            line=10,
            commit_sha=self.commit_sha,
            evidence="query = f'SELECT * FROM x WHERE id={id}'",
            suggested_fix="use params",
            evidence_ids=[self.commit_sha],
            confidence=0.95,
            severity="high",
            rule_id="Q3",
            repository_identity=self.repo_identity,
        )

        # Low severity routine without rule -> must NOT escalate
        frontend_identity = RepositoryIdentity(owner="test-owner", repo="front-repo", id="frontend-repo")
        routine_finding = CandidateFinding(
            id="frontend::CQ-002",
            category=FindingCategory.otros.value,
            file_path="src/button.py",
            line=5,
            commit_sha=self.commit_sha,
            evidence="btn.label = 'Click'",
            suggested_fix="use i18n",
            evidence_ids=[self.commit_sha],
            confidence=0.9,
            severity="low",
            rule_id=None,
            repository_identity=frontend_identity,
        )

        escalated, routine, decisions = gate.filter_for_verification(
            findings=[high_finding, routine_finding],
            rules_by_id={"Q3": self.rule},
        )

        self.assertEqual(len(escalated), 1)
        self.assertEqual(escalated[0].id, "backend::CQ-001")
        self.assertEqual(len(routine), 1)
        self.assertEqual(routine[0].id, "frontend::CQ-002")

        # Repositories remain cleanly differentiated
        self.assertEqual(escalated[0].repository_identity.id, "backend-repo")
        self.assertEqual(routine[0].repository_identity.id, "frontend-repo")

    # --------------------------------------------------------------------------
    # Prueba A5: Compatibilidad real de TypeScript
    # --------------------------------------------------------------------------
    def test_a5_typescript_syntax_validation_status(self):
        # 1. Direct validation of validate_source_syntax for TypeScript
        ts_valid_code = "export const add = (a: number, b: number): number => a + b;\n"
        ts_status = validate_source_syntax("src/calc.ts", ts_valid_code)
        self.assertEqual(ts_status, "unsupported")

        tsx_status = validate_source_syntax("src/Component.tsx", "<div />")
        self.assertEqual(tsx_status, "unsupported")

        js_status = validate_source_syntax("src/index.js", "const x = 1;")
        self.assertEqual(js_status, "unsupported")

        # 2. Simulated despacho-store.ts full_file preflight check
        ts_full_content = (
            "import { Pool } from 'pg';\n"
            "export class DespachoStore {\n"
            "  async getClient() {\n"
            "    const client = await pool.connect();\n"
            "    try { return client; } finally { client.release(); }\n"
            "  }\n"
            "}\n"
        )
        ts_hash = hashlib.sha256(ts_full_content.encode("utf-8")).hexdigest()
        ts_file = CodeDiffSnippet(
            repository=self.repo_name,
            commit_sha=self.commit_sha,
            file_path="src/infrastructure/database/despacho-store.ts",
            diff_side="NEW",
            lines=list(range(1, 8)),
            content=ts_full_content,
            evidence_scope="full_file",
            is_complete=True,
            total_lines=7,
            source_sha256=ts_hash,
        )

        ts_finding = CandidateFinding(
            id="CQ-001",
            category=FindingCategory.otros.value,
            file_path="src/infrastructure/database/despacho-store.ts",
            line=5,
            commit_sha=self.commit_sha,
            evidence="    try { return client; } finally { client.release(); }\n",
            suggested_code="    try {\n      return client;\n    } finally {\n      client.release();\n    }\n",
            improvement_summary="Format release block",
            evidence_ids=[self.commit_sha],
            confidence=0.9,
            rule_id="Q3",
        )

        # Confirm that even with a perfect full_file, preflight currently returns 'unsupported'
        issue = _suggestion_verification_issue(ts_finding, [ts_file])
        self.assertEqual(issue, "Validación sintáctica no soportada para el lenguaje de este archivo.")


if __name__ == "__main__":
    unittest.main()
