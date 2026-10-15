"""Code defect findings, categories, and verification contracts."""

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class FindingCategory(str, Enum):
    inyeccion_sql = "inyeccion_sql"
    manejo_recursos = "manejo_recursos"
    division_cero = "division_cero"
    otros = "otros"


class FindingSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class CandidateFinding(BaseModel):
    """Potential issue reported during code review stage with strict contract."""
    id: str = Field(min_length=1, description="Unique finding identifier (nonempty)")
    rule_id: Optional[str] = Field(default="Q3", min_length=1, description="Existing policy rule, or explicit null when unassociated")
    claim: str = Field(default="Posible defecto de codigo identificado", min_length=1, description="Technical assertion or defect claim (nonempty)")
    severity: FindingSeverity = Field(default=FindingSeverity.low, description="Finding severity: low, medium, high, critical")
    evidence_ids: list[str] = Field(default_factory=lambda: ["c0ffee1"], description="Associated evidence IDs citing exact snippet or CI (nonempty)")
    commit_sha: str = Field(min_length=1, description="Commit SHA where defect was observed (nonempty)")
    file_path: str = Field(min_length=1, description="Target file relative path (nonempty)")
    line: int = Field(description="Exact line number in diff/file")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="Model confidence score 0.0-1.0")
    requires_verification: bool = Field(default=True, description="Whether finding requires independent verification")

    claim_basis: Literal["visible_code", "file_absence", "policy_metric"] = "visible_code"
    metric_key: Optional[str] = None
    observed_value: Optional[float] = None

    @field_validator("rule_id", mode="before")
    @classmethod
    def validate_optional_rule(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("rule_id must be an existing nonempty ID or explicit null")
        return value.strip()

    # Compatibility fields maintained
    category: str = Field(default="otros", description="Finding category e.g. inyeccion_sql, manejo_recursos")
    evidence: str = Field(default="", description="Exact snippet or excerpt of problematic code")
    suggested_fix: str = Field(default="", description="Actionable proposal to fix the defect")
    suggested_code: str = Field(default="", description="Coherent code example proposed for human review")
    improvement_summary: str = Field(default="", description="Spanish-language summary of the proposed improvement")
    decision_status: Literal["pending", "approved", "rejected", "changes_requested", "failed"] = "pending"
    branch_name: Optional[str] = None
    branch_url: Optional[str] = None
    commit_sha_applied: Optional[str] = None

    @field_validator("id", "claim", "commit_sha", "file_path", mode="before")
    @classmethod
    def validate_nonempty_str(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Field cannot be null")
        s = str(v).strip()
        if not s:
            raise ValueError("Field cannot be empty string")
        return s

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("severity cannot be null")
        if isinstance(v, str):
            norm = v.strip().lower()
            if norm in {s.value for s in FindingSeverity}:
                return FindingSeverity(norm)
        return v

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def validate_evidence_ids(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if str(v).strip() else []

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> float:
        if v is None:
            raise ValueError("confidence cannot be null")
        val = float(v)
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {val}")
        return val

    @field_validator("requires_verification", mode="before")
    @classmethod
    def validate_requires_verification(cls, v: Any) -> bool:
        if v is None:
            raise ValueError("requires_verification cannot be null")
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)



class VerificationVerdict(str, Enum):
    confirmed = "confirmed"
    rejected = "rejected"
    insufficient = "insufficient"


class FindingVerification(BaseModel):
    """Independent verification decision for a candidate finding."""
    finding_id: str = Field(description="ID of finding being verified")
    verdict: VerificationVerdict = Field(description="Verification verdict")
    reason: str = Field(description="Technical explanation supporting verdict")
    verified_by: str = Field(default="system", description="Verifier role or engine name")
    evidence_ids: list[str] = Field(default_factory=list, description="Corroborating evidence IDs")

