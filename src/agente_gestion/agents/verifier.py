"""VerifierAgent: selectively verifies candidate findings using independent inspection."""

from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.application.validation import SemanticValidator


class VerificationItemSchema(BaseModel):
    """Schema for individual verified claim."""
    finding_id: str = Field(description="Exact ID of the candidate finding being verified")
    verdict: str = Field(description="Verification verdict: confirmed, rejected, or insufficient")
    reason: str = Field(description="Detailed technical reason justifying the verdict")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Corroborating evidence IDs cited (must be a subset of finding evidence IDs)"
    )


class VerifierOutputSchema(BaseModel):
    """Structured output schema for VerifierAgent."""
    verifications: list[VerificationItemSchema] = Field(
        default_factory=list,
        description="List of independent finding verifications"
    )


class VerifierAgent:
    """
    Explicit verifier agent.
    Performs independent selective verification of candidate findings.
    Uses configurable model, defaults to gemini-2.5-flash.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: Optional[str] = None,
        coordinator: Optional[StageInvocationCoordinator] = None,
    ):
        self.provider = provider
        self.model = model or getattr(provider, "model", "gemini-2.5-flash")
        self.coordinator = coordinator or StageInvocationCoordinator()

    def run(
        self,
        project_id: str,
        findings: list[CandidateFinding],
        snippets: list[CodeDiffSnippet],
        rules: Optional[list[QualityRule]] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> list[FindingVerification]:
        if not findings:
            return []

        valid_ev_ids = set(evidence_ids) if evidence_ids else set()

        instructions = (
            "Eres el agente Verificador Independiente de Codigo y Calidad. "
            "Tu funcion es evaluar con criterio adversario y riguroso cada hallazgo candidato.\n"
            "REGLAS ESTRICTAS:\n"
            "- Cada verificacion debe usar el finding_id EXACTO del hallazgo evaluado.\n"
            "- No confirmes automaticamente ningun hallazgo candidato; exige justificacion tecnica objetiva.\n"
            "- Los evidence_ids deben ser un subconjunto estricto de las evidencias asociadas al hallazgo candidato.\n"
            "- Los veredictos validos son: 'confirmed', 'rejected', 'insufficient'.\n"
        )

        evidences_payload: dict[str, Any] = {
            "project_id": project_id,
            "findings": [f.model_dump() for f in findings],
            "code_snippets": [s.model_dump() for s in snippets],
            "rules": [r.model_dump() for r in rules] if rules else [],
            "available_evidence_ids": list(valid_ev_ids),
        }

        request = GenerationRequest(
            role="verificador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="verifier_output_v1",
            json_schema=VerifierOutputSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=1024,
            timeout_seconds=220.0,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            raw_verifs = data.get("verifications", [])
            parsed_verifs: list[FindingVerification] = []
            for item in raw_verifs:
                raw_verdict = item.get("verdict", "insufficient").lower()
                try:
                    verdict_enum = VerificationVerdict(raw_verdict)
                except ValueError:
                    verdict_enum = VerificationVerdict.insufficient

                parsed_verifs.append(
                    FindingVerification(
                        finding_id=item.get("finding_id", ""),
                        verdict=verdict_enum,
                        reason=item.get("reason", ""),
                        verified_by="verifier_agent",
                        evidence_ids=item.get("evidence_ids", []),
                    )
                )

            return SemanticValidator.validate_finding_verifications(
                verifications=parsed_verifs,
                candidate_findings=findings,
                available_evidence_ids=valid_ev_ids,
            )

        _, validated_dict = self.coordinator.invoke_stage(
            stage="verifier",
            provider=self.provider,
            request=request,
            validator=validate_payload,
        )

        raw_verifs = validated_dict.get("verifications", [])
        result_verifs: list[FindingVerification] = []
        for item in raw_verifs:
            raw_v = item.get("verdict", "insufficient").lower()
            try:
                verdict_enum = VerificationVerdict(raw_v)
            except ValueError:
                verdict_enum = VerificationVerdict.insufficient

            result_verifs.append(
                FindingVerification(
                    finding_id=item["finding_id"],
                    verdict=verdict_enum,
                    reason=item.get("reason", ""),
                    verified_by="verifier_agent",
                    evidence_ids=item.get("evidence_ids", []),
                )
            )

        return result_verifs
