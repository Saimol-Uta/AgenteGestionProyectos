"""Opt-in integration test against the real remote Gemini model.

This test is separated from deterministic suites and only runs when
RUN_REAL_LLM_TESTS=1 is explicitly set in the environment.
"""

import os
import unittest
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.domain.llm import GenerationRequest


class RealModelsTestCase(unittest.TestCase):
    def setUp(self):
        if os.getenv("RUN_REAL_LLM_TESTS") != "1":
            self.skipTest("Skipping real LLM tests. Set RUN_REAL_LLM_TESTS=1 to run.")

    def test_real_gemini_remote_smoke(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.skipTest("GEMINI_API_KEY not set.")

        provider = GeminiProvider(api_key=api_key, model="gemini-2.5-flash")
        req = GenerationRequest(
            role="verificador",
            instructions="Evalua si 1 == 1.",
            evidences={"eval": "true"},
            model="gemini-2.5-flash",
        )
        res = provider.generate(req)
        self.assertIsNotNone(res.status)


if __name__ == "__main__":
    unittest.main()
