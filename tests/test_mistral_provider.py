"""Unit and integration tests for MistralProvider adapter, error sanitization, and MultiRepo quality review."""

import json
import unittest
from unittest.mock import MagicMock, patch

from agente_gestion.adapters.llm.mistral import (
    MistralProvider,
    sanitize_mistral_error,
)
from agente_gestion.adapters.llm.groq import RollingWindowTokenLimiter
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.application.repository_analysis import _prepare_work, _scope_payload
from agente_gestion.application.security import (
    RemoteProviderNotAllowedError,
    RemotePayloadSecurityError,
)
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.snapshot import WeeklySnapshot


class MistralProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.draft = MonitoringDraft(
            summary="Monitoring snapshot draft for Mistral quality review testing.",
            observed_progress=0.75,
            risks_detected=[],
        )

    def test_capabilities_advertised_correctly(self):
        """1. Capacidades anunciadas: 128k ventana de contexto, 4k tokens salida y soporte de JSON Schema."""
        provider = MistralProvider(
            api_key="mis_test_key_12345",
            model="mistral-large-2512",
            context_tokens=128000,
            max_output_tokens=4096,
        )
        caps = provider.get_capabilities()
        self.assertEqual(caps.context_tokens, 128000)
        self.assertEqual(caps.max_output_tokens, 4096)
        self.assertTrue(caps.supports_json_schema)
        self.assertFalse(caps.supports_tools)
        self.assertFalse(caps.supports_streaming)

    def test_missing_api_key_fails_fast_without_network_call(self):
        """2. Clave ausente: rechazo inmediato sin llamada de red."""
        mock_transport = MagicMock()
        provider = MistralProvider(
            api_key="",
            model="mistral-large-2512",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar código.",
            evidences={"code": "def test(): pass"},
            model="mistral-large-2512",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Missing API key", result.error_message)
        mock_transport.assert_not_called()

    def test_remote_ai_disabled_rejected(self):
        """3. Remote AI deshabilitado: rechazo de seguridad por assert_remote_allowed."""
        provider = MistralProvider(
            api_key="mis_test_key_12345",
            model="mistral-large-2512",
            allow_remote_ai=False,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar código.",
            evidences={"code": "def test(): pass"},
            model="mistral-large-2512",
        )
        with self.assertRaises(RemoteProviderNotAllowedError):
            provider.generate(request)

    def test_secret_bearing_payload_rejected(self):
        """4. Carga con secretos: rechazo por assert_safe_remote_payload."""
        provider = MistralProvider(
            api_key="mis_test_key_12345",
            model="mistral-large-2512",
            allow_remote_ai=True,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar código.",
            evidences={"code": "API_KEY = 'AIzaSyA_secret_fake_1234567890'"},
            model="mistral-large-2512",
        )
        with self.assertRaises(RemotePayloadSecurityError):
            provider.generate(request)

    def test_successful_structured_json_generation(self):
        """5. Generación exitosa de JSON estructurado conforme a la API de Mistral."""
        expected_findings = {
            "findings": [
                {
                    "id": "CQ-001",
                    "rule_id": "SEC-01",
                    "claim": "SQL injection risk in string formatting",
                    "severity": "high",
                    "file_path": "src/repository.py",
                    "line": 42,
                    "evidence_snippet": "query = f'SELECT * FROM users WHERE id = {user_id}'",
                    "remediation": "Use parameterized queries.",
                }
            ]
        }
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"content": json.dumps(expected_findings)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 350, "completion_tokens": 85},
            },
            "text": "{}",
            "headers": {},
        })
        provider = MistralProvider(
            api_key="mis_secret_key_abcdef",
            model="mistral-large-2512",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar código según reglas de calidad.",
            evidences={"code": "def get_user(): pass"},
            schema_name="code_findings_v1",
            json_schema={"type": "object"},
            model="mistral-large-2512",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.provider, "mistral")
        self.assertEqual(result.prompt_tokens, 350)
        self.assertEqual(result.completion_tokens, 85)
        self.assertEqual(result.structured_data, expected_findings)
        mock_transport.assert_called_once()

    def test_http_error_sanitization_and_masking(self):
        """6. Sanitización de errores HTTP: enmascaramiento de claves y cabeceras Bearer."""
        raw_error_401 = (
            "Unauthorized access with Bearer mis_secret_key_abcdef. Invalid credential."
        )
        mock_transport = MagicMock(return_value={
            "status_code": 401,
            "body": {"message": raw_error_401},
            "text": raw_error_401,
            "headers": {},
        })
        provider = MistralProvider(
            api_key="mis_secret_key_abcdef",
            model="mistral-large-2512",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "x = 1"},
            model="mistral-large-2512",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Mistral API error 401", result.error_message)
        self.assertNotIn("mis_secret_key_abcdef", result.error_message)
        self.assertIn("[REDACTED_CREDENTIAL]", result.error_message)

    def test_length_truncation_incomplete(self):
        """7. Truncamiento por longitud: estado incomplete cuando finish_reason es 'length'."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"content": '{"findings": [{"id": "CQ-001"'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 500, "completion_tokens": 1024},
            },
            "text": "...",
            "headers": {},
        })
        provider = MistralProvider(
            api_key="mis_secret_key_abcdef",
            model="mistral-large-2512",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "x = 1"},
            model="mistral-large-2512",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.incomplete)
        self.assertEqual(result.finish_reason, "length")
        self.assertIn("truncated", result.error_message)

    def test_malformed_json_returns_error(self):
        """8. JSON malformado: captura de JSONDecodeError con estado error."""
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"content": "Esto no es un JSON válido"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            "text": "Esto no es un JSON válido",
            "headers": {},
        })
        provider = MistralProvider(
            api_key="mis_secret_key_abcdef",
            model="mistral-large-2512",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        request = GenerationRequest(
            role="revisor",
            instructions="Revisar.",
            evidences={"code": "x = 1"},
            model="mistral-large-2512",
        )
        result = provider.generate(request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Failed to parse structured JSON", result.error_message)

    def test_multirepo_code_quality_agent_with_mistral_accommodates_backend_and_frontend(self):
        """9. Simulación de revisión MultiRepo: Mistral acomoda holgadamente el contexto de backend y frontend."""
        # Cargar los datos exactos del snapshot de la ejecución real run-20261018-142631-096d5a
        import os
        snapshot_path = "runs/run-20261018-142631-096d5a/snapshot.json"
        if not os.path.exists(snapshot_path):
            self.skipTest(f"Snapshot file not found: {snapshot_path}")

        with open(snapshot_path) as f:
            snapshot_data = json.load(f)

        snapshot = WeeklySnapshot.model_validate(snapshot_data)
        results = snapshot.repository_results

        # Configurar transporte simulado para Mistral que responde hallazgos válidos
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "findings": [],
                                "review_notes": "All code complies with clean architecture policies.",
                            })
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4500, "completion_tokens": 120},
            },
            "text": "{}",
            "headers": {},
        })

        provider = MistralProvider(
            api_key="mis_valid_test_key",
            model="mistral-large-2512",
            allow_remote_ai=True,
            context_tokens=128000,
            max_output_tokens=4096,
            transport=mock_transport,
        )

        cb = ContextBuilder()

        ids = [item.repository_state.identity.id for item in results]
        names = [(item.repository_state.identity.owner.casefold(), item.repository_state.identity.repo.casefold()) for item in results]
        duplicate_ids = {v for v in ids if ids.count(v) > 1}
        duplicate_names = {v for v in names if names.count(v) > 1}
        works = [_prepare_work(item, index, duplicate_ids, duplicate_names) for index, item in enumerate(results)]

        for work in works:
            agent = CodeQualityAgent(
                provider=provider,
                model="mistral-large-2512",
                timeout_seconds=120,
                max_output_tokens=2048,
                context_builder=cb,
            )
            # Ambos repositorios deben ejecutar la revisión sin lanzar ContextBudgetExceededError
            findings = agent.run(
                project_id="HORMIGESTION",
                draft=self.draft,
                snippets=work.snippets,
                rules=snapshot.policies,
                evidence_ids=work.evidence_ids,
                repository_scope=_scope_payload(work),
                stage_name=f"quality:{work.stage_id}",
            )
            self.assertIsInstance(findings, list)

        # El transporte fue invocado dos veces (una para backend y una para frontend)
        self.assertEqual(mock_transport.call_count, 2)


if __name__ == "__main__":
    unittest.main()
