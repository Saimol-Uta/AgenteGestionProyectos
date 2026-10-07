"""CodeQualityAgent: reviews draft and code changes to report candidate defect findings."""

import json
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator

from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding, FindingSeverity
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.application.generation import StageInvocationCoordinator
from agente_gestion.application.validation import SemanticValidator


class GeneratedFindingSchema(BaseModel):
    """Schema strictly defining required fields for generated findings from LLM."""
    id: str = Field(min_length=1, description="Unique finding identifier (e.g. 'CQ-001')")
    rule_id: Optional[str] = Field(min_length=1, description="Existing applicable policy ID or explicit null; this key is required")
    claim: str = Field(min_length=1, description="Concrete technical assertion or defect description")
    severity: FindingSeverity = Field(description="Severity: low, medium, high, critical")
    evidence_ids: list[str] = Field(min_length=1, description="Nonempty evidence IDs from code context")
    commit_sha: str = Field(min_length=1, description="Exact commit SHA")
    file_path: str = Field(min_length=1, description="Exact file path")
    line: int = Field(description="Exact line number within snippet")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    requires_verification: bool = Field(default=True, description="Must be true for candidate findings")
    claim_basis: Literal["visible_code", "file_absence", "policy_metric"] = "visible_code"
    metric_key: Optional[str] = None
    observed_value: Optional[float] = None
    category: Optional[str] = Field(default="otros", description="Defect category")
    evidence: Optional[str] = Field(default="", description="Snippet excerpt")
    suggested_fix: Optional[str] = Field(default="", description="Suggested fix proposal")

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def validate_nonempty_evidence_ids(cls, v: Any) -> list[str]:
        if not v or not isinstance(v, list):
            raise ValueError("evidence_ids must be a non-empty list of strings")
        cleaned = [str(x).strip() for x in v if str(x).strip()]
        if not cleaned:
            raise ValueError("evidence_ids cannot be empty")
        return cleaned


class CodeFindingsSchema(BaseModel):
    """Concise output schema for candidate findings from code review."""
    findings: list[GeneratedFindingSchema] = Field(
        default_factory=list,
        description="Candidate findings identified in reviewed diff snippets"
    )


