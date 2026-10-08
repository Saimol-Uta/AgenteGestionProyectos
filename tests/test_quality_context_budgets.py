"""Tests for provider/model-aware context budgeting and fail-closed quality execution."""

import json
import unittest
from unittest.mock import MagicMock

from agente_gestion.agents.context import (
    ContextBuilder,
    ContextBudgetExceededError,
)
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.mistral import MistralProvider


class QualityContextBudgetsTestCase(unittest.TestCase):
    def setUp(self):
        self.draft = MonitoringDraft(
            summary="Draft summary for sprint",
            task_state_claims=[],
            risks=[],
            suggested_actions=[],
        )
        self.rule = QualityRule(
            id="SEC-001",
            description="No concatenations in SQL queries",
            metric_key="open_critical_findings",
            source="review",
        )
        self.small_snippet = CodeDiffSnippet(
            repository="repo",
            commit_sha="a1b2c3d4e5f67890123456789012345678901234",
            file_path="src/auth/service.py",
            diff_side="NEW",
            lines=[12, 13, 14],
            content="query = f'SELECT * FROM users WHERE u = {user}'\n",
            evidence_scope="diff",
            is_complete=False,
            total_lines=50,
        )
        # Create a large snippet that exceeds ~3,500 tokens (approx 12,000 characters)
        long_content = "def vulnerable_handler():\n" + ("    item = process_entry(data)\n" * 500)
        self.large_snippet = CodeDiffSnippet(
            repository="repo",
            commit_sha="a1b2c3d4e5f67890123456789012345678901234",
            file_path="src/heavy/processor.py",
            diff_side="NEW",
            lines=list(range(1, 502)),
            content=long_content,
            evidence_scope="diff",
            is_complete=False,
            total_lines=600,
        )

    def test_context_builder_calculate_safe_input_budget(self):
        builder = ContextBuilder()
        # 4096 window, 768 output, 64 margin, 300 overhead
        budget = builder.calculate_safe_input_budget(
            context_window=4096,
            max_output_tokens=768,
            safety_margin=64,
            overhead_tokens=300,
        )
        self.assertEqual(budget, 4096 - 768 - 64 - 300)
        self.assertEqual(budget, 2964)

        # Budget cannot be negative (fail-closed zero floor)
        exhausted = builder.calculate_safe_input_budget(
            context_window=1000,
            max_output_tokens=900,
            safety_margin=150,
            overhead_tokens=100,
        )
        self.assertEqual(exhausted, 0)

    def test_quality_agent_with_ollama_fails_closed_when_context_exceeds_local_budget(self):
        mock_transport = MagicMock()
        ollama_provider = OllamaProvider(
            context_tokens=4096,
            max_output_tokens=768,
            transport=mock_transport,
        )
        agent = CodeQualityAgent(
            provider=ollama_provider,
            model="qwen3.5:4b-q4_K_M",
        )

        # Large snippet should exceed the ~2900 token safe budget of local Ollama
        with self.assertRaises(ContextBudgetExceededError) as ctx:
            agent.run(
                project_id="test-proj",
                draft=self.draft,
                snippets=[self.large_snippet],
                rules=[self.rule],
            )

        self.assertIn("Context budget exceeded for code", str(ctx.exception))
        # Critical verification: network transport was never called
        mock_transport.assert_not_called()

    def test_quality_agent_with_groq_accepts_context_exceeding_ollama_limit(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "findings": [
                                    {
                                        "id": "CQ-001",
                                        "rule_id": "SEC-001",
                                        "claim": "SQL injection in query",
                                        "severity": "critical",
                                        "evidence_ids": ["a1b2c3d4e5f67890123456789012345678901234"],
                                        "commit_sha": "a1b2c3d4e5f67890123456789012345678901234",
                                        "file_path": "src/heavy/processor.py",
                                        "line": 1,
                                        "confidence": 0.95,
                                        "requires_verification": True,
                                        "claim_basis": "visible_code",
                                    }
                                ]
                            }),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4200, "completion_tokens": 120},
            },
        })
        groq_provider = GroqProvider(
            api_key="gsk_valid_key",
            model="llama-3.3-70b-versatile",
            allow_remote_ai=True,
            context_tokens=131072,
            max_output_tokens=8192,
            transport=mock_transport,
        )
        agent = CodeQualityAgent(
            provider=groq_provider,
            model="llama-3.3-70b-versatile",
        )

        # Same large snippet that broke Ollama succeeds under Groq's 131k budget
        findings = agent.run(
            project_id="test-proj",
            draft=self.draft,
            snippets=[self.large_snippet],
            rules=[self.rule],
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].id, "CQ-001")
        mock_transport.assert_called_once()

        # Verify ContextBuilder was executed before provider call
        sent_body = mock_transport.call_args[1]["json"]
        user_message_content = json.loads(sent_body["messages"][1]["content"])
        self.assertIn("context_metadata", user_message_content)
        meta = user_message_content["context_metadata"]
        self.assertEqual(meta["context_window_tokens"], 131072)
        self.assertGreater(meta["budget_tokens"], 4096)

    def test_quality_agent_with_mistral_accepts_context_and_executes_context_builder(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"findings": []}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        })
        mistral_provider = MistralProvider(
            api_key="mistral_key",
            model="mistral-large-latest",
            allow_remote_ai=True,
            context_tokens=131072,
            max_output_tokens=8192,
            transport=mock_transport,
        )
        agent = CodeQualityAgent(
            provider=mistral_provider,
            model="mistral-large-latest",
        )

        findings = agent.run(
            project_id="test-proj",
            draft=self.draft,
            snippets=[self.small_snippet],
            rules=[self.rule],
        )
        self.assertEqual(findings, [])
        mock_transport.assert_called_once()
        sent_body = mock_transport.call_args[1]["json"]
        user_payload = json.loads(sent_body["messages"][1]["content"])
        self.assertIn("code_snippets", user_payload)
        self.assertIn("context_metadata", user_payload)

    def test_quality_agent_fails_closed_when_context_exceeds_configured_remote_window(self):
        # Configure a small explicit window on GroqProvider
        mock_transport = MagicMock()
        tiny_groq = GroqProvider(
            api_key="gsk_key",
            model="llama-3.3-70b-versatile",
            allow_remote_ai=True,
            context_tokens=1000,
            max_output_tokens=500,
            transport=mock_transport,
        )
        agent = CodeQualityAgent(
            provider=tiny_groq,
            model="llama-3.3-70b-versatile",
        )

        with self.assertRaises(ContextBudgetExceededError):
            agent.run(
                project_id="test-proj",
                draft=self.draft,
                snippets=[self.large_snippet],
                rules=[self.rule],
            )

        mock_transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
