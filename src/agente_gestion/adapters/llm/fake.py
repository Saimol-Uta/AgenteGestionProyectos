"""Reusable deterministic simulated provider for testing and offline E2E verification."""

import json
from typing import Any, Optional
from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    ModelCapabilities,
)
from agente_gestion.ports.llm import LLMProviderPort


class FakeProvider(LLMProviderPort):
    """
    Deterministic simulated provider. Clearly marked as simulated.
    Can operate in autonomous role-based mock mode or pop from an explicit response queue.
    """

    is_simulated: bool = True
    provider_name: str = "fake_simulated"

    def __init__(
        self,
        model: str = "fake-model-v1",
        context_tokens: int = 8192,
        max_output_tokens: int = 2048,
    ):
        self.model = model
        self.context_tokens = context_tokens
        self.max_output_tokens = max_output_tokens
        self._queue: list[GenerationResult] = []

    def enqueue_responses(self, responses: list[GenerationResult]) -> None:
        """Enqueues predetermined results to be popped sequentially on subsequent generate calls."""
        self._queue.extend(responses)

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            context_tokens=self.context_tokens,
            max_output_tokens=self.max_output_tokens,
            supports_json_schema=True,
            supports_tools=False,
            supports_streaming=False,
            reasoning_options=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        # If explicit responses are queued, pop and return the next one
        if self._queue:
            queued = self._queue.pop(0)
            return queued

        # Default role-based deterministic outputs
        role = request.role.lower()

        if "elaborador" in role or "monitoring" in role:
            # Deterministic draft
            tasks_in_ev = request.evidences.get("tasks", [])
            task_ids = [t.get("id") for t in tasks_in_ev[:2]] if tasks_in_ev else ["T1", "T2"]
            available_ev = request.evidences.get("available_evidence_ids", [])
            ev_ids = available_ev[:2] if available_ev else ["ev-1"]
            structured = {
                "summary": "Resumen semanal: el avance progresa de acuerdo con las tareas planificadas.",
                "risks": ["Riesgo potencial de sincronizacion"],
                "suggested_actions": ["Continuar monitoreo"],
                "referenced_task_ids": task_ids,
                "evidence_ids": ev_ids,
            }
        elif "revisor" in role or "code" in role or "quality" in role:
            # Deterministic candidate findings satisfying strict contract
            rules_in_ev = request.evidences.get("rules", [])
            applicable_rule_id = None
            for r in rules_in_ev:
                if r.get("source") == "review" or r.get("metric_key") == "open_critical_findings":
                    applicable_rule_id = r.get("id")
                    break

            snippets_in_ev = request.evidences.get("code_snippets", [])
            file_path = snippets_in_ev[0].get("file_path", "src/service.py") if snippets_in_ev else "src/service.py"
            commit_sha = snippets_in_ev[0].get("commit_sha", "c0ffee1") if snippets_in_ev else "c0ffee1"
            line = snippets_in_ev[0].get("lines", [15])[0] if snippets_in_ev and snippets_in_ev[0].get("lines") else 15
            available_ev = request.evidences.get("available_evidence_ids", [])
            if commit_sha in available_ev:
                evidence_ids = [commit_sha]
            elif available_ev:
                evidence_ids = [available_ev[0]]
            else:
                evidence_ids = [commit_sha]

            structured = {
                "findings": [
                    {
                        "id": "f-fake-1",
                        "rule_id": applicable_rule_id,
                        "claim": "Riesgo de division por cero en calculo de metricas",
                        "severity": "high",
                        "evidence_ids": evidence_ids,
                        "commit_sha": commit_sha,
                        "file_path": file_path,
                        "line": line,
                        "confidence": 0.85,
                        "requires_verification": True,
                        "category": "division_cero",
                        "evidence": "return 1 / total",
                        "suggested_fix": "if total == 0: return 0",
                        "suggested_code": "return 0 if total == 0 else 1 / total",
                        "improvement_summary": "Evita la división por cero y conserva el cálculo para valores válidos.",
                    }
                ]
            }
        elif "verificador" in role or "verifier" in role:
            # Deterministic finding verifications
            findings_in_ev = request.evidences.get("findings", [])
            verifications = []
            for f in findings_in_ev:
                fid = f.get("id", "f-fake-1")
                eids = f.get("evidence_ids", [])
                verifications.append({
                    "finding_id": fid,
                    "verdict": "confirmed",
                    "reason": "Technical inspection confirms defect in snippet.",
                    "verified_by": "verifier_gemini",
                    "evidence_ids": eids[:1] if eids else ["c0ffee1"],  # Subset of candidate finding's evidence
                })
            structured = {"verifications": verifications}
        elif "aprobador" in role or "report" in role:
            # Deterministic report synthesis: respect backend policy recommendation
            policy_ev = request.evidences.get("policy_evaluations", {})
            rec_action = policy_ev.get("recommended_action", "recomendar_aprobacion")
            structured = {
                "recommendation": rec_action,
                "summary": "El reporte semanal cuenta con metricas verificadas y politicas cumplidas.",
                "recommendations": ["Aprobar avance semanal" if rec_action == "recomendar_aprobacion" else "Revisar criterios de calidad"],
            }
        else:
            structured = {
                "summary": "Resultado simulado para rol no especifico.",
                "status": "ok",
            }

        return GenerationResult(
            status=GenerationStatus.complete,
            structured_data=structured,
            raw_text=json.dumps(structured),
            finish_reason="stop",
            provider=self.provider_name,
            model=request.model or self.model,
            duration_seconds=0.01,
            prompt_tokens=100,
            completion_tokens=50,
            request_ref=request.schema_name,
        )
