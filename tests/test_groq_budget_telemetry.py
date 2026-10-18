"""Unit and integration tests for Groq budget enforcement, error classification, and MultiRepo telemetry."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agente_gestion.adapters.llm.groq import (
    GroqProvider,
    RollingWindowTokenLimiter,
    sanitize_provider_error,
)
from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError
from agente_gestion.agents.quality import CodeQualityAgent, CodeFindingsSchema, GeneratedFindingSchema
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.application.generation import (
    AuthenticationError,
    GlobalBudgetTracker,
    PayloadTooLargeError,
    ProviderInternalError,
    RateLimitExceededError,
    StageInvocationCoordinator,
)
from agente_gestion.application.repository_analysis import _safe_failure
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import FindingSeverity
from agente_gestion.domain.llm import GenerationRequest, GenerationResult, GenerationStatus
from agente_gestion.domain.report import MonitoringDraft


class GroqBudgetAndTelemetryTestCase(unittest.TestCase):
    def setUp(self):
        self.draft = MonitoringDraft(
            summary="Monitoring snapshot draft for quality review testing.",
            observed_progress=0.75,
            risks_detected=[],
        )

    def test_payload_greater_than_limit_locally_rejected_before_sending(self):
        """1. Payload mayor a la cuota: rechazo local sin llamada de red."""
        mock_transport = MagicMock()
        limiter = RollingWindowTokenLimiter(limit_tokens=6000, window_seconds=60.0)
        provider = GroqProvider(
            api_key="gsk_test_key",
            model="qwen/qwen3.8-27b",
            allow_remote_ai=True,
            transport=mock_transport,
            rate_limiter=limiter,
        )
        # 30,000 characters -> ~10,000 tokens (exceeds 6000 limit)
        huge_code = "def large_fn():\n" + ("    x = 1\n" * 2000)
        request = GenerationRequest(
            role="revisor",
            instructions="Analiza el codigo.",
            evidences={"code": huge_code},
            model="qwen/qwen3.8-27b",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Local rejection", result.error_message)
        self.assertIn("exceeds operational limit", result.error_message)
        mock_transport.assert_not_called()

    def test_payload_within_budget_permitted(self):
        """2. Payload dentro del presupuesto: solicitud enviada exitosamente."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": json.dumps({"findings": []})}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 10},
            },
            "text": "{}",
            "headers": {},
        })
        limiter = RollingWindowTokenLimiter(limit_tokens=6000, window_seconds=60.0)
        provider = GroqProvider(
            api_key="gsk_test_key",
            model="qwen/qwen3.8-27b",
            allow_remote_ai=True,
            transport=mock_transport,
            rate_limiter=limiter,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "x = 1"},
            model="qwen/qwen3.8-27b",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.complete)
        mock_transport.assert_called_once()
        self.assertGreater(limiter.current_usage(), 0)

    def test_two_repositories_same_itpm_window_aggregate_quota_pacing(self):
        """3. Dos repositorios en la misma ventana ITPM: cumplimiento de cuota agregada y espera controlada."""
        current_time = 100.0
        sleep_calls: list[float] = []

        def mock_time():
            return current_time

        def mock_sleep(seconds: float):
            nonlocal current_time
            sleep_calls.append(seconds)
            current_time += seconds

        limiter = RollingWindowTokenLimiter(
            limit_tokens=6000,
            window_seconds=60.0,
            sleeper=mock_sleep,
            time_fn=mock_time,
        )

        # First request consumes 4000 tokens at t=100
        self.assertTrue(limiter.acquire(4000, timeout=120.0))
        self.assertEqual(limiter.current_usage(), 4000)

        # Advance 10s: t=110
        current_time = 110.0
        # Second request wants 3500 tokens: 4000 + 3500 = 7500 > 6000
        # It must wait until t=160.05 for the first request to expire
        self.assertTrue(limiter.acquire(3500, timeout=120.0))
        self.assertGreater(len(sleep_calls), 0)
        self.assertGreaterEqual(current_time, 160.0)
        # At this point, the first 4000 tokens rolled out, usage is only 3500
        self.assertEqual(limiter.current_usage(), 3500)

    def test_insufficient_context_budget_for_useful_review(self):
        """4. Contexto insuficiente: resultado parcial o error de presupuesto sin conclusiones injustificadas."""
        builder = ContextBuilder()
        snippet = CodeDiffSnippet(
            repository="org/repo",
            commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            file_path="src/service.ts",
            diff_side="NEW",
            lines=[1, 2, 3],
            content="const a = 1;\nconst b = 2;\nconst c = 3;\n",
            evidence_scope="snippet",
        )
        # Extremely small budget (e.g. 5 tokens) cannot even accommodate base overhead
        with self.assertRaises(ContextBudgetExceededError):
            builder.build_code_context(
                project_id="PROJ",
                draft=self.draft,
                criteria=[],
                code_snippets=[snippet],
                budget_tokens=5,
                strict_budget=True,
            )

    def test_json_conforming_to_schema_with_1024_output_limit(self):
        """5. Generación de JSON conforme al esquema con 1024 tokens de salida."""
        finding_data = {
            "id": "CQ-001",
            "rule_id": None,
            "claim": "Direct user input interpolation into query",
            "severity": "high",
            "evidence_ids": ["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"],
            "commit_sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "file_path": "src/query.ts",
            "line": 15,
            "confidence": 0.95,
            "requires_verification": True,
            "suggested_code": "db.query('SELECT * FROM t WHERE id = $1', [id])",
            "improvement_summary": "Parametriza la consulta.",
        }
        schema = CodeFindingsSchema(findings=[GeneratedFindingSchema.model_validate(finding_data)])
        json_output = schema.model_dump_json()
        self.assertLess(len(json_output), 1024 * 3)  # Comfortably fits in 1024 tokens
        parsed = CodeFindingsSchema.model_validate_json(json_output)
        self.assertEqual(len(parsed.findings), 1)
        self.assertEqual(parsed.findings[0].id, "CQ-001")

    def test_http_413_classified_correctly_without_retries(self):
        """6. Error HTTP 413: clasificación como PayloadTooLargeError y sin reintentos idénticos."""
        mock_transport = MagicMock(return_value={
            "status_code": 413,
            "body": {
                "error": {
                    "message": "Request too large for model in organization 'org_test123' on ITPM: Limit 7000, Requested 73185",
                }
            },
            "text": "Request too large",
            "headers": {},
        })
        provider = GroqProvider(
            api_key="gsk_test_key",
            model="qwen/qwen3.8-27b",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
            max_transient_retries=2,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "huge code"},
            model="qwen/qwen3.8-27b",
        )
        with self.assertRaises(PayloadTooLargeError) as ctx:
            coordinator.invoke_stage(
                stage="quality:backend",
                provider=provider,
                request=request,
            )
        self.assertIn("413", str(ctx.exception))
        # Crucial: 413 must NOT be retried (call count must be exactly 1)
        self.assertEqual(mock_transport.call_count, 1)

    def test_http_429_handled_with_transient_retry(self):
        """7. Error HTTP 429: manejo como rate limit con reintento acotado."""
        mock_transport = MagicMock(side_effect=[
            {"status_code": 429, "body": {"error": {"message": "Rate limit reached"}}, "headers": {}},
            {"status_code": 200, "body": {"choices": [{"message": {"content": json.dumps({"findings": []})}}]}, "headers": {}},
        ])
        provider = GroqProvider(
            api_key="gsk_test_key",
            model="qwen/qwen3.8-27b",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
            max_transient_retries=1,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "code"},
            model="qwen/qwen3.8-27b",
        )
        _, data = coordinator.invoke_stage(
            stage="quality:frontend",
            provider=provider,
            request=request,
        )
        self.assertEqual(mock_transport.call_count, 2)
        self.assertEqual(data.get("findings"), [])

    def test_telemetry_multirepo_stages_grouped_under_quality_preserving_repository_id(self):
        """8. Proyección de telemetría MultiRepo con etapas quality:<repo_id> y verifier:<repo_id>."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE llm_calls (
                    call_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    request_ref TEXT,
                    stage TEXT,
                    attempt_number INTEGER,
                    provider TEXT,
                    model TEXT,
                    status TEXT,
                    finish_reason TEXT,
                    duration_seconds REAL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    validation_passed INTEGER,
                    validation_errors TEXT,
                    error_message TEXT,
                    raw_response TEXT,
                    structured_data TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    status TEXT,
                    is_simulated INTEGER,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_seconds REAL,
                    error_code TEXT,
                    summary_text TEXT,
                    snapshot_id TEXT,
                    report_id TEXT,
                    total_llm_calls INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE stage_results (
                    run_id TEXT,
                    stage_name TEXT,
                    status TEXT,
                    duration_seconds REAL,
                    completed_at TEXT,
                    output_summary TEXT,
                    error_message TEXT,
                    PRIMARY KEY (run_id, stage_name)
                )
            """)
            run_id = "run-test-telemetry-01"
            conn.execute("INSERT INTO runs (run_id, project_id, status) VALUES (?, 'PROJ', 'needs_attention')", (run_id,))
            conn.execute("INSERT INTO stage_results (run_id, stage_name, status) VALUES (?, 'quality', 'needs_attention')", (run_id,))
            conn.execute("""
                INSERT INTO llm_calls (call_id, run_id, stage, provider, model, status, duration_seconds)
                VALUES ('c1', ?, 'quality:backend', 'groq', 'qwen/qwen3.8-27b', 'error', 0.61),
                       ('c2', ?, 'quality:frontend', 'groq', 'qwen/qwen3.8-27b', 'error', 0.20)
            """, (run_id, run_id))
            conn.commit()
            conn.close()

            settings = ApiSettings(
                db_path=str(db_path),
                runs_dir=str(tmp_dir),
                projects=(ProjectConfig(id="PROJ", name="Test Project"),),
            )
            adapter = SQLiteQueryRepository(settings=settings)
            stages = adapter.run_stages(run_id)
            quality_stage = next(s for s in stages.items if s.stage_name == "quality")
            self.assertIsNotNone(quality_stage.providers)
            self.assertEqual(len(quality_stage.providers), 2)

            backend_attempt = next(p for p in quality_stage.providers if p.repository_id == "backend")
            frontend_attempt = next(p for p in quality_stage.providers if p.repository_id == "frontend")

            self.assertEqual(backend_attempt.provider, "groq")
            self.assertEqual(backend_attempt.status, "failed")
            self.assertEqual(backend_attempt.duration_seconds, 0.61)

            self.assertEqual(frontend_attempt.provider, "groq")
            self.assertEqual(frontend_attempt.status, "failed")
            self.assertEqual(frontend_attempt.duration_seconds, 0.20)

    def test_error_message_scrubbing_redacts_org_ids_and_credentials(self):
        """10. Sanitización de errores: enmascaramiento de organization IDs y credenciales."""
        raw_error = (
            "Groq API error 413: Request too large for model 'qwen/qwen3.8-27b' in organization "
            "'org_01m49p6dmxeepsbvhj78c953gc' with Bearer gsk_secret_1234567890."
        )
        sanitized = sanitize_provider_error(raw_error, secret="gsk_secret_1234567890")
        self.assertNotIn("org_01m49p6dmxeepsbvhj78c953gc", sanitized)
        self.assertNotIn("gsk_secret_1234567890", sanitized)
        self.assertIn("org_[REDACTED]", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_safe_failure_categorization(self):
        """Validación de mensajes acotados en _safe_failure sin filtrar información confidencial."""
        exc_413 = PayloadTooLargeError("413 Request too large")
        reason_413 = _safe_failure("quality", exc_413)
        self.assertIn("PayloadTooLargeError", reason_413)
        self.assertIn("input tokens exceed provider limit", reason_413)

        exc_429 = RateLimitExceededError("429 Rate limit reached")
        reason_429 = _safe_failure("quality", exc_429)
        self.assertIn("RateLimitExceededError", reason_429)
        self.assertIn("rate limit reached", reason_429)

        exc_auth = AuthenticationError("401 Unauthorized")
        reason_auth = _safe_failure("quality", exc_auth)
        self.assertIn("AuthenticationError", reason_auth)
        self.assertIn("provider credentials rejected", reason_auth)

    def test_build_code_context_reserves_metadata_headroom_for_runtime_payload(self):
        """Verifica que build_code_context con strict_budget reserva margen para selection y context_metadata."""
        builder = ContextBuilder()
        snippets = [
            CodeDiffSnippet(
                repository="backend",
                commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                file_path=f"src/service_{i}.ts",
                diff_side="RIGHT",
                lines=[1],
                content="const x = 1;\n" * 40,
                evidence_scope="snippet",
            )
            for i in range(30)
        ]
        # Con presupuesto ajustado, no debe lanzar ContextBudgetExceededError al generar runtime_payload
        ctx = builder.build_code_context(
            project_id="PROJ",
            draft=self.draft,
            criteria=["seguridad"],
            code_snippets=snippets,
            budget_tokens=2500,
            strict_budget=True,
        )
        self.assertTrue(ctx.is_truncated)
        self.assertEqual(ctx.status, "incomplete")
        self.assertLessEqual(ctx.estimated_tokens, 2500)

        # runtime_payload debe ejecutarse limpiamente dentro del presupuesto
        payload = builder.runtime_payload(
            ctx,
            instructions="Analiza el código.",
            schema={"type": "object"},
            output_tokens=768,
            context_tokens=6000,
            max_output_tokens=768,
        )
        self.assertIn("context_metadata", payload)
        self.assertLessEqual(
            payload["context_metadata"]["estimated_tokens"],
            payload["context_metadata"]["budget_tokens"],
        )

    def test_groq_provider_deducts_pacing_wait_from_transport_timeout(self):
        """Verifica que el tiempo consumido en pacing se descuente del socket timeout del transporte."""
        captured_timeout = None

        def mock_transport(url, headers, payload=None, timeout=30.0, **kwargs):
            nonlocal captured_timeout
            captured_timeout = timeout
            return {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": "{}"}}]},
                "text": "{}",
                "headers": {},
            }

        current_time = 100.0

        def mock_time():
            return current_time

        def mock_sleep(seconds: float):
            nonlocal current_time
            current_time += seconds

        limiter = RollingWindowTokenLimiter(
            limit_tokens=6000,
            window_seconds=60.0,
            sleeper=mock_sleep,
            time_fn=mock_time,
        )
        # Pre-consume 4000 tokens at t=100
        limiter.acquire(4000, timeout=120.0)

        # Advance to t=110
        current_time = 110.0

        provider = GroqProvider(
            api_key="gsk_test_key",
            allow_remote_ai=True,
            transport=mock_transport,
            rate_limiter=limiter,
        )

        # Second request wants ~3500 tokens -> 4000 + 3500 = 7500 > 6000 -> needs to wait ~50s until t=160
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "x = 1\n" * 1600},  # ~3700 tokens
            model="qwen/qwen3.8-27b",
            timeout_seconds=120.0,
        )

        with patch("agente_gestion.adapters.llm.groq.time.monotonic", side_effect=lambda: current_time):
            result = provider.generate(request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertIsNotNone(captured_timeout)
        # 120s timeout - ~50s pacing wait = ~70s remaining for transport socket
        self.assertLess(captured_timeout, 120.0)
        self.assertGreaterEqual(captured_timeout, 5.0)


if __name__ == "__main__":
    unittest.main()

