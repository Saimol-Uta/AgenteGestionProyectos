"""ProjectMonitoringAgent: drafts weekly status and highlights risks based on verified facts."""

import re
from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskCard, DeterministicTaskMetrics
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.report import MonitoringDraft, TaskStateClaim, DomainProposalRuleSet
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError, monitoring_choices
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.application.validation import SemanticValidator, ValidationError


class MonitoringDraftSchema(BaseModel):
    """Concise schema for LLM structured output. Metric fields are never computed by LLM."""

    summary: str = Field(description="Narrative progress summary")

    task_state_claims: list[TaskStateClaim] = Field(default_factory=list)

    risks: list[str] = Field(
        default_factory=list,
        description="Project delivery risks identified"
    )

    suggested_actions: list[str] = Field(
        default_factory=list,
        description="Concrete actions suggested for manager"
    )

    referenced_task_ids: list[str] = Field(
        ...,
        description="OBLIGATORIO: incluye el ID exacto de cada tarea mencionada en el análisis. Usa [] si no mencionas tareas."
    )

    evidence_ids: list[str] = Field(
        ...,
        description="OBLIGATORIO: incluye el ID exacto de cada tarea mencionada, además de otras evidencias citadas. Usa [] si no hay referencias."
    )

    ci_status_note: Optional[str] = Field(
        default=None,
        description="Observations regarding CI build and tests"
    )

