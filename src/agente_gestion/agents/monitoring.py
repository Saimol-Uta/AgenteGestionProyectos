"""ProjectMonitoringAgent: drafts weekly status and highlights risks based on verified facts."""

from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskCard, DeterministicTaskMetrics
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.report import MonitoringDraft, TaskStateClaim
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.application.validation import SemanticValidator, ValidationError


class MonitoringDraftSchema(BaseModel):
    """Concise schema for LLM structured output. Metric fields are never computed by LLM."""
    summary: str = Field(description="Narrative progress summary")
    task_state_claims: list[TaskStateClaim] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list, description="Project delivery risks identified")
    suggested_actions: list[str] = Field(default_factory=list, description="Concrete actions suggested for manager")
    referenced_task_ids: list[str] = Field(default_factory=list, description="Explicit task IDs cited")
    evidence_ids: list[str] = Field(default_factory=list, description="Explicit evidence IDs cited")
    ci_status_note: Optional[str] = Field(default=None, description="Observations regarding CI build and tests")


class ProjectMonitoringAgent:
    """
    Explicit elaborador agent.
    Drafts progress assessments, risk items, and suggested actions.
    Uses qwen3.5:2b-q4_K_M by default.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: str = "qwen3.5:2b-q4_K_M",
        coordinator: Optional[StageInvocationCoordinator] = None,
        context_builder: Optional[ContextBuilder] = None,
    ):
        self.context_builder = context_builder or ContextBuilder()
        self.provider = provider
        self.model = model
        self.coordinator = coordinator or StageInvocationCoordinator()

    def run(
        self,
        project_id: str,
        metrics: DeterministicTaskMetrics,
        tasks: list[TaskCard],
        ci_result: Optional[CIRunResult] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> MonitoringDraft:
        available_task_ids = {t.id for t in tasks}
        valid_ev_ids = (set(evidence_ids) if evidence_ids else set()) | available_task_ids

        instructions = (
            "Eres el agente Elaborador de Gestion de Proyectos. "
            "Tu tarea es redactar un borrador de estado semanal objetivo y conciso.\n"
            "REGLAS ESTRICTAS:\n"
            "- No inventes identificadores de tareas ni evidencias; cita unicamente IDs existentes en el contexto.\n"
            "- Las metricas de avance son calculadas de forma autoritativa por el backend; no calcules ni modifiques conteos finales.\n"
            "- Si el CI ha fallado, destaca el riesgo correspondiente.\n"
            "- TaskCard.state es autoritativo: solo state=en_curso (TaskState.in_progress) significa en curso; no inventes estados.\n"
            "- Toda tarea mencionada debe citarse con su ID en referenced_task_ids y evidence_ids.\n"
            "- Declara toda afirmacion de estado tambien en task_state_claims, sin contradecir el texto narrativo.\n"
            "- Repite el ID exacto al afirmar estados; no uses pronombres o frases de estado sin atribucion clara despues de mencionar varias tareas.\n"
        )

        context = self.context_builder.build_monitoring_context(
            project_id, metrics, tasks, ci_result, sorted(valid_ev_ids), strict_budget=True,
        )
        evidences_payload = self.context_builder.runtime_payload(
            context, instructions, MonitoringDraftSchema.model_json_schema(), 512,
            self.provider.get_capabilities().context_tokens,
        )

        request = GenerationRequest(
            role="elaborador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="monitoring_draft_v1",
            json_schema=MonitoringDraftSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=512,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            try:
                candidate = MonitoringDraft(
                    summary=data.get("summary", ""),
                    task_state_claims=data.get("task_state_claims", []),
                    risks=data.get("risks", []),
                    suggested_actions=data.get("suggested_actions", []),
                    referenced_task_ids=data.get("referenced_task_ids", []),
                    evidence_ids=data.get("evidence_ids", []),
                    ci_status_note=data.get("ci_status_note"),
                )
            except Exception as e:
                return [f"Schema validation error: {str(e)}"]

            return SemanticValidator.validate_monitoring_draft(
                draft=candidate,
                available_task_ids=available_task_ids,
                metrics=metrics,
                ci_result=ci_result,
                available_evidence_ids=valid_ev_ids,
                tasks=tasks,
            )

        _, validated_dict = self.coordinator.invoke_stage(
            stage="monitoring",
            provider=self.provider,
            request=request,
            validator=validate_payload,
        )

        return MonitoringDraft(
            summary=validated_dict.get("summary", ""),
            task_state_claims=validated_dict.get("task_state_claims", []),
            risks=validated_dict.get("risks", []),
            suggested_actions=validated_dict.get("suggested_actions", []),
            referenced_task_ids=validated_dict.get("referenced_task_ids", []),
            evidence_ids=validated_dict.get("evidence_ids", []),
            ci_status_note=validated_dict.get("ci_status_note"),
        )
