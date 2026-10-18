"""CodeQualityAgent: reviews draft and code changes to report candidate defect findings."""

import json
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator

from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding, FindingSeverity
from agente_gestion.domain.repositories import RepositoryIdentity, RepositoryProvenance
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.llm import GenerationRequest
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.agents.context import ContextBuilder, estimate_tokens_from_data
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
    suggested_code: str = Field(min_length=1, description="Syntactically coherent code example for human review")
    improvement_summary: str = Field(min_length=1, description="Breve resumen en español de la mejora propuesta")

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


class RepositoryScopedGeneratedFindingSchema(GeneratedFindingSchema):
    """Repository-mode output can state provenance, which is checked before binding."""

    repository_identity: Optional[RepositoryIdentity] = None
    provenance: Optional[RepositoryProvenance] = None


class RepositoryScopedCodeFindingsSchema(BaseModel):
    findings: list[RepositoryScopedGeneratedFindingSchema] = Field(
        default_factory=list,
        description="Candidate findings identified within one fixed repository scope",
    )


class CodeQualityAgent:
    """
    Explicit revisor agent.
    Reviews monitoring draft, quality criteria, and bounded code diff snippets.
    Produces candidate findings only (candidate findings are never automatically confirmed).
    Uses llama-3.3-70b-versatile by default.
    """

    def __init__(
        self,
        provider: LLMProviderPort,
        model: str = "llama-3.3-70b-versatile",
        coordinator: Optional[StageInvocationCoordinator] = None,
        timeout_seconds: float = 120.0,
        context_builder: Optional[ContextBuilder] = None,
        max_output_tokens: Optional[int] = None,
        safety_margin_tokens: int = 64,
    ):
        self.context_builder = context_builder or ContextBuilder()
        self.provider = provider
        self.model = model
        self.coordinator = coordinator or StageInvocationCoordinator()
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.safety_margin_tokens = safety_margin_tokens

    def run(
        self,
        project_id: str,
        draft: MonitoringDraft,
        snippets: list[CodeDiffSnippet],
        criteria: Optional[list[str]] = None,
        rules: Optional[list[QualityRule]] = None,
        evidence_ids: Optional[list[str]] = None,
        repository_scope: Optional[dict[str, Any]] = None,
        stage_name: Optional[str] = None,
    ) -> list[CandidateFinding]:
        valid_rules = {r.id for r in rules} if rules else set()
        code_ev_ids = set(evidence_ids) if evidence_ids else set()
        for s in snippets:
            code_ev_ids.add(s.commit_sha)

        output_schema = (
            RepositoryScopedCodeFindingsSchema
            if repository_scope is not None else CodeFindingsSchema
        )
        finding_schema = (
            RepositoryScopedGeneratedFindingSchema
            if repository_scope is not None else GeneratedFindingSchema
        )
        output_json_schema = output_schema.model_json_schema()

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
            "  * suggested_code: ejemplo de código concreto que corrige el hallazgo, compatible con el contexto visible. NUNCA vacio.\n"
            "  * improvement_summary: resumen breve de la mejora en español. NUNCA vacio. Nota: 'suggested_fix' NO sustituye a 'improvement_summary'. Ambos campos son obligatorios.\n"
            "- Prohibido inventar o autoasignar IDs de reglas o evidencias inexistentes.\n"
            "- No inventes defectos si el código es correcto. Si no encuentras defectos reales en los fragmentos visibles, devuelve una lista vacía: {\"findings\": []}.\n"
            "- Cita únicamente líneas y archivos visibles en los fragmentos provistos.\n\n"
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
            '      "suggested_fix": "Usar consultas parametrizadas",\n'
            '      "suggested_code": "cursor.execute(\\\"SELECT * FROM users WHERE u = ?\\\", (user,))",\n'
            '      "improvement_summary": "Parametriza la consulta para evitar inyección SQL."\n'
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

        if repository_scope is not None:
            identity = repository_scope.get("identity", {})
            instructions += (
                "\nALCANCE DE REPOSITORIO FIJADO POR EL BACKEND:\n"
                f"- Repositorio: {identity.get('owner', '')}/{identity.get('repo', '')} (id {identity.get('id', '')}).\n"
                f"- SHA objetivo inmutable: {repository_scope.get('target_commit_sha')}.\n"
                "- Usa exclusivamente los fragmentos, CI y evidencias de este alcance. No infieras otra identidad.\n"
                "- No emitas repository_identity ni provenance; el backend vincula la salida a evidencia validada.\n"
            )

        capabilities = self.provider.get_capabilities()
        output_tokens = min(self.max_output_tokens or 768, capabilities.max_output_tokens)
        safety_margin = self.safety_margin_tokens

        envelope_overhead = 512
        instructions_overhead = (
            estimate_tokens_from_data(instructions)
            + estimate_tokens_from_data(output_json_schema)
            + envelope_overhead
        )
        safe_input_budget = self.context_builder.calculate_safe_input_budget(
            context_window=capabilities.context_tokens,
            max_output_tokens=output_tokens,
            safety_margin=safety_margin,
            overhead_tokens=instructions_overhead,
        )
        if getattr(self.context_builder, "_explicit_default", False):
            safe_input_budget = min(self.context_builder.default_budget_tokens, safe_input_budget)

        context = self.context_builder.build_code_context(
            project_id,
            draft,
            criteria or [],
            snippets,
            rules,
            sorted(code_ev_ids),
            budget_tokens=safe_input_budget,
            strict_budget=True,
            repository_scope=repository_scope,
        )
        self.last_selection = context.data.get("selection", {})
        included_file_lines = {
            (s.get("file_path"), tuple(s.get("lines", [])))
            for s in context.data.get("code_snippets", [])
        }
        visible_snippets = [
            s for s in snippets
            if (s.file_path, tuple(s.lines)) in included_file_lines
        ] if included_file_lines else snippets
        available_ev_ids = set(context.data.get("evidence_ids", [])) or code_ev_ids

        evidences_payload = self.context_builder.runtime_payload(
            context,
            instructions,
            output_json_schema,
            output_tokens,
            capabilities.context_tokens,
            max_output_tokens=capabilities.max_output_tokens,
            request_fields={
                "role": "revisor",
                "model": self.model,
                "schema_name": "code_findings_v1",
                "timeout_seconds": self.timeout_seconds,
            },
            safety_margin_tokens=safety_margin,
        )

        request = GenerationRequest(
            role="revisor",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="code_findings_v1",
            json_schema=output_json_schema,
            model=self.model,
            max_generation_tokens=output_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            raw_findings = data.get("findings", [])
            parsed_findings: list[CandidateFinding] = []
            for item in raw_findings:
                try:
                    gen_item = finding_schema.model_validate(item)
                    if not gen_item.suggested_code.strip() or not gen_item.improvement_summary.strip():
                        return ["Malformed finding structure: Finding must include non-empty suggested_code and improvement_summary."]
                    parsed_findings.append(CandidateFinding.model_validate(gen_item.model_dump()))
                except Exception as e:
                    return [f"Malformed finding structure: {str(e)}"]

            return SemanticValidator.validate_candidate_findings(
                findings=parsed_findings,
                available_snippets=visible_snippets,
                valid_rule_ids=valid_rules,
                available_evidence_ids=available_ev_ids,
                rules=rules or [],
            )

        def build_repair_request(
            orig_req: GenerationRequest,
            parsed_data: dict[str, Any],
            val_errors: list[str],
        ) -> GenerationRequest:
            err_summary = "; ".join(val_errors)
            previous_draft_str = json.dumps(parsed_data, indent=2, ensure_ascii=False) if parsed_data else ""

            # Check if we can localize evidence context around candidate findings
            raw_findings = parsed_data.get("findings", []) if isinstance(parsed_data, dict) else []
            localized_snippets = []
            if raw_findings:
                referenced_locs = {
                    (f.get("commit_sha"), f.get("file_path"))
                    for f in raw_findings
                    if isinstance(f, dict) and f.get("file_path")
                }
                localized_snippets = [
                    s for s in visible_snippets
                    if (s.commit_sha, s.file_path) in referenced_locs
                ]

            repair_evidences = orig_req.evidences
            if localized_snippets:
                focused_context = self.context_builder.build_code_context(
                    project_id=project_id,
                    draft=draft,
                    criteria=criteria or [],
                    code_snippets=localized_snippets,
                    rules=rules,
                    evidence_ids=sorted(code_ev_ids),
                    budget_tokens=safe_input_budget,
                    strict_budget=True,
                    repository_scope=repository_scope,
                )
                repair_evidences = self.context_builder.runtime_payload(
                    focused_context,
                    instructions,
                    output_json_schema,
                    output_tokens,
                    capabilities.context_tokens,
                    max_output_tokens=capabilities.max_output_tokens,
                    request_fields={
                        "role": "revisor",
                        "model": self.model,
                        "schema_name": "code_findings_v1",
                        "timeout_seconds": self.timeout_seconds,
                    },
                    safety_margin_tokens=safety_margin,
                )

            repair_instructions = (
                f"{instructions}\n\n"
                f"IMPORTANT CORRECTION REQUIRED:\n"
                f"Your previous output failed validation with the following error(s):\n"
                f"{err_summary}\n\n"
                f"YOUR PREVIOUS OUTPUT DRAFT:\n"
                f"```json\n{previous_draft_str}\n```\n\n"
                f"Regenerate the complete valid JSON response fixing these specific issues.\n"
                f"- Ensure every candidate finding strictly includes 'suggested_code' and 'improvement_summary'.\n"
                f"- Preserve exact commit_sha, file_path, line, and evidence_ids from your previous draft and visible code."
            )

            return GenerationRequest(
                role=orig_req.role,
                instructions=repair_instructions,
                evidences=repair_evidences,
                schema_name=orig_req.schema_name,
                json_schema=orig_req.json_schema,
                model=orig_req.model,
                max_generation_tokens=orig_req.max_generation_tokens,
                timeout_seconds=orig_req.timeout_seconds,
                options=orig_req.options,
            )

        _, validated_dict = self.coordinator.invoke_stage(
            stage=stage_name or "quality",
            provider=self.provider,
            request=request,
            validator=validate_payload,
            repair_context_builder=build_repair_request,
        )

        raw_findings = validated_dict.get("findings", [])
        return [CandidateFinding.model_validate(item) for item in raw_findings]
