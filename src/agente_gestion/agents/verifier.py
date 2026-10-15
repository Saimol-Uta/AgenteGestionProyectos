"""VerifierAgent: selectively verifies candidate findings using independent inspection."""

import hashlib
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.code import CodeDiffSnippet, validate_source_syntax
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


def _suggestion_verification_issue(
    finding: CandidateFinding, snippets: list[CodeDiffSnippet],
) -> str | None:
    """Require one hashed, complete, pinned file before verifying a code suggestion."""
    replacement = finding.suggested_code
    if not replacement.strip():
        return None
    full_files = [snippet for snippet in snippets
                  if snippet.commit_sha == finding.commit_sha
                  and snippet.file_path == finding.file_path
                  and snippet.evidence_scope == "full_file"]
    if len(full_files) != 1:
        return "Se requiere un único archivo completo para validar el código sugerido."
    full_file = full_files[0]
    source = full_file.content
    expected_hash = full_file.source_sha256
    if (not full_file.is_complete or full_file.truncated
            or not full_file.repository.strip()
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", full_file.commit_sha) is None
            or expected_hash is None
            or hashlib.sha256(source.encode("utf-8")).hexdigest() != expected_hash
            or full_file.total_lines != len(source.splitlines())
            or full_file.lines != list(range(1, full_file.total_lines + 1))):
        return "El archivo completo no conserva límites o hash verificables."
    excerpt = finding.evidence
    if not excerpt:
        return "El hallazgo no contiene un fragmento original explícito para el parche."
    if source.count(excerpt) != 1:
        return "El fragmento original no coincide de forma única con el archivo completo."
    updated = source.replace(excerpt, replacement, 1)
    syntax_status = validate_source_syntax(finding.file_path, updated)
    if syntax_status == "unsupported":
        return "Validación sintáctica no soportada para el lenguaje de este archivo."
    if syntax_status == "invalid":
        return "El código sugerido rompe la sintaxis del archivo completo."
    return None


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

        source_issues = {
            finding.id: issue for finding in findings
            if (issue := _suggestion_verification_issue(finding, snippets)) is not None
        }
        verifiable_findings = [finding for finding in findings if finding.id not in source_issues]
        if not verifiable_findings:
            return [
                FindingVerification(
                    finding_id=finding.id, verdict=VerificationVerdict.insufficient,
                    reason=source_issues[finding.id], verified_by="verifier_agent",
                    evidence_ids=[],
                )
                for finding in findings
            ]

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
            "- Si hay un archivo completo, aplica mentalmente suggested_code al fragmento original único y valida la sintaxis del archivo resultante antes de confirmar.\n"
        )

        context = self.context_builder.build_verifier_context(
            project_id, verifiable_findings, snippets, rules or [], sorted(valid_ev_ids), strict_budget=True,
        )
        capabilities = self.provider.get_capabilities()
        output_tokens = min(1024, capabilities.max_output_tokens)
        evidences_payload = self.context_builder.runtime_payload(
            context, instructions, VerifierOutputSchema.model_json_schema(), output_tokens,
            capabilities.context_tokens, max_output_tokens=capabilities.max_output_tokens,
            request_fields={"role": "verificador", "model": self.model, "schema_name": "verifier_output_v1", "timeout_seconds": self.timeout_seconds},
        )

        snippets = [CodeDiffSnippet.model_validate(row) for row in evidences_payload["code_snippets"]]
        selected_rule_ids = {row["id"] for row in evidences_payload["rules"]}
        if rules is not None:
            rules = [rule for rule in rules if rule.id in selected_rule_ids]
        valid_ev_ids = set(evidences_payload["available_evidence_ids"])

        request = GenerationRequest(
            role="verificador",
            instructions=instructions,
            evidences=evidences_payload,
            schema_name="verifier_output_v1",
            json_schema=VerifierOutputSchema.model_json_schema(),
            model=self.model,
            max_generation_tokens=output_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        def validate_payload(data: dict[str, Any]) -> list[str]:
            raw_verifs = data.get("verifications", [])
            parsed_verifs: list[FindingVerification] = []
            for item in raw_verifs:
                raw_verdict = item.get("verdict", "insufficient").lower()
                reason = item.get("reason", "")
                finding = next((candidate for candidate in verifiable_findings
                                if candidate.id == item.get("finding_id")), None)
                if finding is not None:
                    issue = _suggestion_verification_issue(finding, snippets)
                    if issue:
                        raw_verdict = VerificationVerdict.insufficient.value
                        reason = issue
                try:
                    verdict_enum = VerificationVerdict(raw_verdict)
                except ValueError:
                    verdict_enum = VerificationVerdict.insufficient

                parsed_verifs.append(
                    FindingVerification(
                        finding_id=item.get("finding_id", ""),
                        verdict=verdict_enum,
                        reason=reason,
                        verified_by="verifier_agent",
                        evidence_ids=item.get("evidence_ids", []),
                    )
                )

            return SemanticValidator.validate_finding_verifications(
                verifications=parsed_verifs,
                candidate_findings=verifiable_findings,
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
            reason = item.get("reason", "")
            finding = next((candidate for candidate in verifiable_findings
                            if candidate.id == item.get("finding_id")), None)
            if finding is not None:
                issue = _suggestion_verification_issue(finding, snippets)
                if issue:
                    raw_v = VerificationVerdict.insufficient.value
                    reason = issue
            try:
                verdict_enum = VerificationVerdict(raw_v)
            except ValueError:
                verdict_enum = VerificationVerdict.insufficient

            result_verifs.append(
                FindingVerification(
                    finding_id=item["finding_id"],
                    verdict=verdict_enum,
                    reason=reason,
                    verified_by="verifier_agent",
                    evidence_ids=item.get("evidence_ids", []),
                )
            )

        verified_by_id = {item.finding_id: item for item in result_verifs}
        results: list[FindingVerification] = []
        for finding in findings:
            if finding.id in source_issues:
                results.append(FindingVerification(
                    finding_id=finding.id, verdict=VerificationVerdict.insufficient,
                    reason=source_issues[finding.id], verified_by="verifier_agent", evidence_ids=[],
                ))
            elif finding.id in verified_by_id:
                results.append(verified_by_id[finding.id])
        return results