class ProjectMonitoringAgent:
    """
    Explicit elaborador agent.
    Drafts progress assessments, risk items, and suggested actions.
    Uses gemini-3.5-flash-lite by default.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: str = "gemini-3.5-flash-lite",
        coordinator: Optional[StageInvocationCoordinator] = None,
        context_builder: Optional[ContextBuilder] = None,
        max_generation_tokens: int = 512,
    ):
        self.context_builder = context_builder or ContextBuilder()
        self.provider = provider
        self.model = model
        self.max_generation_tokens = max_generation_tokens
        self.coordinator = coordinator or StageInvocationCoordinator()

    def run(
        self,
        project_id: str,
        metrics: DeterministicTaskMetrics,
        tasks: list[TaskCard],
        ci_result: Optional[CIRunResult] = None,
        evidence_ids: Optional[list[str]] = None,
        source_is_complete: Optional[bool] = None,
        source_commit_sha: Optional[str] = None,
        proposal_rules: Optional[DomainProposalRuleSet] = None,
        task_evidence: Optional[list[EvidenceItem]] = None,
    ) -> MonitoringDraft:
        available_task_ids = {t.id for t in tasks}
        valid_ev_ids = (set(evidence_ids) if evidence_ids else set()) | available_task_ids

        instructions = (
            "Eres el agente Elaborador de Gestion de Proyectos. "
            "Tu tarea es redactar un borrador de estado semanal objetivo y conciso.\n"
            "REGLAS ESTRICTAS:\n"
            "- selection declara alcance relevante y cantidad omitida; si solo hay un ejemplo representativo no es una lista exhaustiva. Usa metrics para progreso global, no infieras detalles omitidos.\n"
            "- No inventes identificadores de tareas ni evidencias; cita unicamente IDs existentes en el contexto.\n"
            "- Las metricas de avance son calculadas de forma autoritativa por el backend; no calcules ni modifiques conteos finales.\n"
            "- Si el CI ha fallado, destaca el riesgo correspondiente.\n"
            "- TaskCard.state es autoritativo: solo state=en_curso (TaskState.in_progress) significa en curso; no inventes estados.\n"
            "- Toda tarea mencionada debe citarse con su ID en referenced_task_ids y evidence_ids.\n"
            "- Declara toda afirmacion de estado tambien en task_state_claims, sin contradecir el texto narrativo.\n"
            "- Repite el ID exacto al afirmar estados; no uses pronombres o frases de estado sin atribucion clara despues de mencionar varias tareas.\n"
        )

        capabilities = self.provider.get_capabilities()
        if (self.max_generation_tokens <= 0
                or self.max_generation_tokens > capabilities.max_output_tokens
                or self.max_generation_tokens + 64 >= capabilities.context_tokens):
            raise ContextBudgetExceededError("Monitoring output reservation exceeds configured provider limits")
        context = self.context_builder.build_monitoring_context(
            project_id, metrics, tasks, ci_result, sorted(valid_ev_ids), strict_budget=True,
            source_is_complete=source_is_complete, source_commit_sha=source_commit_sha,
        )
        evidences_payload = self.context_builder.runtime_payload(
            context, instructions, MonitoringDraftSchema.model_json_schema(), self.max_generation_tokens,
            capabilities.context_tokens, max_output_tokens=capabilities.max_output_tokens,
            request_fields={"role": "elaborador", "model": self.model, "schema_name": "monitoring_draft_v1"},
        )

        choices, limitations = monitoring_choices(project_id, tasks, evidences_payload["tasks"],
                                                 proposal_rules, task_evidence)
        schema = MonitoringDraftSchema.model_json_schema()
        if choices:
            optional_context = context.model_copy(deep=True)
            optional_context.data["proposal_choices"] = [dict(choice_id=f"choice-{i}",
                **choice.model_dump(mode="json")) for i, choice in enumerate(choices)]
            schema["properties"]["selected_choice_ids"] = {
                "type": "array", "items": {"type": "string"},
                "description": "Select only proposal_choices IDs, at most one per task/field; no permission granted."}
            try:
                evidences_payload = self.context_builder.runtime_payload(
                    optional_context, instructions, schema, self.max_generation_tokens, capabilities.context_tokens,
                    max_output_tokens=capabilities.max_output_tokens,
                    request_fields={"role": "elaborador", "model": self.model, "schema_name": "monitoring_draft_v1"})
            except ContextBudgetExceededError:
                choices = []
                limitations.append("proposal_context_budget")
                schema = MonitoringDraftSchema.model_json_schema()

        selected_ids = {row["id"] for row in evidences_payload["tasks"]}
        omitted_tasks = [task for task in tasks if task.id not in selected_ids]
        tasks = [task for task in tasks if task.id in selected_ids]
        available_task_ids = selected_ids
        valid_ev_ids = set(evidences_payload["available_evidence_ids"])
        if evidences_payload["ci_result"] is None:
            ci_result = None

        request = GenerationRequest(
            role="elaborador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="monitoring_draft_v1",
            json_schema=schema,
            model=self.model,
            max_generation_tokens=self.max_generation_tokens,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            try:
                MonitoringDraftSchema.model_validate(data)
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

            narrative = "\n".join([candidate.summary, *candidate.risks, *candidate.suggested_actions,
                                   candidate.ci_status_note or ""])
            for task in omitted_tasks:
                if any(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", narrative, re.IGNORECASE)
                       for name in (task.id, task.title)):
                    return ["Narrative references an omitted task outside selected evidence; request additional context."]

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

        selected = validated_dict.get("selected_choice_ids", [])
        changes = []
        lookup = {f"choice-{i}": c for i, c in enumerate(choices)}
        if type(selected) is not list or any(type(i) is not str or i not in lookup for i in selected):
            limitations.append("unknown_choice")
        else:
            changes = [lookup[i] for i in selected]
            keys = [(c.task_id, c.field) for c in changes]
            if len(set(keys)) != len(keys):
                limitations.append("conflicting_selection")
                changes = []

        return MonitoringDraft(
            requested_changes=changes, proposal_limitations=sorted(set(limitations)),
            summary=validated_dict.get("summary", ""),
            task_state_claims=validated_dict.get("task_state_claims", []),
            risks=validated_dict.get("risks", []),
            suggested_actions=validated_dict.get("suggested_actions", []),
            referenced_task_ids=validated_dict.get("referenced_task_ids", []),
            evidence_ids=validated_dict.get("evidence_ids", []),
            ci_status_note=validated_dict.get("ci_status_note"),
        )