class CodeQualityAgent:
    """
    Explicit revisor agent.
    Reviews monitoring draft, quality criteria, and bounded code diff snippets.
    Produces candidate findings only (candidate findings are never automatically confirmed).
    Uses qwen3.5:4b-q4_K_M by default.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: str = "qwen3.5:4b-q4_K_M",
        coordinator: Optional[StageInvocationCoordinator] = None,
        timeout_seconds: float = 120.0,
        context_builder: Optional[ContextBuilder] = None,
    ):
        self.context_builder = context_builder or ContextBuilder()
        self.provider = provider
        self.model = model
        self.coordinator = coordinator or StageInvocationCoordinator()
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        project_id: str,
        draft: MonitoringDraft,
        snippets: list[CodeDiffSnippet],
        criteria: Optional[list[str]] = None,
        rules: Optional[list[QualityRule]] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> list[CandidateFinding]:
        valid_rules = {r.id for r in rules} if rules else set()
        code_ev_ids = set(evidence_ids) if evidence_ids else set()
        for s in snippets:
            code_ev_ids.add(s.commit_sha)

        rules_list_str = ", ".join(sorted(valid_rules)) if valid_rules else "ninguna"
        ev_list_str = ", ".join(sorted(code_ev_ids)) if code_ev_ids else "ninguna"

        example_rule = next((rule.id for rule in rules or []
                             if rule.source == "review" or rule.metric_key == "open_critical_findings"), None)
        example_ev = sorted(code_ev_ids)[0] if code_ev_ids else (snippets[0].commit_sha if snippets else "a1b2c3d4e5f6")
        example_sha = snippets[0].commit_sha if snippets else "a1b2c3d4e5f6"
        example_file = snippets[0].file_path if snippets else "src/auth/service.py"
        example_line = snippets[0].lines[0] if (snippets and snippets[0].lines) else 12

        instructions = (
            "Eres el agente Revisor de Calidad de Codigo.\n"
            "Revisa los fragmentos de diff y el borrador de monitoreo reportando hallazgos tecnicos.\n"
            "CONTRATO ESTRICTO:\n"
            "- Cada hallazgo es estrictamente CANDIDATO (requires_verification: true); no asumas confirmacion.\n"
            "- Cada hallazgo DEBE incluir OBLIGATORIAMENTE:\n"
            "  * id: identificador unico no vacio (ej. 'CQ-001').\n"
            f"  * rule_id: ID exacto de la regla de calidad aplicable de la lista [{rules_list_str}]. Usa null explicito si ninguna regla aplica; nunca vacio.\n"
            "  * claim: afirmacion tecnica concreta del defecto identificado. NUNCA vacio.\n"
            "  * severity: 'low', 'medium', 'high', o 'critical'. NUNCA null.\n"
            f"  * evidence_ids: lista NO vacia citando los IDs de evidencia de codigo disponibles [{ev_list_str}]. NUNCA vacia ni null.\n"
            "  * commit_sha: commit SHA exacto del fragmento analizado.\n"
            "  * file_path: ruta de archivo exacta del fragmento analizado.\n"
            "  * line: numero de linea exacto dentro de las lineas del fragmento.\n"
            "  * confidence: flotante entre 0.0 y 1.0.\n"
            "  * requires_verification: booleano (true).\n"
            "- Prohibido inventar o autoasignar IDs de reglas o evidencias inexistentes.\n\n"
            "EJEMPLO DE ESQUEMA VALIDO:\n"
            "{\n"
            '  "findings": [\n'
            '    {\n'
            '      "id": "CQ-001",\n'
            f'      "rule_id": {json.dumps(example_rule)},\n'
            '      "claim": "Consulta SQL concatenada con entrada de usuario susceptible a inyeccion SQL",\n'
            '      "severity": "critical",\n'
            f'      "evidence_ids": ["{example_ev}"],\n'
            f'      "commit_sha": "{example_sha}",\n'
            f'      "file_path": "{example_file}",\n'
            f'      "line": {example_line},\n'
            '      "confidence": 0.95,\n'
            '      "requires_verification": true,\n'
            '      "category": "inyeccion_sql",\n'
            '      "evidence": "query = f\\"SELECT * FROM users WHERE u = \'{user}\'\\"",\n'
            '      "suggested_fix": "Usar consultas parametrizadas"\n'
            '    }\n'
            '  ]\n'
            "}\n"
        )

        instructions += (
            "- claim_basis: visible_code, file_absence o policy_metric; nunca disfraces ausencia como defecto visible.\n"
            "- Una regla CI de cobertura no aplica a defectos de implementacion; usa rule_id=null si no hay regla aplicable.\n"
            "- policy_metric exige metric_key y observed_value respaldados por hechos reportados, no inferidos de codigo.\n"
            "\nLIMITES DE EVIDENCIA:\n"
            "- No infieras codigo fuera de las lineas visibles; el fin del fragmento no es el fin del archivo.\n"
            "- snippet, diff y truncated son parciales; no afirman ausencia ni implementacion incompleta fuera del rango visible.\n"
            "- Solo full_file con is_complete=true y truncated=false permite conclusiones de ausencia dentro del archivo completo.\n"
            "- Si faltan lineas necesarias, declara evidencia insuficiente y solicita contexto adicional; no reportes ausencia como defecto.\n"
            "- El verificador debe usar insufficient cuando confirmar dependa de contenido no visible.\n"
        )

        context = self.context_builder.build_code_context(
            project_id, draft, criteria or [], snippets, rules, sorted(code_ev_ids), strict_budget=True,
        )
        capabilities = self.provider.get_capabilities()
        evidences_payload = self.context_builder.runtime_payload(
            context, instructions, CodeFindingsSchema.model_json_schema(), 768,
            capabilities.context_tokens, max_output_tokens=capabilities.max_output_tokens,
            request_fields={"role": "revisor", "model": self.model, "schema_name": "code_findings_v1", "timeout_seconds": self.timeout_seconds},
        )

        request = GenerationRequest(
            role="revisor",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="code_findings_v1",
            json_schema=CodeFindingsSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=768,
            timeout_seconds=self.timeout_seconds,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            raw_findings = data.get("findings", [])
            parsed_findings: list[CandidateFinding] = []
            for item in raw_findings:
                try:
                    gen_item = GeneratedFindingSchema.model_validate(item)
                    parsed_findings.append(CandidateFinding.model_validate(gen_item.model_dump()))
                except Exception as e:
                    return [f"Malformed finding structure: {str(e)}"]

            return SemanticValidator.validate_candidate_findings(
                findings=parsed_findings,
                available_snippets=snippets,
                valid_rule_ids=valid_rules,
                available_evidence_ids=code_ev_ids,
                rules=rules or [],
            )

        _, validated_dict = self.coordinator.invoke_stage(
            stage="quality",
            provider=self.provider,
            request=request,
            validator=validate_payload,
        )

        raw_findings = validated_dict.get("findings", [])
        return [CandidateFinding.model_validate(item) for item in raw_findings]
