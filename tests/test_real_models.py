"""Opt-in integration tests against real local Ollama and remote Gemini models.

These tests are separated from deterministic suites and only run when
RUN_REAL_LLM_TESTS=1 is explicitly set in the environment.
"""

import os
import unittest
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.domain.llm import GenerationRequest


class RealModelsTestCase(unittest.TestCase):
    def setUp(self):
        if os.getenv("RUN_REAL_LLM_TESTS") != "1":
            self.skipTest("Skipping real LLM tests. Set RUN_REAL_LLM_TESTS=1 to run.")

    def test_real_ollama_local_smoke(self):
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
        provider = OllamaProvider(base_url=base_url, model="qwen3.5:2b-q4_K_M")

        req = GenerationRequest(
            role="elaborador",
            instructions="Responde brevemente con un saludo.",
            evidences={"ping": "pong"},
            model="qwen3.5:2b-q4_K_M",
        )
        res = provider.generate(req)
        self.assertIsNotNone(res.status)

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
