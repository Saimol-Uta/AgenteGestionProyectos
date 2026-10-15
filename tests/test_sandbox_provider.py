"""Regression tests for deterministic offline provider contracts."""

import unittest

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.domain.llm import GenerationRequest, GenerationStatus


class SandboxProviderTests(unittest.TestCase):
    def test_monitoring_draft_does_not_invent_task_or_evidence_ids(self) -> None:
        request = GenerationRequest(
            role="elaborador",
            instructions="Draft an objective monitoring summary.",
            evidences={"tasks": [], "available_evidence_ids": []},
            schema_name="monitoring_draft_v1",
            json_schema={"type": "object"},
            model="sandbox-monitoring",
            max_generation_tokens=256,
        )

        result = FakeProvider(model="sandbox-monitoring").generate(request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertEqual(result.structured_data["referenced_task_ids"], [])
        self.assertEqual(result.structured_data["evidence_ids"], [])


if __name__ == "__main__":
    unittest.main()
