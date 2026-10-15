"""ReportAgent: synthesizes final weekly report and explains advisory recommendation constrained by deterministic policy."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import DeterministicTaskMetrics
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import MonitoringDraft, FinalReport, HumanDecisionState
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.application.validation import SemanticValidator


class ReportExplanationSchema(BaseModel):
    """Output schema for ReportAgent. Metric fields are never computed by the model."""
    summary: str = Field(description="Synthesized objective weekly executive summary")
    recommendation: str = Field(
        description="Advisory recommendation constrained strictly by backend policy evaluation"
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable next steps proposed for human manager review"
    )


class ReportAgent:
    """
    Explicit aprobador / report synthesis agent.
    Synthesizes findings, drafts, and policy results into an advisory final report.
    Uses gemini-3.5-flash-lite by default.
    Enforces that recommendation is strictly constrained by deterministic backend policy.
    Final metric counts are supplied authoritatively by backend, never recalculated by LLM.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: str = "gemini-3.5-flash-lite",
        coordinator: Optional[StageInvocationCoordinator] = None,
    ):
        self.provider = provider
        self.model = model
        self.coordinator = coordinator or StageInvocationCoordinator()

    def run(
        self,
        project_id: str,
        cutoff_datetime: datetime,
        metrics: DeterministicTaskMetrics,
        draft: MonitoringDraft,
        confirmed_findings: list[CandidateFinding],
        rejected_findings: list[CandidateFinding],
        insufficient_findings: list[CandidateFinding],
        pending_findings: list[CandidateFinding],
        policy_evaluations: PolicyEvaluationResult,
        ci_result: Optional[CIRunResult] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> FinalReport:
        instructions = (
            "Eres el agente Aprobador / Sintetizador de Reporte de Proyecto. "
            "Tu rol es estrictamente asesor. El backend ya determino las politicas y metricas objetivas.\n"
            "REGLAS ESTRICTAS:\n"
            f"- La accion recomendada por el backend segun politicas es: '{policy_evaluations.recommended_action}'.\n"
            "- Si alguna regla obligatoria fallo o es desconocida, NO puedes recomendar aprobacion favorable.\n"
            "- No recalcules ni alteres ninguna metrica numerica; seran insertadas por el backend.\n"
            "- El informe quedara pendiente de la decision final del gestor humano.\n"
        )

        evidences_payload: dict[str, Any] = {
            "project_id": project_id,
            "draft": draft.model_dump(),
            "policy_evaluations": policy_evaluations.model_dump(),
            "confirmed_findings_count": len(confirmed_findings),
            "pending_findings_count": len(pending_findings),
            "rejected_findings_count": len(rejected_findings),
            "insufficient_findings_count": len(insufficient_findings),
            "available_evidence_ids": evidence_ids or [],
        }

        request = GenerationRequest(
            role="aprobador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="report_synthesis_v1",
            json_schema=ReportExplanationSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=768,
        )

        valid_ev_ids = set(evidence_ids) if evidence_ids else set()

        def validate_payload(data: dict[str, Any]) -> list[str]:
            rec = data.get("recommendation", "")
            errors = SemanticValidator.validate_approval_recommendation(
                claimed_recommendation=rec,
                actual_policy_result=policy_evaluations,
            )
            if "evidence_ids" in data:
                seen_evs: set[str] = set()
                for eid in data.get("evidence_ids", []):
                    if eid in seen_evs:
                        errors.append(f"Duplicate referenced evidence ID in report: '{eid}'.")
                    seen_evs.add(eid)
                    if eid not in valid_ev_ids:
                        errors.append(
                            f"Report referenced evidence ID '{eid}' does not exist in context."
                        )
            return errors

        _, validated_dict = self.coordinator.invoke_stage(
            stage="report",
            provider=self.provider,
            request=request,
            validator=validate_payload,
        )

        # Assemble authoritative FinalReport with backend metrics and categorized findings
        return FinalReport(
            project_id=project_id,
            cutoff_datetime=cutoff_datetime,
            metrics=metrics,
            ci_result=ci_result,
            confirmed_findings=confirmed_findings,
            rejected_findings=rejected_findings,
            insufficient_findings=insufficient_findings,
            pending_findings=pending_findings,
            policy_evaluations=policy_evaluations,
            recommendation=validated_dict.get("recommendation", policy_evaluations.recommended_action),
            human_decision=HumanDecisionState.pending,
            summary=validated_dict.get("summary", draft.summary),
            recommendations=validated_dict.get("recommendations", []),
        )
