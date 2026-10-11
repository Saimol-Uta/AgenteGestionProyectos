"""Abstract port for persistence storage."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.run import ExecutionRun, StageExecutionRecord
from agente_gestion.domain.findings import CandidateFinding, FindingVerification
from agente_gestion.domain.llm import LLMCallAttempt
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.proposal_review import DecisionEvent, Projection
from agente_gestion.domain.proposal_dry_run import DryRunRequest, DryRunResult


class StoragePort(ABC):
    """Storage interface for persisting runs, snapshots, stages, reports, and decisions."""

    @abstractmethod
    def save_run(self, run: ExecutionRun) -> None:
        """Persist an execution run."""
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[ExecutionRun]:
        """Retrieve an execution run by ID."""
        raise NotImplementedError

    @abstractmethod
    def find_run_by_fingerprint(self, snapshot_hash: str, config_hash: str) -> Optional[ExecutionRun]:
        """Find an existing run by snapshot and config hashes for idempotency."""
        raise NotImplementedError

    @abstractmethod
    def save_stage_result(self, run_id: str, stage: StageExecutionRecord) -> None:
        """Persist a stage execution record."""
        raise NotImplementedError

    @abstractmethod
    def get_stage_results(self, run_id: str) -> dict[str, StageExecutionRecord]:
        """Retrieve all recorded stage results for a run."""
        raise NotImplementedError

    @abstractmethod
    def save_snapshot(self, snapshot: WeeklySnapshot, content_hash: Optional[str] = None) -> None:
        """Persist a captured weekly snapshot."""
        raise NotImplementedError

    def save_snapshot_with_capture_receipt(
        self, snapshot: WeeklySnapshot, content_hash: Optional[str] = None,
    ) -> SnapshotCapture:
        """Save and return the exact stored capture; optional storage capability."""
        raise NotImplementedError("Snapshot capture receipts unavailable")

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> Optional[WeeklySnapshot]:
        """Retrieve a persisted weekly snapshot by ID."""
        raise NotImplementedError

    def list_snapshot_captures(self, project_id: str) -> list[SnapshotCapture]:
        """List immutable captures chronologically; optional storage capability."""
        raise NotImplementedError("Snapshot history unavailable")

    def get_snapshot_capture(self, capture_id: str) -> Optional[SnapshotCapture]:
        """Retrieve a capture by exact JSON hash; optional storage capability."""
        raise NotImplementedError("Snapshot history unavailable")

    def save_action_proposal(self, proposal: ActionProposal,
                             supporting_evidence: list[EvidenceItem]) -> None:
        """Persist already sanitized submission DTOs; optional, never grants permission."""
        raise NotImplementedError("Action proposal catalogue unavailable")

    def get_action_proposal(self, proposal_id: str) -> Optional[tuple[ActionProposal, list[EvidenceItem]]]:
        """Return a frozen submission and its explicitly referenced evidence."""
        raise NotImplementedError("Action proposal catalogue unavailable")

    def list_action_proposals(self, project_id: str) -> list[tuple[ActionProposal, list[EvidenceItem]]]:
        """List frozen submissions for a project; optional storage capability."""
        raise NotImplementedError("Action proposal catalogue unavailable")

    def get_proposal_review_state(
        self, project_id: str, proposal_id: str,
    ) -> Optional[tuple[str, list[DecisionEvent], Projection]]:
        """Verified exact submission hash, ordered ledger and derived offline state."""
        raise NotImplementedError("Proposal review ledger unavailable")

    def append_proposal_decision(self, event: DecisionEvent) -> Projection:
        """Append with optimistic version checking; never grants live permission."""
        raise NotImplementedError("Proposal review ledger unavailable")

    def append_proposal_dry_run(
        self, request: DryRunRequest,
    ) -> tuple[DryRunRequest, DryRunResult, str, bool]:
        """Simulate under current guards; return UTC receipt and replay indicator."""
        raise NotImplementedError("Proposal dry-run audit unavailable")

    def get_proposal_dry_run(
        self, project_id: str, proposal_id: str, request_id: str,
    ) -> Optional[tuple[DryRunRequest, DryRunResult, str]]:
        """Historical audit lookup, not permission for a new simulation."""
        raise NotImplementedError("Proposal dry-run audit unavailable")

    @abstractmethod
    def save_evidence(self, run_id: str, evidence_items: list[EvidenceItem]) -> None:
        """Persist normalized evidence records associated with a run."""
        raise NotImplementedError

    @abstractmethod
    def save_findings(
        self,
        run_id: str,
        findings: list[CandidateFinding],
        escalation_decisions: Optional[list[Any]] = None,
    ) -> None:
        """Persist candidate findings and their escalation decisions."""
        raise NotImplementedError

    @abstractmethod
    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve findings recorded for a run."""
        raise NotImplementedError

    @abstractmethod
    def save_verifications(self, run_id: str, verifications: list[FindingVerification]) -> None:
        """Persist finding verifications for a run."""
        raise NotImplementedError

    @abstractmethod
    def get_verifications(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve verifications recorded for a run."""
        raise NotImplementedError

    @abstractmethod
    def save_llm_attempts(self, run_id: str, attempts: list[LLMCallAttempt]) -> None:
        """Persist individual LLM call and repair attempts for audit."""
        raise NotImplementedError

    @abstractmethod
    def save_report(
        self,
        report: FinalReport,
        report_id: Optional[str] = None,
        report_md: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """Persist a generated weekly report."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_report(self, project_id: str) -> Optional[FinalReport]:
        """Retrieve the latest report for a project."""
        raise NotImplementedError

    @abstractmethod
    def get_report(self, report_id: str) -> Optional[FinalReport]:
        """Retrieve a specific report by report_id."""
        raise NotImplementedError

    @abstractmethod
    def register_human_decision(
        self,
        report_id: str,
        decision: HumanDecisionState,
        reason: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> FinalReport:
        """Register a human manager decision against a report version."""
        raise NotImplementedError
