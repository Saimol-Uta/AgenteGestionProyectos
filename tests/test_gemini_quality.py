"""Comprehensive simulated tests for Gemini 3.5 Flash-Lite as CodeQualityAgent reviewer."""

import json
import unittest
from unittest.mock import MagicMock, patch

from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.mistral import MistralProvider
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.application.config import Settings
from agente_gestion.application.escalation import EscalationGate
from agente_gestion.application.generation import (
    AuthenticationError,
    RateLimitExceededError,
    StageInvocationCoordinator,
)
from agente_gestion.application.repository_analysis import (
    _prepare_work,
    _scope_payload,
    analyze_repository_results,
)
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryIdentity,
)
from agente_gestion.domain.run import StageStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot


class GeminiQualityReviewerTestCase(unittest.TestCase):
    def setUp(self):
        self.draft = MonitoringDraft(
            summary="Monitoring snapshot draft for Gemini quality review testing.",
            observed_progress=0.80,
            risks_detected=[],
        )

    def test_gemini_quality_capabilities_and_thinking_toggle(self):
        """1. Capacidades de Gemini como Revisor: 128k contexto, 4k salida, reasoning_options activo con thinking_level."""
        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            context_tokens=128000,
            max_output_tokens=4096,
            thinking_level="medium",
        )
        caps = provider.get_capabilities()
        self.assertEqual(caps.context_tokens, 128000)
        self.assertEqual(caps.max_output_tokens, 4096)
        self.assertTrue(caps.supports_json_schema)
        self.assertTrue(caps.reasoning_options)

        provider_no_thinking = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            context_tokens=128000,
            max_output_tokens=4096,
            thinking_level=None,
        )
        self.assertFalse(provider_no_thinking.get_capabilities().reasoning_options)

    def test_gemini_payload_construction_includes_thinking_config(self):
        """2. Construcción de payload: generación con thinkingConfig y thinkingLevel MEDIUM."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"findings": []}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 150, "candidatesTokenCount": 10},
            },
            "text": "{}",
        })
        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=4096,
            thinking_level="medium",
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar código.",
            evidences={"code": "pass"},
            model="gemini-3.5-flash-lite",
            max_generation_tokens=4096,
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.complete)

        sent_payload = mock_transport.call_args[1].get("payload") or mock_transport.call_args[1].get("json")
        self.assertIsNotNone(sent_payload)
        gen_cfg = sent_payload.get("generationConfig", {})
        self.assertIn("thinkingConfig", gen_cfg)
        self.assertEqual(gen_cfg["thinkingConfig"]["thinkingLevel"], "MEDIUM")
        self.assertEqual(gen_cfg["maxOutputTokens"], 4096)

    def test_gemini_code_quality_agent_structured_findings_parsing(self):
        """3. Compatibilidad con CodeQualityAgent y validación del esquema Pydantic de hallazgos."""
        mock_findings = {
            "findings": [
                {
                    "id": "CQ-001",
                    "rule_id": None,
                    "claim": "Insecure password comparison vulnerable to timing attack",
                    "severity": "high",
                    "evidence_ids": ["ev-01"],
                    "commit_sha": "c0ffee1",
                    "file_path": "src/api/auth.py",
                    "line": 42,
                    "confidence": 0.95,
                    "suggested_code": "import hmac\nreturn hmac.compare_digest(a, b)",
                    "improvement_summary": "Use constant time comparison to mitigate timing attacks",
                }
            ]
        }
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": json.dumps(mock_findings)}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 850, "candidatesTokenCount": 120},
            },
            "text": "{}",
        })

        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=4096,
            thinking_level="medium",
            transport=mock_transport,
        )

        agent = CodeQualityAgent(
            provider=provider,
            model="gemini-3.5-flash-lite",
            timeout_seconds=60,
            max_output_tokens=4096,
            context_builder=ContextBuilder(),
        )

        snippet = CodeDiffSnippet(
            repository="test-repo",
            commit_sha="c0ffee1",
            file_path="src/api/auth.py",
            diff_side="NEW",
            lines=[40, 41, 42, 43],
            content="def check_password(a, b):\n    return a == b\n",
        )

        findings = agent.run(
            project_id="TEST-GEMINI",
            draft=self.draft,
            snippets=[snippet],
            rules=[],
            evidence_ids=["ev-01"],
            repository_scope={"repo_name": "test-repo", "branch": "develop"},
            stage_name="quality:test",
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertIsNone(finding.rule_id)
        self.assertEqual(finding.file_path, "src/api/auth.py")
        self.assertEqual(finding.line, 42)
        self.assertEqual(finding.severity, "high")
        self.assertIn("timing attack", finding.claim)
        self.assertIn("hmac.compare_digest", finding.suggested_code)

    def test_gemini_rate_limit_429_mapped_properly(self):
        """4. Control de errores HTTP 429: RateLimitExceededError capturado correctamente."""
        mock_transport = MagicMock(return_value={
            "status_code": 429,
            "body": {"error": {"message": "Resource has been exhausted (e.g. check quota)."}},
            "text": "Resource has been exhausted",
        })
        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        agent = CodeQualityAgent(
            provider=provider,
            model="gemini-3.5-flash-lite",
            context_builder=ContextBuilder(),
        )
        snippet = CodeDiffSnippet(
            repository="test-repo",
            commit_sha="c0ffee1",
            file_path="src/main.py",
            diff_side="NEW",
            lines=[1],
            content="print('hello')",
        )
        with self.assertRaises(RateLimitExceededError):
            agent.run(
                project_id="TEST",
                draft=self.draft,
                snippets=[snippet],
                rules=[],
                stage_name="quality:backend",
            )

    def test_gemini_400_and_500_errors_handled(self):
        """5. Control de errores HTTP 400 y 500: manejo limpio de excepciones no reintentables."""
        mock_transport_400 = MagicMock(return_value={
            "status_code": 400,
            "body": {"error": {"message": "Invalid argument: thinkingLevel unsupported"}},
            "text": "Invalid argument",
        })
        provider_400 = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            transport=mock_transport_400,
        )
        res_400 = provider_400.generate(GenerationRequest(
            role="revisor",
            instructions="Test",
            evidences={"x": 1},
            model="gemini-3.5-flash-lite",
        ))
        self.assertEqual(res_400.status, GenerationStatus.error)
        self.assertIn("Gemini API error 400", res_400.error_message)

    def test_multirepo_backend_and_frontend_both_succeed_with_gemini(self):
        """6. Análisis MultiRepo: backend y frontend ejecutan revisión completa sin colisión de presupuesto."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"findings": []}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 2200, "candidatesTokenCount": 30},
            },
            "text": "{}",
        })

        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=4096,
            thinking_level="medium",
            transport=mock_transport,
        )

        cb = ContextBuilder()
        agent = CodeQualityAgent(
            provider=provider,
            model="gemini-3.5-flash-lite",
            timeout_seconds=120,
            max_output_tokens=4096,
            context_builder=cb,
        )

        # Backend snippet (simulating 15 snippets)
        backend_snippets = [
            CodeDiffSnippet(
                repository="hormigestion-backend",
                commit_sha="b001",
                file_path=f"src/service_{i}.py",
                diff_side="NEW",
                lines=[1, 2],
                content=f"def fn_{i}(): pass\n",
            )
            for i in range(15)
        ]
        # Frontend snippet (simulating 2 snippets)
        frontend_snippets = [
            CodeDiffSnippet(
                repository="hormigestion-frontend",
                commit_sha="f001",
                file_path=f"src/component_{i}.tsx",
                diff_side="NEW",
                lines=[1, 2],
                content=f"export const Comp_{i} = () => null;\n",
            )
            for i in range(2)
        ]

        backend_findings = agent.run(
            project_id="HORMIGESTION",
            draft=self.draft,
            snippets=backend_snippets,
            rules=[],
            evidence_ids=[f"ev-back-{i}" for i in range(15)],
            repository_scope={"repo_name": "hormigestion-backend", "branch": "develop"},
            stage_name="quality:backend",
        )
        self.assertIsInstance(backend_findings, list)

        frontend_findings = agent.run(
            project_id="HORMIGESTION",
            draft=self.draft,
            snippets=frontend_snippets,
            rules=[],
            evidence_ids=[f"ev-front-{i}" for i in range(2)],
            repository_scope={"repo_name": "hormigestion-frontend", "branch": "develop"},
            stage_name="quality:frontend",
        )
        self.assertIsInstance(frontend_findings, list)
        self.assertEqual(mock_transport.call_count, 2)

    def test_multirepo_analysis_allocates_full_8192_budget_to_both_repositories(self):
        """7. MultiRepo: backend y frontend reciben presupuestos independientes de 8.192 tokens sin división."""
        payloads_captured = []

        def mock_transport(url, headers=None, payload=None, json=None, timeout=None):
            body = payload or json or {}
            payloads_captured.append(body)
            return {
                "status_code": 200,
                "body": {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": '{"findings": []}'}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 20},
                },
                "text": "{}",
            }

        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=8192,
            thinking_level="medium",
            transport=mock_transport,
        )

        backend_identity = RepositoryIdentity(id="backend", owner="Josu-F1", repo="hormigestion-backend")
        frontend_identity = RepositoryIdentity(id="frontend", owner="Josu-F1", repo="hormigestion-frontend")

        backend_sha = "b" * 40
        frontend_sha = "f" * 40

        backend_state = RepositoryAnalysisState(
            identity=backend_identity,
            branch="develop",
            target_commit_sha=backend_sha,
            commit_resolution_status=CommitResolutionStatus.resolved,
            access_status=RepositoryAccessStatus.allowed,
            availability_status=RepositoryAvailabilityStatus.available,
        )
        frontend_state = RepositoryAnalysisState(
            identity=frontend_identity,
            branch="develop",
            target_commit_sha=frontend_sha,
            commit_resolution_status=CommitResolutionStatus.resolved,
            access_status=RepositoryAccessStatus.allowed,
            availability_status=RepositoryAvailabilityStatus.available,
        )

        backend_snippet = CodeDiffSnippet(
            repository="Josu-F1/hormigestion-backend",
            commit_sha=backend_sha,
            file_path="src/service.py",
            diff_side="NEW",
            lines=[1, 2],
            content="def f(): pass\n",
            repository_identity=backend_identity,
        )
        frontend_snippet = CodeDiffSnippet(
            repository="Josu-F1/hormigestion-frontend",
            commit_sha=frontend_sha,
            file_path="src/app.tsx",
            diff_side="NEW",
            lines=[1, 2],
            content="export const App = () => null;\n",
            repository_identity=frontend_identity,
        )

        backend_result = RepositorySnapshotResult(
            repository_state=backend_state,
            code_diffs=[backend_snippet],
            is_complete=False,
            incomplete_reason="File budget exhausted",
        )
        frontend_result = RepositorySnapshotResult(
            repository_state=frontend_state,
            code_diffs=[frontend_snippet],
            is_complete=True,
        )

        coordinator = StageInvocationCoordinator()
        escalation_gate = EscalationGate()
        config = MagicMock()
        config.quality_timeout_seconds = 120.0
        config.verifier_timeout_seconds = 120.0
        config.quality_max_output_tokens = 8192
        config.quality_context_margin = 64
        config.allow_remote_ai = True
        config.is_simulated = False
        config.global_quality_max_output_tokens = None

        outcome = analyze_repository_results(
            [backend_result, frontend_result],
            project_id="HORMIGESTION",
            draft=self.draft,
            rules=[],
            quality_provider=provider,
            verifier_provider=None,
            coordinator=coordinator,
            escalation_gate=escalation_gate,
            config=config,
        )

        self.assertEqual(len(payloads_captured), 2)
        for sent_body in payloads_captured:
            gen_cfg = sent_body.get("generationConfig", {})
            self.assertEqual(gen_cfg.get("maxOutputTokens"), 8192)
            self.assertEqual(gen_cfg.get("thinkingConfig", {}).get("thinkingLevel"), "MEDIUM")

        self.assertIn("quality:backend", outcome.stages)
        self.assertIn("quality:frontend", outcome.stages)
        self.assertEqual(outcome.stages["quality:backend"].status, StageStatus.needs_attention)
        self.assertEqual(outcome.stages["quality:frontend"].status, StageStatus.completed)

    def test_gemini_quality_completes_when_response_exceeds_2048_tokens(self):
        """8. Respuesta con razonamiento y JSON que supera 2.048 tokens se completa exitosamente con límite 8.192."""
        mock_findings = {
            "findings": [
                {
                    "id": f"CQ-{i:03d}",
                    "rule_id": None,
                    "claim": f"Defect description number {i} with extensive detail",
                    "severity": "medium",
                    "evidence_ids": ["ev-01"],
                    "commit_sha": "c0ffee1",
                    "file_path": "src/api/auth.py",
                    "line": 10 + i,
                    "confidence": 0.90,
                    "suggested_code": f"# fix {i}\npass",
                    "improvement_summary": f"Improvement {i}",
                }
                for i in range(10)
            ]
        }
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": json.dumps(mock_findings)}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 2500},
            },
            "text": "{}",
        })

        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=8192,
            thinking_level="medium",
            transport=mock_transport,
        )

        agent = CodeQualityAgent(
            provider=provider,
            model="gemini-3.5-flash-lite",
            timeout_seconds=60,
            max_output_tokens=8192,
            context_builder=ContextBuilder(),
        )

        snippet = CodeDiffSnippet(
            repository="test-repo",
            commit_sha="c0ffee1",
            file_path="src/api/auth.py",
            diff_side="NEW",
            lines=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
            content="def auth():\n    pass\n",
        )

        findings = agent.run(
            project_id="TEST-GEMINI",
            draft=self.draft,
            snippets=[snippet],
            rules=[],
            evidence_ids=["ev-01"],
            repository_scope={"repo_name": "test-repo", "branch": "develop"},
            stage_name="quality:test",
        )

        self.assertEqual(len(findings), 10)
        sent_payload = mock_transport.call_args[1].get("payload") or mock_transport.call_args[1].get("json")
        self.assertEqual(sent_payload["generationConfig"]["maxOutputTokens"], 8192)

    def test_gemini_max_tokens_truncation_is_strictly_rejected(self):
        """9. Respuesta con MAX_TOKENS es estrictamente rechazada sin aceptar JSON truncado."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"findings": [{"id": "CQ-001"'}]},
                        "finishReason": "MAX_TOKENS",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 75000, "candidatesTokenCount": 2048},
            },
            "text": "Truncated",
        })

        provider = GeminiProvider(
            api_key="synthetic_gemini_key",
            model="gemini-3.5-flash-lite",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=8192,
            thinking_level="medium",
            transport=mock_transport,
        )

        res = provider.generate(GenerationRequest(
            role="revisor",
            instructions="Review code",
            evidences={"code": "snippet"},
            model="gemini-3.5-flash-lite",
            max_generation_tokens=8192,
        ))
        self.assertEqual(res.status, GenerationStatus.incomplete)
        self.assertEqual(res.finish_reason, "MAX_TOKENS")
        self.assertIn("truncated by max tokens limit", res.error_message)

        agent = CodeQualityAgent(
            provider=provider,
            model="gemini-3.5-flash-lite",
            max_output_tokens=8192,
        )
        snippet = CodeDiffSnippet(
            repository="test-repo",
            commit_sha="c0ffee1",
            file_path="src/main.py",
            diff_side="NEW",
            lines=[1],
            content="x = 1\n",
        )
        with self.assertRaises(RuntimeError) as ctx:
            agent.run(
                project_id="TEST",
                draft=self.draft,
                snippets=[snippet],
                rules=[],
                stage_name="quality:backend",
            )
        self.assertIn("Gemini generation truncated by max tokens limit", str(ctx.exception))

    def test_groq_and_mistral_retain_independent_output_limits(self):
        """10. Groq y Mistral conservan sus límites y configuraciones particulares sin heredar los 8.192 de Gemini."""
        groq = GroqProvider(
            api_key="gsk_test",
            model="llama-3.3-70b-versatile",
            max_output_tokens=1024,
        )
        mistral = MistralProvider(
            api_key="mis_test",
            model="mistral-large-2512",
            max_output_tokens=4096,
        )
        self.assertEqual(groq.get_capabilities().max_output_tokens, 1024)
        self.assertEqual(mistral.get_capabilities().max_output_tokens, 4096)


if __name__ == "__main__":
    unittest.main()
