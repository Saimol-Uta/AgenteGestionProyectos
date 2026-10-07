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
from agente_gestion.agents.context import ContextBuilder
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
        timeout_seconds: float = 120.0,
        context_builder: Optional[ContextBuilder] = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.context_builder = context_builder or ContextBuilder()
        self.provider = provider
        self.model = model or getattr(provider, "model", "gemini-2.5-flash")
        self.coordinator = coordinator or StageInvocationCoordinator()
        self.timeout_seconds = timeout_seconds

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
            "- No confirmes asociaciones incompatibles de politica; cobertura desconocida no demuestra ausencia de pruebas.\n"
            "- Los veredictos validos son: 'confirmed', 'rejected', 'insufficient'.\n"
        )

        instructions += (
            "\nLIMITES DE EVIDENCIA:\n"
            "- No infieras codigo fuera de las lineas visibles; el fin del fragmento no es el fin del archivo.\n"
            "- snippet, diff y truncated son parciales; no afirman ausencia ni implementacion incompleta fuera del rango visible.\n"
            "- Solo full_file con is_complete=true y truncated=false permite conclusiones de ausencia dentro del archivo completo.\n"
            "- Si faltan lineas necesarias, declara evidencia insuficiente y solicita contexto adicional; no reportes ausencia como defecto.\n"
            "- El verificador debe usar insufficient cuando confirmar dependa de contenido no visible.\n"
        )

        context = self.context_builder.build_verifier_context(
            project_id, findings, snippets, rules or [], sorted(valid_ev_ids), strict_budget=True,
        )
        evidences_payload = self.context_builder.runtime_payload(
            context, instructions, VerifierOutputSchema.model_json_schema(), 1024,
            self.provider.get_capabilities().context_tokens,
        )

        request = GenerationRequest(
            role="verificador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="verifier_output_v1",
            json_schema=VerifierOutputSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=1024,
            timeout_seconds=self.timeout_seconds,
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
                available_snippets=snippets,
                rules=rules,
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
