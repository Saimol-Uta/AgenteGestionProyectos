"""Domain models and contracts."""

from agente_gestion.domain.evidence import (
    EvidenceProvenance,
    EvidenceSource,
    DiffSide,
    EvidenceItem,
)
from agente_gestion.domain.tasks import (
    TaskCard,
    TaskState,
    TaskMappingConfig,
    DeterministicTaskMetrics,
    calculate_task_metrics,
)
from agente_gestion.domain.code import (
    CodeDiffSnippet,
    CIRunResult,
    match_ci_for_commit,
)
from agente_gestion.domain.policies import (
    QualityRule,
    RuleEvaluationStatus,
    RuleEvaluation,
    PolicyEvaluationResult,
    evaluate_policies,
)
from agente_gestion.domain.findings import (
    FindingCategory,
    CandidateFinding,
    VerificationVerdict,
    FindingVerification,
)
from agente_gestion.domain.report import (
    HumanDecisionState,
    MonitoringDraft,
    FinalReport,
)
from agente_gestion.domain.llm import (
    GenerationStatus,
    GenerationRequest,
    GenerationResult,
    ModelCapabilities,
)
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.run import (
    RunStatus,
    StageStatus,
    StageExecutionRecord,
    ExecutionRun,
    compute_sha256,
)

__all__ = [
    "EvidenceProvenance",
    "EvidenceSource",
    "DiffSide",
    "EvidenceItem",
    "TaskCard",
    "TaskState",
    "TaskMappingConfig",
    "DeterministicTaskMetrics",
    "calculate_task_metrics",
    "CodeDiffSnippet",
    "CIRunResult",
    "match_ci_for_commit",
    "QualityRule",
    "RuleEvaluationStatus",
    "RuleEvaluation",
    "PolicyEvaluationResult",
    "evaluate_policies",
    "FindingCategory",
    "CandidateFinding",
    "VerificationVerdict",
    "FindingVerification",
    "HumanDecisionState",
    "MonitoringDraft",
    "FinalReport",
    "GenerationStatus",
    "GenerationRequest",
    "GenerationResult",
    "ModelCapabilities",
    "WeeklySnapshot",
    "RunStatus",
    "StageStatus",
    "StageExecutionRecord",
    "ExecutionRun",
    "compute_sha256",
]
