"""SQLite storage adapter for execution runs, audit records, and human decisions."""

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from agente_gestion.ports.storage import StoragePort
from agente_gestion.domain.run import (
    ExecutionRun,
    RunStatus,
    StageExecutionRecord,
    StageStatus,
    compute_sha256,
)
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot, RepositorySnapshotResult
from agente_gestion.domain.report import FinalReport, HumanDecisionState, RepositoryReportResult
from agente_gestion.domain.repository_history import (
    RepositoryArtifactIdentity,
    RepositoryArtifactObservation,
    RepositoryHistoryFilter,
    RepositoryHistoryRecord,
)
from agente_gestion.adapters.storage.repository_history import (
    RepositorySourceScope,
    artifact_identity_key,
    extract_artifact_observations,
    iter_repository_source_scopes,
    project_source_scope,
    scope_matches_filters,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.llm import LLMCallAttempt, GenerationStatus
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal
from agente_gestion.domain.proposal_review import DecisionEvent, Projection, fold_decisions, _raw
from agente_gestion.domain.proposal_dry_run import DryRunRequest, DryRunResult, _checked, _known_keys, simulate_proposal
from agente_gestion.domain.action_execution import ExecutionRequest, ExecutionResult
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.domain.security_rbac import SecurityAuditEntry


@dataclass(frozen=True)
class ProposalExecutionLedgerRecord:
    result: ExecutionResult
    decision: str | None
    original_proposal_id: str | None
    human_reason: str | None
    audit_event_id: str | None
    actor_verification: Literal["verified_local_session", "unknown"]


class FindingDecisionStorageError(RuntimeError):
    """Stable storage-level reason for a rejected or conflicting decision request."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SQLiteStorageAdapter(StoragePort):
    """
    Robust SQLite persistence adapter with explicit connection management.
    Never holds open transactions across network boundaries.
    """

    def __init__(self, db_path: str = ".runtime/agente.db", *, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            # Fail immediately for a missing DB; never create directories or schemas.
            self._get_connection().close()
        else:
            self._ensure_dir_and_schema()

    def _get_connection(self) -> sqlite3.Connection:
        if self.read_only:
            uri = Path(self.db_path).absolute().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.execute("PRAGMA query_only = ON")
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_dir_and_schema(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = self._get_connection()
        try:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        snapshot_id TEXT NOT NULL,
                        config_hash TEXT NOT NULL,
                        snapshot_hash TEXT NOT NULL,
                        policy_version TEXT NOT NULL,
                        prompt_versions TEXT,
                        schema_versions TEXT,
                        duration_seconds REAL DEFAULT 0.0,
                        error_message TEXT,
                        report_id TEXT,
                        is_simulated INTEGER DEFAULT 0
                    );

                    CREATE INDEX IF NOT EXISTS idx_runs_fingerprint ON runs(snapshot_hash, config_hash);
                    CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id);

                    CREATE TABLE IF NOT EXISTS stage_results (
                        run_id TEXT NOT NULL,
                        stage_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        duration_seconds REAL DEFAULT 0.0,
                        output_summary TEXT,
                        error_message TEXT,
                        data_hash TEXT,
                        completed_at TEXT,
                        PRIMARY KEY (run_id, stage_name)
                    );

                    CREATE TABLE IF NOT EXISTS snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        cutoff_datetime TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        is_complete INTEGER NOT NULL,
                        incomplete_reason TEXT,
                        data_json TEXT NOT NULL,
                        content_hash TEXT
                    );

                    CREATE TABLE IF NOT EXISTS evidence (
                        id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        source TEXT,
                        title TEXT,
                        content_hash TEXT,
                        data_json TEXT,
                        PRIMARY KEY (id, run_id)
                    );

                    CREATE TABLE IF NOT EXISTS findings (
                        id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        category TEXT,
                        file_path TEXT,
                        line INTEGER,
                        commit_sha TEXT,
                        evidence TEXT,
                        suggested_fix TEXT,
                        rule_id TEXT,
                        confidence REAL,
                        should_escalate INTEGER DEFAULT 0,
                        escalation_reasons TEXT,
                        PRIMARY KEY (id, run_id)
                    );

                    CREATE TABLE IF NOT EXISTS verifications (
                        finding_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        reason TEXT,
                        verified_by TEXT,
                        evidence_ids TEXT,
                        PRIMARY KEY (finding_id, run_id)
                    );

                    CREATE TABLE IF NOT EXISTS llm_calls (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        status TEXT NOT NULL,
                        finish_reason TEXT,
                        duration_seconds REAL DEFAULT 0.0,
                        prompt_tokens INTEGER,
                        completion_tokens INTEGER,
                        validation_passed INTEGER NOT NULL,
                        validation_errors TEXT,
                        error_message TEXT,
                        structured_data TEXT
                    );

                    CREATE TABLE IF NOT EXISTS reports (
                        report_id TEXT PRIMARY KEY,
                        run_id TEXT,
                        project_id TEXT NOT NULL,
                        version INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        cutoff_datetime TEXT NOT NULL,
                        recommendation TEXT NOT NULL,
                        human_decision TEXT NOT NULL,
                        decision_reason TEXT,
                        decision_timestamp TEXT,
                        data_json TEXT NOT NULL,
                        markdown_text TEXT
                    );

                    CREATE TABLE IF NOT EXISTS human_decisions (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL,
                        run_id TEXT,
                        decision TEXT NOT NULL,
                        reason TEXT,
                        recorded_at TEXT NOT NULL
                    );
                    """
                )
            # Additive findings migration preserves legacy rows and leaves read-only
            # browser queries able to continue using the original table shape.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                finding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
                for column, declaration in (
                    ("suggested_code", "TEXT NOT NULL DEFAULT ''"),
                    ("improvement_summary", "TEXT NOT NULL DEFAULT ''"),
                    ("decision_status", "TEXT NOT NULL DEFAULT 'pending'"),
                    ("branch_name", "TEXT"),
                    ("branch_url", "TEXT"),
                    ("commit_sha_applied", "TEXT"),
                ):
                    if column not in finding_columns:
                        conn.execute(f"ALTER TABLE findings ADD COLUMN {column} {declaration}")
            # executescript above may commit implicitly. Keep all history DDL,
            # indexes and backfill in a separate explicit transaction, never a script.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS snapshot_captures (
                        capture_id TEXT PRIMARY KEY,
                        snapshot_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        cutoff_datetime TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL
                    )
                """)
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_snapshot_captures_project_order
                                ON snapshot_captures(project_id, captured_at, capture_id)""")
                for row in conn.execute("SELECT data_json FROM snapshots"):
                    self._insert_capture(conn, row["data_json"])
            # Repository artifacts are an additive index over immutable captures.
            # Source validation lives here; report eligibility remains in each saved report.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS repository_artifacts (
                    artifact_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    repository_owner TEXT NOT NULL,
                    repository_name TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    ci_run_id TEXT NOT NULL,
                    actual_attempt INTEGER NOT NULL,
                    artifact_id TEXT NOT NULL,
                    artifact_name TEXT NOT NULL,
                    UNIQUE(project_id, repository_id, repository_owner, repository_name,
                           commit_sha, ci_run_id, actual_attempt, artifact_id, artifact_name)
                )""")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_repository_artifacts_lookup
                    ON repository_artifacts(project_id, repository_id, commit_sha, ci_run_id, actual_attempt)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS repository_artifact_observations (
                    artifact_key TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    source_metadata_sha256 TEXT,
                    source_outcome TEXT,
                    source_certifies_metrics INTEGER,
                    source_schema_valid INTEGER,
                    source_semantics_valid INTEGER,
                    source_identity_bound INTEGER,
                    source_workflow_status_matches INTEGER,
                    PRIMARY KEY(artifact_key, capture_id)
                )""")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_repository_artifact_observations_capture
                    ON repository_artifact_observations(capture_id, artifact_key)""")
                for row in conn.execute("SELECT * FROM snapshot_captures"):
                    try:
                        capture = self._parse_capture(row)
                        observations = extract_artifact_observations(
                            capture.snapshot, tolerate_unindexed_historical_data=True,
                        )
                    except (TypeError, ValueError, KeyError, OverflowError):
                        # Historical provenance is indexed only when it can be validated.
                        continue
                    for observation in observations:
                        self._insert_repository_artifact_observation(
                            conn, capture.capture_id, observation,
                        )
            # Catalogue migration is writer-only and independent of history backfill.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS action_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        capture_id TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL
                    )
                """)
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_action_proposals_project_order
                                ON action_proposals(project_id, proposal_id)""")
            # Independent writer-only ledger migration, after catalogue initialization.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS proposal_decisions (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    proposal_body_sha256 TEXT NOT NULL,
                    ordered_version INTEGER NOT NULL CHECK(ordered_version > 0),
                    data_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(project_id, proposal_id, ordered_version)
                )""")
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS proposal_decisions_no_{operation}
                        BEFORE {operation} ON proposal_decisions
                        BEGIN SELECT RAISE(ABORT, 'Proposal review ledger is append-only'); END""")
            # Independent writer-only audit migration, after the review ledger.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS proposal_dry_runs (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL, proposal_body_sha256 TEXT NOT NULL,
                    permission_event_id TEXT NOT NULL, expected_version INTEGER NOT NULL,
                    status TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL
                )""")
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS proposal_dry_runs_no_{operation}
                        BEFORE {operation} ON proposal_dry_runs
                        BEGIN SELECT RAISE(ABORT, 'Proposal dry-run audit is append-only'); END""")
            # Independent writer-only execution ledger migration.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS proposal_executions (
                    execution_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    actor_id TEXT,
                    field TEXT,
                    recorded_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    human_reason TEXT
                )""")
                execution_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(proposal_executions)")
                }
                if "human_reason" not in execution_columns:
                    conn.execute("ALTER TABLE proposal_executions ADD COLUMN human_reason TEXT")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_proposal_executions_lookup
                    ON proposal_executions(project_id, proposal_id, recorded_at)""")
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS proposal_executions_no_{operation}
                        BEFORE {operation} ON proposal_executions
                        BEGIN SELECT RAISE(ABORT, 'Proposal execution ledger is append-only'); END""")
            # Independent notification outbox migration (H3).
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS notification_outbox (
                    delivery_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    file_path TEXT,
                    dispatched_at TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    detail TEXT
                )""")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_notification_outbox_window
                    ON notification_outbox(project_id, window_id, channel)""")
            # Independent security audit log migration (H4).
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS security_audit_log (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    permission_checked TEXT NOT NULL,
                    resource_id TEXT,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    token_fingerprint TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    human_reason TEXT
                )""")
                audit_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(security_audit_log)")
                }
                if "human_reason" not in audit_columns:
                    conn.execute("ALTER TABLE security_audit_log ADD COLUMN human_reason TEXT")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_security_audit_project
                    ON security_audit_log(project_id, recorded_at)""")
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS security_audit_log_no_{operation}
                        BEFORE {operation} ON security_audit_log
                        BEGIN SELECT RAISE(ABORT, 'Security audit log is append-only'); END""")
            # Request receipts are mutable only while an action is in flight; the
            # decision ledger itself is permanently append-only.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS finding_decision_requests (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('processing', 'completed', 'failed')),
                    result_json TEXT,
                    error_status INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    event_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, run_id, finding_id, idempotency_key)
                )""")
                conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_finding_decisions_one_active
                    ON finding_decision_requests(project_id, run_id, finding_id)
                    WHERE state='processing'""")
                conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_finding_decisions_one_approval
                    ON finding_decision_requests(project_id, run_id, finding_id)
                    WHERE decision='approve'""")
                conn.execute("""CREATE TABLE IF NOT EXISTS finding_decisions (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )""")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_finding_decisions_lookup
                    ON finding_decisions(project_id, run_id, finding_id, recorded_at)""")
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS finding_decisions_no_{operation}
                        BEFORE {operation} ON finding_decisions
                        BEGIN SELECT RAISE(ABORT, 'Finding decision ledger is append-only'); END""")
        finally:
            conn.close()


    @staticmethod
    def _canonical_utc(value: str) -> str:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/Guayaquil"))
        return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @classmethod
    def _capture_metadata(cls, raw: str) -> tuple[str, str, str, str, str]:
        # Inspect the original JSON only for metadata; never reserialize legacy data
        # or hash a parsed model whose defaults may change between schema versions.
        data = json.loads(raw)
        actual_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return (actual_hash, data["id"], data["project_id"],
                cls._canonical_utc(data["cutoff_datetime"]),
                cls._canonical_utc(data["captured_at"]))

    def _insert_capture(self, conn: sqlite3.Connection, raw: str) -> None:
        metadata = self._capture_metadata(raw)
        conn.execute("""
            INSERT INTO snapshot_captures (
                capture_id, snapshot_id, project_id, cutoff_datetime,
                captured_at, data_json, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(capture_id) DO NOTHING
        """, (*metadata, raw, metadata[0]))

    @classmethod
    def _parse_capture(cls, row: sqlite3.Row) -> SnapshotCapture:
        try:
            metadata = cls._capture_metadata(row["data_json"])
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid snapshot capture JSON metadata") from exc
        stored = tuple(row[key] for key in (
            "capture_id", "snapshot_id", "project_id", "cutoff_datetime", "captured_at"))
        if metadata != stored or row["content_hash"] != metadata[0]:
            raise ValueError("Snapshot capture hash or metadata mismatch")
        return SnapshotCapture(capture_id=metadata[0], content_hash=metadata[0],
                               snapshot=WeeklySnapshot.model_validate_json(row["data_json"]))

    @staticmethod
    def _require_history(conn: sqlite3.Connection) -> None:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='snapshot_captures'").fetchone():
            raise NotImplementedError("Snapshot history unavailable; writer migration required")

    def list_snapshot_captures(self, project_id: str) -> list[SnapshotCapture]:
        conn = self._get_connection()
        try:
            self._require_history(conn)
            rows = conn.execute("""SELECT * FROM snapshot_captures WHERE project_id = ?
                                   ORDER BY captured_at, capture_id""", (project_id,))
            return [self._parse_capture(row) for row in rows]
        finally:
            conn.close()

    def get_snapshot_capture(self, capture_id: str) -> Optional[SnapshotCapture]:
        conn = self._get_connection()
        try:
            self._require_history(conn)
            row = conn.execute("SELECT * FROM snapshot_captures WHERE capture_id = ?",
                               (capture_id,)).fetchone()
            return self._parse_capture(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def _repository_identity_matches(left: Any, right: Any) -> bool:
        return bool(
            left is not None and right is not None
            and left.id == right.id
            and left.owner.casefold() == right.owner.casefold()
            and left.repo.casefold() == right.repo.casefold()
        )

    @staticmethod
    def _artifact_observation_row(
        observation: RepositoryArtifactObservation, capture_id: str,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        identity = observation.identity
        repository = identity.repository_identity
        artifact_key = artifact_identity_key(identity)
        artifact_row = (
            artifact_key, identity.project_id, repository.id, repository.owner.casefold(),
            repository.repo.casefold(), identity.commit_sha, identity.ci_run_id,
            identity.actual_attempt, identity.artifact_id, identity.artifact_name,
        )
        metadata = observation.source_metrics_artifact
        if metadata is None:
            metadata_row = (None, None, None, None, None, None, None)
        else:
            metadata_row = (
                hashlib.sha256(metadata.model_dump_json().encode("utf-8")).hexdigest(),
                metadata.outcome.value,
                int(metadata.certifies_metrics), int(metadata.schema_valid),
                int(metadata.semantics_valid), int(metadata.identity_bound),
                int(metadata.workflow_status_matches),
            )
        observation_row = (
            artifact_key, capture_id,
            identity.model_dump_json(), *metadata_row,
        )
        return artifact_row, observation_row

    @classmethod
    def _insert_repository_artifact_observation(
        cls,
        conn: sqlite3.Connection,
        capture_id: str,
        observation: RepositoryArtifactObservation,
    ) -> None:
        artifact_row, observation_row = cls._artifact_observation_row(observation, capture_id)
        conn.execute(
            """INSERT INTO repository_artifacts (
                artifact_key, project_id, repository_id, repository_owner, repository_name,
                commit_sha, ci_run_id, actual_attempt, artifact_id, artifact_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_key) DO NOTHING""",
            artifact_row,
        )
        stored_artifact = conn.execute(
            "SELECT artifact_key, project_id, repository_id, repository_owner, repository_name, "
            "commit_sha, ci_run_id, actual_attempt, artifact_id, artifact_name "
            "FROM repository_artifacts WHERE artifact_key = ?",
            (artifact_row[0],),
        ).fetchone()
        if stored_artifact is None or tuple(stored_artifact) != artifact_row:
            raise ValueError("Conflicting repository artifact identity.")

        conn.execute(
            """INSERT INTO repository_artifact_observations (
                artifact_key, capture_id, identity_json, source_metadata_sha256,
                source_outcome, source_certifies_metrics, source_schema_valid,
                source_semantics_valid, source_identity_bound, source_workflow_status_matches
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_key, capture_id) DO NOTHING""",
            observation_row,
        )
        stored_observation = conn.execute(
            """SELECT artifact_key, capture_id, identity_json, source_metadata_sha256,
                source_outcome, source_certifies_metrics, source_schema_valid,
                source_semantics_valid, source_identity_bound, source_workflow_status_matches
            FROM repository_artifact_observations WHERE artifact_key = ? AND capture_id = ?""",
            (observation_row[0], capture_id),
        ).fetchone()
        if stored_observation is None or tuple(stored_observation) != observation_row:
            raise ValueError("Conflicting source artifact observation for one snapshot capture.")

    @classmethod
    def _load_repository_artifact_observations(
        cls,
        conn: sqlite3.Connection,
        capture_id: str,
        source_observations: dict[str, RepositoryArtifactObservation],
    ) -> list[RepositoryArtifactObservation]:
        rows = conn.execute(
            """SELECT a.*, o.identity_json, o.source_metadata_sha256, o.source_outcome,
                o.source_certifies_metrics, o.source_schema_valid, o.source_semantics_valid,
                o.source_identity_bound, o.source_workflow_status_matches
            FROM repository_artifact_observations AS o
            JOIN repository_artifacts AS a ON a.artifact_key = o.artifact_key
            WHERE o.capture_id = ? ORDER BY a.artifact_key""",
            (capture_id,),
        )
        result: list[RepositoryArtifactObservation] = []
        indexed_keys: set[str] = set()
        for row in rows:
            try:
                identity = RepositoryArtifactIdentity.model_validate_json(row["identity_json"])
                if artifact_identity_key(identity) != row["artifact_key"]:
                    raise ValueError("Artifact identity key mismatch")
                repository = identity.repository_identity
                expected_artifact = (
                    row["artifact_key"], identity.project_id, repository.id,
                    repository.owner.casefold(), repository.repo.casefold(), identity.commit_sha,
                    identity.ci_run_id, identity.actual_attempt, identity.artifact_id,
                    identity.artifact_name,
                )
                stored_artifact = tuple(row[key] for key in (
                    "artifact_key", "project_id", "repository_id", "repository_owner",
                    "repository_name", "commit_sha", "ci_run_id", "actual_attempt",
                    "artifact_id", "artifact_name",
                ))
                if expected_artifact != stored_artifact:
                    raise ValueError("Artifact identity index mismatch")

                source_observation = source_observations.get(row["artifact_key"])
                if source_observation is None or source_observation.identity != identity:
                    raise ValueError("Indexed artifact is not present in its validated source capture")
                metadata = source_observation.source_metrics_artifact
                if metadata is None:
                    expected_status = (None, None, None, None, None, None)
                    expected_hash = None
                else:
                    expected_status = (
                        metadata.outcome.value, int(metadata.certifies_metrics),
                        int(metadata.schema_valid), int(metadata.semantics_valid),
                        int(metadata.identity_bound), int(metadata.workflow_status_matches),
                    )
                    expected_hash = hashlib.sha256(
                        metadata.model_dump_json().encode("utf-8")
                    ).hexdigest()
                stored_status = tuple(row[key] for key in (
                    "source_outcome", "source_certifies_metrics", "source_schema_valid",
                    "source_semantics_valid", "source_identity_bound",
                    "source_workflow_status_matches",
                ))
                if expected_hash != row["source_metadata_sha256"] or expected_status != stored_status:
                    raise ValueError("Source metadata index mismatch")
                result.append(source_observation)
                indexed_keys.add(row["artifact_key"])
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError("Stored repository artifact provenance is invalid.") from exc
        if indexed_keys != set(source_observations):
            raise ValueError("Stored repository artifact index is incomplete for its source capture.")
        return result

    @staticmethod
    def _report_result_for_scope(
        report: FinalReport, scope: RepositorySourceScope,
    ) -> Optional[RepositoryReportResult]:
        source = scope.repository_result
        if source is None or scope.repository_identity is None:
            return None
        matches: list[RepositoryReportResult] = []
        for candidate in report.repository_results:
            state = candidate.repository_state
            if (not SQLiteStorageAdapter._repository_identity_matches(
                    state.identity, scope.repository_identity)
                    or state.target_commit_sha != scope.target_commit_sha):
                continue
            if source.ci_runs:
                if candidate.ci_result is None or not any(
                    candidate.ci_result.id == source_ci.id
                    and candidate.ci_result.commit_sha.lower() == source_ci.commit_sha.lower()
                    and candidate.ci_result.run_attempt == source_ci.run_attempt
                    and candidate.ci_result.artifact_id == source_ci.artifact_id
                    and candidate.ci_result.artifact_name == source_ci.artifact_name
                    and SQLiteStorageAdapter._repository_identity_matches(
                        candidate.ci_result.repository_identity or (
                            candidate.ci_result.provenance.repository_identity
                            if candidate.ci_result.provenance is not None else None
                        ),
                        source_ci.repository_identity or (
                            source_ci.provenance.repository_identity
                            if source_ci.provenance is not None else None
                        ),
                    )
                    for source_ci in source.ci_runs
                ):
                    continue
            elif candidate.ci_result is not None:
                continue
            matches.append(candidate)
        return matches[0] if len(matches) == 1 else None

    def _history_evidence_for_scope(
        self,
        run_id: Optional[str],
        project_id: str,
        scope: RepositorySourceScope,
        all_scopes: list[RepositorySourceScope],
    ) -> list[EvidenceItem]:
        if run_id is None or scope.repository_identity is None:
            return []
        identity = scope.repository_identity
        labels: dict[str, set[tuple[str, str, str]]] = {}
        for other in all_scopes:
            candidate = other.repository_identity
            if candidate is None:
                continue
            key = (candidate.id, candidate.owner.casefold(), candidate.repo.casefold())
            for value in (candidate.id, f"{candidate.owner}/{candidate.repo}"):
                labels.setdefault(value.casefold(), set()).add(key)
        commits: set[str] = set()
        if scope.target_commit_sha is not None:
            commits.add(scope.target_commit_sha.lower())
        if scope.repository_result is not None:
            commits.update(item.commit_sha.lower() for item in (
                *scope.repository_result.code_diffs, *scope.repository_result.ci_runs,
            ))
        else:
            commits.update(item.commit_sha.lower() for item in (
                *scope.legacy_code_diffs, *scope.legacy_ci_runs,
            ))
        identity_key = (identity.id, identity.owner.casefold(), identity.repo.casefold())
        evidence: list[EvidenceItem] = []
        for item in self.get_evidence(run_id):
            if not isinstance(item, EvidenceItem):
                continue
            provenance = item.provenance
            label = provenance.repository
            if (provenance.project_id != project_id or label is None
                    or provenance.commit_sha is None
                    or provenance.commit_sha.lower() not in commits
                    or labels.get(label.casefold()) != {identity_key}):
                continue
            evidence.append(item)
        return evidence

    def query_repository_history(
        self,
        filters: RepositoryHistoryFilter | dict[str, Any],
    ) -> list[RepositoryHistoryRecord]:
        """Query exact persisted captures by AND-combined project/repository/CI identity.

        Source artifact validation is returned separately from each report's eligibility
        projection. Historical records without explicit identity remain unknown and are
        never assigned the project's current or a synthetic primary repository.
        """
        query = RepositoryHistoryFilter.model_validate(filters)
        conn = self._get_connection()
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('snapshot_captures', 'repository_artifacts', 'repository_artifact_observations')"
            )}
            required = {
                "snapshot_captures", "repository_artifacts", "repository_artifact_observations",
            }
            if tables != required:
                raise NotImplementedError(
                    "Repository history query unavailable; a writable SQLite migration is required."
                )

            if query.project_id is None:
                capture_rows = conn.execute(
                    "SELECT * FROM snapshot_captures ORDER BY project_id, captured_at, capture_id"
                )
            else:
                capture_rows = conn.execute(
                    "SELECT * FROM snapshot_captures WHERE project_id = ? "
                    "ORDER BY captured_at, capture_id", (query.project_id,),
                )
            records: list[RepositoryHistoryRecord] = []
            for capture_row in capture_rows:
                capture = self._parse_capture(capture_row)
                snapshot = capture.snapshot
                scopes = list(iter_repository_source_scopes(snapshot))
                source_observations = {
                    artifact_identity_key(item.identity): item
                    for item in extract_artifact_observations(
                        snapshot, tolerate_unindexed_historical_data=True,
                    )
                }
                artifact_observations = self._load_repository_artifact_observations(
                    conn, capture.capture_id, source_observations,
                )
                runs = conn.execute(
                    """SELECT run_id FROM runs
                    WHERE project_id = ? AND snapshot_id = ? AND snapshot_hash = ?
                    ORDER BY created_at, run_id""",
                    (snapshot.project_id, snapshot.id, capture.capture_id),
                ).fetchall()
                run_ids: list[Optional[str]] = [row["run_id"] for row in runs] or [None]
                for original_scope in scopes:
                    if not scope_matches_filters(original_scope, query, project_id=snapshot.project_id):
                        continue
                    scope = project_source_scope(original_scope, query)
                    selected_ci_runs = (
                        scope.repository_result.ci_runs if scope.repository_result is not None
                        else scope.legacy_ci_runs
                    )
                    scoped_artifacts = [
                        observation for observation in artifact_observations
                        if self._repository_identity_matches(
                            observation.identity.repository_identity, scope.repository_identity,
                        )
                        and (scope.target_commit_sha is None
                             or observation.identity.commit_sha == scope.target_commit_sha)
                        and any(
                            observation.identity.ci_run_id == ci.id
                            and observation.identity.commit_sha == ci.commit_sha.lower()
                            and observation.identity.actual_attempt == ci.run_attempt
                            and observation.identity.artifact_id == ci.artifact_id
                            and observation.identity.artifact_name == ci.artifact_name
                            for ci in selected_ci_runs
                        )
                    ]
                    if scope.repository_identity is None:
                        scoped_artifacts = []
                    for run_id in run_ids:
                        report_rows = []
                        if run_id is not None:
                            report_rows = conn.execute(
                                "SELECT report_id FROM reports WHERE project_id = ? AND run_id = ? "
                                "ORDER BY created_at, report_id",
                                (snapshot.project_id, run_id),
                            ).fetchall()
                        report_ids: list[Optional[str]] = [row["report_id"] for row in report_rows] or [None]
                        evidence = self._history_evidence_for_scope(
                            run_id, snapshot.project_id, scope, scopes,
                        )
                        for report_id in report_ids:
                            report = self.get_report(report_id) if report_id is not None else None
                            report_result = None
                            if (report is not None and report.project_id == snapshot.project_id
                                    and report.run_id == run_id):
                                report_result = self._report_result_for_scope(report, scope)
                            records.append(RepositoryHistoryRecord(
                                project_id=snapshot.project_id,
                                capture_id=capture.capture_id,
                                snapshot_id=snapshot.id,
                                source_scope=scope.source_scope,
                                source_item_key=scope.source_item_key,
                                repository_identity=scope.repository_identity,
                                target_commit_sha=scope.target_commit_sha,
                                repository_result=scope.repository_result,
                                legacy_code_diffs=list(scope.legacy_code_diffs),
                                legacy_ci_runs=list(scope.legacy_ci_runs),
                                analysis_run_id=run_id,
                                report_id=report_id,
                                report_result=report_result,
                                artifact_observations=scoped_artifacts,
                                evidence=evidence,
                            ))
            return records
        finally:
            conn.close()

    @staticmethod
    def _require_proposals(conn: sqlite3.Connection) -> None:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='action_proposals'").fetchone():
            raise NotImplementedError("Action proposal catalogue unavailable; writer migration required")

    @staticmethod
    def _proposal_json(data: dict) -> str:
        return json.dumps(data, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False)

    def _validate_submission(self, conn: sqlite3.Connection, raw: str):
        try:
            data = json.loads(raw)
            if set(data) != {'proposal', 'evidence'} or self._proposal_json(data) != raw:
                raise ValueError
            proposal = ActionProposal.model_validate(data['proposal'])
            evidence = [EvidenceItem.model_validate(item) for item in data['evidence']]
            # Only explicitly referenced DTOs belong in the immutable submission.
            if any(item.provenance.id not in proposal.evidence_ids for item in evidence):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("Invalid action proposal submission schema") from None
        self._require_history(conn)
        row = conn.execute("SELECT * FROM snapshot_captures WHERE capture_id = ?",
                           (proposal.observed_capture_id,)).fetchone()
        if row is None:
            raise ValueError("Action proposal capture unavailable")
        try:
            capture = self._parse_capture(row)
            errors = validate_action_proposal(proposal, capture, evidence)
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid action proposal capture") from None
        if errors:
            raise ValueError("Invalid action proposal bindings: " + ', '.join(errors))
        return proposal, evidence

    def _parse_proposal(self, conn: sqlite3.Connection, row: sqlite3.Row):
        raw = row['data_json']
        if type(raw) is not str or hashlib.sha256(raw.encode('utf-8')).hexdigest() != row['content_hash']:
            raise ValueError("Action proposal integrity mismatch")
        proposal, evidence = self._validate_submission(conn, raw)
        metadata = (proposal.proposal_id, proposal.project_id,
                    proposal.observed_capture_id, proposal.schema_version)
        if metadata != tuple(row[key] for key in ('proposal_id', 'project_id', 'capture_id', 'schema_version')):
            raise ValueError("Action proposal metadata mismatch")
        return proposal, evidence

    def save_action_proposal(self, proposal: ActionProposal,
                             supporting_evidence: list[EvidenceItem]) -> None:
        """Freeze full referenced, already source-normalized/sanitized DTOs.

        Callers supply sanitized EvidenceItems; this is not a sanitizer.
        SHA proves byte consistency only: not authorship, live state, permission,
        or an evidence-to-capture association. Parsed models are not identity.
        """
        if self.read_only:
            raise PermissionError("Action proposal catalogue is read-only")
        try:
            data = proposal.model_dump(mode='json', warnings='error')
            if set(proposal.__dict__) - set(data):
                raise ValueError
            evidence_dtos = [item.model_dump(mode='json', warnings='error') for item in supporting_evidence]
            selected = [item for item in evidence_dtos
                        if item['provenance']['id'] in data['evidence_ids']]
            raw = self._proposal_json({'proposal': data, 'evidence': selected})
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ValueError("Invalid action proposal submission DTO") from None
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require_proposals(conn)
                frozen, _ = self._validate_submission(conn, raw)
                old = conn.execute("SELECT * FROM action_proposals WHERE proposal_id = ?",
                                   (frozen.proposal_id,)).fetchone()
                if old:
                    self._parse_proposal(conn, old)
                    if old['data_json'] != raw:
                        raise ValueError("Action proposal ID conflicts with stored submission")
                    return
                conn.execute("""INSERT INTO action_proposals
                    (proposal_id, project_id, capture_id, schema_version, data_json, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (frozen.proposal_id, frozen.project_id, frozen.observed_capture_id,
                     frozen.schema_version, raw, hashlib.sha256(raw.encode('utf-8')).hexdigest()))
        finally:
            conn.close()

    def get_action_proposal(self, proposal_id: str) -> Optional[tuple[ActionProposal, list[EvidenceItem]]]:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN")
                self._require_proposals(conn)
                row = conn.execute("SELECT * FROM action_proposals WHERE proposal_id = ?",
                                   (proposal_id,)).fetchone()
                return self._parse_proposal(conn, row) if row else None
        finally:
            conn.close()

    def list_action_proposals(self, project_id: str) -> list[tuple[ActionProposal, list[EvidenceItem]]]:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN")
                self._require_proposals(conn)
                rows = conn.execute("""SELECT * FROM action_proposals WHERE project_id = ?
                                       ORDER BY proposal_id""", (project_id,))
                return [self._parse_proposal(conn, row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _require_review(conn: sqlite3.Connection) -> None:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='proposal_decisions'").fetchone():
            raise NotImplementedError("Proposal review ledger unavailable; writer migration required")

    def _review_context(self, conn, project_id, proposal_id):
        self._require_review(conn)
        self._require_proposals(conn)
        row = conn.execute("SELECT * FROM action_proposals WHERE project_id=? AND proposal_id=?",
                           (project_id, proposal_id)).fetchone()
        if row is None:
            return None
        proposal, evidence = self._parse_proposal(conn, row)
        capture = self._parse_capture(conn.execute(
            "SELECT * FROM snapshot_captures WHERE capture_id=?", (proposal.observed_capture_id,)).fetchone())
        digest, events = row['content_hash'], []
        # Validate all row bindings before filtering: forged scope metadata must not
        # silently omit an event from the requested ledger. No foreign payload escapes.
        for stored in conn.execute("SELECT * FROM proposal_decisions ORDER BY ordered_version"):
            raw = stored['data_json']
            try:
                if type(raw) is not str or hashlib.sha256(raw.encode()).hexdigest() != stored['content_hash']:
                    raise ValueError
                event = DecisionEvent.model_validate_json(raw)
                metadata = (event.event_id, event.project_id, event.proposal_id, event.capture_id,
                            event.proposal_body_sha256, event.expected_version + 1)
                if metadata != tuple(stored[key] for key in ('event_id', 'project_id', 'proposal_id',
                        'capture_id', 'proposal_body_sha256', 'ordered_version')):
                    raise ValueError
                if self._proposal_json(event.model_dump(mode='json')) != raw:
                    raise ValueError
                stamp = datetime.fromisoformat(stored['recorded_at'])
                if stamp.tzinfo != timezone.utc or stamp.isoformat() != stored['recorded_at']:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Proposal review ledger integrity mismatch") from None
            if (event.project_id, event.proposal_id) == (project_id, proposal_id):
                events.append(event)
        projection = fold_decisions(proposal, capture, evidence, digest, events)
        return proposal, capture, evidence, digest, events, projection

    def get_proposal_review_state(
        self, project_id: str, proposal_id: str,
    ) -> Optional[tuple[str, list[DecisionEvent], Projection]]:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN")
                context = self._review_context(conn, project_id, proposal_id)
                return (context[3], context[4], context[5]) if context else None
        finally:
            conn.close()

    def append_proposal_decision(
        self, event: DecisionEvent, *, security_audit_entry: Optional[SecurityAuditEntry] = None,
    ) -> Projection:
        """Atomically append a proposal event and its optional verified web audit event.

        Global event IDs: identical retries only; cross-scope reuse conflicts. Server recorded_at
        is UTC audit metadata, not an actor/authentication claim. No catalogue/status/latest-snapshot
        writes; projection is always folded.
        """
        if self.read_only:
            raise PermissionError("Proposal review ledger is read-only")
        try:
            event = DecisionEvent.model_validate(_raw(event))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid proposal decision event") from None
        raw = self._proposal_json(event.model_dump(mode='json'))
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                context = self._review_context(conn, event.project_id, event.proposal_id)
                if context is None:
                    raise ValueError("Proposal review binding unavailable")
                proposal, capture, evidence, digest, events, _ = context
                old = conn.execute("SELECT data_json FROM proposal_decisions WHERE event_id=?",
                                   (event.event_id,)).fetchone()
                if old and old['data_json'] != raw:
                    raise ValueError("event_id_conflict")
                fold_decisions(proposal, capture, evidence, digest, [*events, event])
                if not old:
                    conn.execute("""INSERT INTO proposal_decisions
                        (event_id, project_id, proposal_id, capture_id, proposal_body_sha256,
                         ordered_version, data_json, content_hash, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (event.event_id, event.project_id, event.proposal_id, event.capture_id,
                         event.proposal_body_sha256, event.expected_version + 1, raw,
                         hashlib.sha256(raw.encode()).hexdigest(), datetime.now(timezone.utc).isoformat()))
                if security_audit_entry is not None:
                    if (security_audit_entry.project_id != event.project_id
                            or security_audit_entry.resource_id != event.proposal_id):
                        raise ValueError("Proposal security audit binding mismatch")
                    self._append_or_validate_security_audit_event(conn, security_audit_entry)
            # Re-read committed rows in one consistent snapshot on this connection.
            with conn:
                conn.execute("BEGIN")
                return self._review_context(conn, event.project_id, event.proposal_id)[5]
        finally:
            conn.close()

    @staticmethod
    def _require_dry_runs(conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='proposal_dry_runs'").fetchone():
            raise NotImplementedError("Proposal dry-run audit unavailable; writer migration required")

    def _parse_dry_run(self, conn, row):
        try:
            models = []
            for prefix, model in [('request', DryRunRequest), ('result', DryRunResult)]:
                raw = row[prefix + '_json']
                if hashlib.sha256(raw.encode()).hexdigest() != row[prefix + '_sha256']:
                    raise ValueError
                checked = model.model_validate_json(raw, strict=True)
                if self._proposal_json(checked.model_dump(mode='json',
                        exclude=set(model.model_computed_fields))) != raw:
                    raise ValueError
                models.append(checked)
            request, result = models
            keys = tuple(DryRunRequest.model_fields)
            if tuple(getattr(request, key) for key in keys) != tuple(row[key] for key in keys):
                raise ValueError
            stamp = datetime.fromisoformat(row['recorded_at'])
            if (result.status != row['status'] or stamp.tzinfo != timezone.utc or
                    stamp.isoformat() != row['recorded_at']):
                raise ValueError
            context = self._review_context(conn, request.project_id, request.proposal_id)
            if context is None or (request.capture_id, request.proposal_body_sha256) != (
                    context[1].capture_id, context[3]):
                raise ValueError
            return request, result, row['recorded_at']
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ValueError("Proposal dry-run audit integrity mismatch") from None

    def get_proposal_dry_run(self, project_id: str, proposal_id: str, request_id: str
                             ) -> Optional[tuple[DryRunRequest, DryRunResult, str]]:
        """Historical lookup: later revocation does not revoke the recorded receipt."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN")
                self._require_dry_runs(conn)
                row = conn.execute("SELECT * FROM proposal_dry_runs WHERE request_id=?",
                                   (request_id,)).fetchone()
                if row is None:
                    return None
                receipt = self._parse_dry_run(conn, row)
                return receipt if (receipt[0].project_id, receipt[0].proposal_id) == (
                    project_id, proposal_id) else None
        finally:
            conn.close()

    def append_proposal_dry_run(
        self, request: DryRunRequest, *, security_audit_entry: Optional[SecurityAuditEntry] = None,
    ) -> tuple[DryRunRequest, DryRunResult, str, bool]:
        """Recheck guards before replay. Denied retries fail closed; inspect history.

        BEGIN IMMEDIATE serializes permission/version reads with ledger writers.
        No live invocation or mutable source/status write is performed.
        """
        if self.read_only:
            raise PermissionError("Proposal dry-run audit is read-only")
        request = _checked(DryRunRequest, request)  # Strict actual DTO, before connect.
        raw = self._proposal_json(request.model_dump(mode='json'))
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require_dry_runs(conn)
                context = self._review_context(conn, request.project_id, request.proposal_id)
                if context is None:
                    raise ValueError("Proposal dry-run binding unavailable")
                proposal, capture, evidence, digest, events, _ = context
                submission = conn.execute('SELECT data_json FROM action_proposals WHERE proposal_id=?',
                                          (request.proposal_id,)).fetchone()[0]
                _known_keys(json.loads(submission), {'proposal': _raw(proposal),
                                                    'evidence': [_raw(item) for item in evidence]})
                if (request.capture_id, request.proposal_body_sha256) != (capture.capture_id, digest):
                    raise ValueError("Proposal dry-run binding mismatch")
                # Verify before filtering: forged scope metadata cannot hide a capture.
                captures = []
                for row in conn.execute("SELECT * FROM snapshot_captures"):
                    item = self._parse_capture(row)
                    _known_keys(json.loads(row['data_json']), _raw(item.snapshot))
                    captures.append(item)
                captures = [item for item in captures if item.snapshot.project_id == request.project_id]
                result = simulate_proposal(request=request, proposal=proposal, capture=capture,
                    evidence_items=evidence, proposal_body_sha256=digest, events=events,
                    project_captures=captures)
                if security_audit_entry is not None:
                    if (security_audit_entry.project_id != request.project_id
                            or security_audit_entry.resource_id != request.proposal_id):
                        raise ValueError("Proposal security audit binding mismatch")
                    audit_decision = "granted" if result.status == "SIMULATED" else "denied"
                    security_audit_entry = security_audit_entry.model_copy(update={"decision": audit_decision})
                result_raw = self._proposal_json(result.model_dump(mode='json',
                    exclude=set(DryRunResult.model_computed_fields)))
                old = conn.execute("SELECT * FROM proposal_dry_runs WHERE request_id=?",
                                   (request.request_id,)).fetchone()
                if old:
                    receipt = self._parse_dry_run(conn, old)
                    if old['request_json'] != raw:
                        raise ValueError("request_id_conflict")
                    if result.status != 'SIMULATED' or old['result_json'] != result_raw:
                        raise ValueError("dry_run_retry_denied; inspect historical audit")
                    if security_audit_entry is not None:
                        self._append_or_validate_security_audit_event(conn, security_audit_entry)
                    return (*receipt, True)
                stamp = datetime.now(timezone.utc).isoformat()
                conn.execute("""INSERT INTO proposal_dry_runs
                    (request_id, project_id, proposal_id, proposal_body_sha256, capture_id,
                     permission_event_id, expected_version, status, recorded_at,
                     request_json, request_sha256, result_json, result_sha256)
                    VALUES (:request_id, :project_id, :proposal_id, :proposal_body_sha256,
                            :capture_id, :permission_event_id, :expected_version, :status,
                            :recorded_at, :request_json, :request_sha256, :result_json,
                            :result_sha256)""", {
                        'request_id': request.request_id,
                        'project_id': request.project_id,
                        'proposal_id': request.proposal_id,
                        'proposal_body_sha256': request.proposal_body_sha256,
                        'capture_id': request.capture_id,
                        'permission_event_id': request.permission_event_id,
                        'expected_version': request.expected_version,
                        'status': result.status,
                        'recorded_at': stamp,
                        'request_json': raw,
                        'request_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                        'result_json': result_raw,
                        'result_sha256': hashlib.sha256(result_raw.encode()).hexdigest(),
                    })
                if security_audit_entry is not None:
                    self._append_or_validate_security_audit_event(conn, security_audit_entry)
                return request, result, stamp, False
        finally:
            conn.close()

    def append_proposal_execution(
        self, request: ExecutionRequest, result: ExecutionResult, *, human_reason: str | None = None,
    ) -> None:
        """Atomically append an execution attempt and result to the append-only ledger."""
        if self.read_only:
            raise PermissionError("Proposal execution storage is read-only")
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO proposal_executions
                    (execution_id, request_id, project_id, proposal_id, status,
                     verification_status, actor_id, field, recorded_at, request_json, result_json, human_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result.execution_id,
                        request.request_id,
                        request.project_id,
                        request.proposal_id,
                        result.status,
                        result.verification_status,
                        result.actor_id,
                        result.field,
                        result.recorded_at.isoformat(),
                        request.model_dump_json(),
                        result.model_dump_json(),
                        human_reason,
                    ),
                )
        finally:
            conn.close()

    def list_proposal_executions(
        self, project_id: str, proposal_id: Optional[str] = None
    ) -> list[ExecutionResult]:
        conn = self._get_connection()
        try:
            if proposal_id:
                cur = conn.execute(
                    "SELECT result_json FROM proposal_executions WHERE project_id = ? AND proposal_id = ? ORDER BY recorded_at ASC",
                    (project_id, proposal_id),
                )
            else:
                cur = conn.execute(
                    "SELECT result_json FROM proposal_executions WHERE project_id = ? ORDER BY recorded_at ASC",
                    (project_id,),
                )
            return [ExecutionResult.model_validate_json(row["result_json"]) for row in cur.fetchall()]
        finally:
            conn.close()

    def list_proposal_execution_records(
        self, project_id: str,
    ) -> tuple[list[ProposalExecutionLedgerRecord], Literal["available", "unknown"]]:
        """Read allowlisted execution receipt metadata without creating any ledger schema."""
        connection = self._get_connection()
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )}
            if "proposal_executions" not in tables:
                return [], "unknown"
            execution_columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(proposal_executions)",
            )}
            required_columns = {
                "execution_id", "request_id", "project_id", "proposal_id", "status",
                "verification_status", "actor_id", "recorded_at", "result_json",
            }
            if not required_columns.issubset(execution_columns):
                return [], "unknown"
            outcomes_available = False
            if "proposal_execution_outcomes" in tables:
                outcome_columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(proposal_execution_outcomes)",
                )}
                outcomes_available = {"execution_id", "response_json", "response_sha256"}.issubset(
                    outcome_columns,
                )
            audit_columns = set()
            if "security_audit_log" in tables:
                audit_columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(security_audit_log)",
                )}
            select_human_reason = "human_reason" in execution_columns
            rows = connection.execute(
                "SELECT execution_id, request_id, project_id, proposal_id, status, verification_status, "
                "actor_id, recorded_at, result_json"
                + (", human_reason" if select_human_reason else "")
                + " FROM proposal_executions WHERE project_id=? ORDER BY recorded_at, execution_id",
                (project_id,),
            ).fetchall()
            records: list[ProposalExecutionLedgerRecord] = []
            availability: Literal["available", "unknown"] = (
                "available" if outcomes_available or not rows else "unknown"
            )
            for row in rows:
                try:
                    result = ExecutionResult.model_validate_json(row["result_json"])
                    recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
                    if (
                        row["project_id"] != project_id
                        or result.execution_id != row["execution_id"]
                        or result.request_id != row["request_id"]
                        or result.proposal_id != row["proposal_id"]
                        or result.status != row["status"]
                        or result.verification_status != row["verification_status"]
                        or result.actor_id != row["actor_id"]
                        or recorded_at != result.recorded_at
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise ValueError("Proposal execution ledger integrity mismatch") from None

                decision = None
                original_proposal_id = None
                audit_event_id = None
                actor_verification: Literal["verified_local_session", "unknown"] = "unknown"
                if outcomes_available:
                    outcome = connection.execute(
                        "SELECT response_json, response_sha256 FROM proposal_execution_outcomes "
                        "WHERE execution_id=?", (result.request_id,),
                    ).fetchone()
                    if outcome is None:
                        availability = "unknown"
                    else:
                        response_json = outcome["response_json"]
                        if (not isinstance(response_json, str)
                                or hashlib.sha256(response_json.encode("utf-8")).hexdigest()
                                != outcome["response_sha256"]):
                            raise ValueError("Proposal execution receipt integrity mismatch")
                        try:
                            receipt = json.loads(response_json)
                            execution_view = receipt["execution"]
                            decision_value = receipt["decision"]
                            original_value = receipt["original_proposal_id"]
                            valid_id = lambda value: (
                                isinstance(value, str)
                                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None
                                and value not in {".", ".."}
                            )
                            if (
                                not isinstance(receipt, dict)
                                or not isinstance(execution_view, dict)
                                or receipt.get("proposal_id") != result.proposal_id
                                or not valid_id(original_value)
                                or decision_value not in {
                                    "approve_and_execute", "modify_and_execute", "reject",
                                }
                                or execution_view.get("execution_id") != result.execution_id
                                or execution_view.get("result_status") != result.status
                                or execution_view.get("verification_status") != result.verification_status
                                or receipt.get("execution_mode") not in {"live", "sandbox"}
                                or execution_view.get("execution_mode") != receipt["execution_mode"]
                                or (result.execution_mode is not None
                                    and result.execution_mode != receipt["execution_mode"])
                            ):
                                raise ValueError
                            decision = decision_value
                            original_proposal_id = original_value
                        except (KeyError, TypeError, ValueError):
                            raise ValueError("Proposal execution receipt binding mismatch") from None

                        if "security_audit_log" in tables and {
                            "event_id", "project_id", "actor_id", "role", "permission_checked",
                            "resource_id", "decision", "reason", "token_fingerprint",
                        }.issubset(audit_columns):
                            expected_audit_id = "web-exec-" + hashlib.sha256(
                                "|".join((project_id, result.proposal_id, result.request_id)).encode("utf-8"),
                            ).hexdigest()[:32]
                            audit = connection.execute(
                                "SELECT event_id, project_id, actor_id, role, permission_checked, resource_id, "
                                "decision, reason, token_fingerprint FROM security_audit_log WHERE event_id=?",
                                (expected_audit_id,),
                            ).fetchone()
                            if audit is not None and (
                                audit["event_id"].startswith("web-")
                                and audit["project_id"] == project_id
                                and audit["actor_id"] == result.actor_id
                                and audit["role"] == "local_operator"
                                and audit["permission_checked"] == "web:proposal_execute"
                                and audit["resource_id"] == result.proposal_id
                                and audit["decision"] in {"granted", "denied"}
                                and isinstance(audit["reason"], str)
                                and audit["reason"].startswith("request_sha256=")
                                and re.fullmatch(r"sha256:[0-9a-f]{16}", str(audit["token_fingerprint"]))
                            ):
                                audit_event_id = expected_audit_id
                                actor_verification = "verified_local_session"

                human_reason = (
                    row["human_reason"] if select_human_reason else None
                )
                if human_reason is not None and not isinstance(human_reason, str):
                    human_reason = None
                records.append(ProposalExecutionLedgerRecord(
                    result=result,
                    decision=decision,
                    original_proposal_id=original_proposal_id,
                    human_reason=human_reason,
                    audit_event_id=audit_event_id,
                    actor_verification=actor_verification,
                ))
            return records, availability
        finally:
            connection.close()

    def get_proposal_execution(self, execution_id: str) -> Optional[ExecutionResult]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT result_json FROM proposal_executions WHERE execution_id = ?",
                (execution_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ExecutionResult.model_validate_json(row["result_json"])
        finally:
            conn.close()

    def save_run(self, run: ExecutionRun) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, project_id, status, created_at, updated_at,
                        snapshot_id, config_hash, snapshot_hash, policy_version,
                        prompt_versions, schema_versions, duration_seconds,
                        error_message, report_id, is_simulated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        duration_seconds=excluded.duration_seconds,
                        error_message=excluded.error_message,
                        report_id=excluded.report_id
                    """,
                    (
                        run.run_id,
                        run.project_id,
                        run.status.value,
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                        run.snapshot_id,
                        run.config_hash,
                        run.snapshot_hash,
                        run.policy_version,
                        json.dumps(run.prompt_versions),
                        json.dumps(run.schema_versions),
                        run.duration_seconds,
                        run.error_message,
                        run.report_id,
                        1 if run.is_simulated else 0,
                    ),
                )
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[ExecutionRun]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            stages = self.get_stage_results(run_id)
            return ExecutionRun(
                run_id=row["run_id"],
                project_id=row["project_id"],
                status=RunStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                snapshot_id=row["snapshot_id"],
                config_hash=row["config_hash"],
                snapshot_hash=row["snapshot_hash"],
                policy_version=row["policy_version"],
                prompt_versions=json.loads(row["prompt_versions"] or "{}"),
                schema_versions=json.loads(row["schema_versions"] or "{}"),
                stages=stages,
                duration_seconds=row["duration_seconds"],
                error_message=row["error_message"],
                report_id=row["report_id"],
                is_simulated=bool(row["is_simulated"]),
            )
        finally:
            conn.close()

    def find_run_by_fingerprint(self, snapshot_hash: str, config_hash: str) -> Optional[ExecutionRun]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT run_id FROM runs
                WHERE snapshot_hash = ? AND config_hash = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (snapshot_hash, config_hash),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self.get_run(row["run_id"])
        finally:
            conn.close()

    def get_all_runs_for_project(self, project_id: str) -> list[ExecutionRun]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT run_id FROM runs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
            rows = cur.fetchall()
            runs = []
            for r in rows:
                run = self.get_run(r["run_id"])
                if run:
                    runs.append(run)
            return runs
        finally:
            conn.close()

    def save_stage_result(self, run_id: str, stage: StageExecutionRecord) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO stage_results (
                        run_id, stage_name, status, duration_seconds,
                        output_summary, error_message, data_hash, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, stage_name) DO UPDATE SET
                        status=excluded.status,
                        duration_seconds=excluded.duration_seconds,
                        output_summary=excluded.output_summary,
                        error_message=excluded.error_message,
                        data_hash=excluded.data_hash,
                        completed_at=excluded.completed_at
                    """,
                    (
                        run_id,
                        stage.stage_name,
                        stage.status.value,
                        stage.duration_seconds,
                        stage.output_summary,
                        stage.error_message,
                        stage.data_hash,
                        stage.completed_at.isoformat() if stage.completed_at else None,
                    ),
                )
        finally:
            conn.close()

    def get_stage_results(self, run_id: str) -> dict[str, StageExecutionRecord]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM stage_results WHERE run_id = ?", (run_id,))
            rows = cur.fetchall()
            results = {}
            for r in rows:
                results[r["stage_name"]] = StageExecutionRecord(
                    stage_name=r["stage_name"],
                    status=StageStatus(r["status"]),
                    duration_seconds=r["duration_seconds"],
                    output_summary=r["output_summary"],
                    error_message=r["error_message"],
                    data_hash=r["data_hash"],
                    completed_at=datetime.fromisoformat(r["completed_at"]) if r["completed_at"] else None,
                )
            return results
        finally:
            conn.close()

    def save_snapshot(self, snapshot: WeeklySnapshot, content_hash: Optional[str] = None) -> None:
        self._save_snapshot(snapshot, content_hash, receipt=False)

    def save_snapshot_with_capture_receipt(
        self, snapshot: WeeklySnapshot, content_hash: Optional[str] = None,
    ) -> SnapshotCapture:
        """Return verified stored JSON identity only after the atomic save commits.

        A receipt proves stored byte consistency, not authorship or permission.
        The caller hash remains legacy latest-row metadata, never capture identity.
        """
        return self._save_snapshot(snapshot, content_hash, receipt=True)

    def _save_snapshot(
        self, snapshot: WeeklySnapshot, content_hash: Optional[str], *, receipt: bool,
    ) -> Optional[SnapshotCapture]:
        raw = snapshot.model_dump_json()
        capture_id = self._capture_metadata(raw)[0]
        artifact_observations = extract_artifact_observations(snapshot)
        conn = self._get_connection()
        try:
            with conn:
                # Capture, source index and latest snapshot projection commit or roll back together.
                conn.execute("BEGIN IMMEDIATE")
                self._insert_capture(conn, raw)
                if receipt:
                    row = conn.execute("SELECT * FROM snapshot_captures WHERE capture_id = ?",
                                       (capture_id,)).fetchone()
                    self._parse_capture(row)
                    if row["data_json"] != raw:
                        raise ValueError("Snapshot capture conflicts with stored JSON")
                for observation in artifact_observations:
                    self._insert_repository_artifact_observation(conn, capture_id, observation)
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        snapshot_id, project_id, cutoff_datetime, captured_at,
                        is_complete, incomplete_reason, data_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        data_json=excluded.data_json,
                        content_hash=excluded.content_hash
                    """,
                    (
                        snapshot.id,
                        snapshot.project_id,
                        snapshot.cutoff_datetime.isoformat(),
                        snapshot.captured_at.isoformat(),
                        1 if snapshot.is_complete else 0,
                        snapshot.incomplete_reason,
                        raw,
                        content_hash,
                    ),
                )
            if receipt:
                # Re-read the actual row using the same connection after commit.
                row = conn.execute("SELECT * FROM snapshot_captures WHERE capture_id = ?",
                                   (capture_id,)).fetchone()
                return self._parse_capture(row)
        finally:
            conn.close()

    def get_snapshot(self, snapshot_id: str) -> Optional[WeeklySnapshot]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT data_json FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            row = cur.fetchone()
            if not row:
                return None
            return WeeklySnapshot.model_validate_json(row["data_json"])
        finally:
            conn.close()

    def save_evidence(self, run_id: str, evidence_items: list[Any]) -> None:
        conn = self._get_connection()
        try:
            with conn:
                for ev in evidence_items:
                    if hasattr(ev, "provenance") and getattr(ev, "provenance", None) is not None:
                        prov = ev.provenance
                        ev_id = prov.id
                        source_val = (
                            prov.source.value
                            if hasattr(prov.source, "value")
                            else str(prov.source)
                        )
                        title_val = getattr(ev, "summary", "")
                        content_hash = compute_sha256(ev)
                    elif isinstance(ev, dict):
                        ev_id = ev.get("id", "")
                        source_obj = ev.get("source", "")
                        source_val = (
                            source_obj.value
                            if hasattr(source_obj, "value")
                            else str(source_obj)
                        )
                        title_val = ev.get("title", ev.get("summary", ""))
                        content_hash = ev.get("content_hash") or compute_sha256(ev)
                    else:
                        ev_id = getattr(ev, "id", "")
                        source_obj = getattr(ev, "source", "")
                        source_val = (
                            source_obj.value
                            if hasattr(source_obj, "value")
                            else str(source_obj)
                        )
                        title_val = getattr(ev, "title", getattr(ev, "summary", ""))
                        content_hash = getattr(ev, "content_hash", None) or compute_sha256(ev)

                    data_json = (
                        ev.model_dump_json()
                        if hasattr(ev, "model_dump_json")
                        else json.dumps(ev, default=str)
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO evidence (
                            id, run_id, source, title, content_hash, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ev_id,
                            run_id,
                            source_val,
                            title_val,
                            content_hash,
                            data_json,
                        ),
                    )
        finally:
            conn.close()

    def get_evidence(self, run_id: str) -> list[Any]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM evidence WHERE run_id = ?", (run_id,))
            rows = cur.fetchall()
            items = []
            for r in rows:
                data_str = r["data_json"]
                if data_str:
                    try:
                        items.append(EvidenceItem.model_validate_json(data_str))
                        continue
                    except Exception:
                        try:
                            items.append(json.loads(data_str))
                            continue
                        except Exception:
                            pass
                items.append({
                    "id": r["id"],
                    "run_id": r["run_id"],
                    "source": r["source"],
                    "title": r["title"],
                    "content_hash": r["content_hash"],
                })
            return items
        finally:
            conn.close()

    def save_findings(
        self,
        run_id: str,
        findings: list[CandidateFinding],
        escalation_decisions: Optional[list[Any]] = None,
    ) -> None:
        esc_map = {}
        if escalation_decisions:
            for ed in escalation_decisions:
                esc_map[ed.finding_id] = ed

        conn = self._get_connection()
        try:
            with conn:
                for f in findings:
                    dec = esc_map.get(f.id)
                    should_esc = 1 if (dec and dec.should_escalate) else 0
                    reasons = json.dumps(dec.reasons if dec else [])
                    cat_val = f.category.value if hasattr(f.category, "value") else str(f.category)
                    conn.execute(
                        """
                        INSERT INTO findings (
                            id, run_id, category, file_path, line, commit_sha,
                            evidence, suggested_fix, rule_id, confidence,
                            should_escalate, escalation_reasons, suggested_code, improvement_summary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id, run_id) DO UPDATE SET
                            category=excluded.category,
                            file_path=excluded.file_path,
                            line=excluded.line,
                            commit_sha=excluded.commit_sha,
                            evidence=excluded.evidence,
                            suggested_fix=excluded.suggested_fix,
                            rule_id=excluded.rule_id,
                            confidence=excluded.confidence,
                            should_escalate=excluded.should_escalate,
                            escalation_reasons=excluded.escalation_reasons,
                            suggested_code=CASE WHEN findings.suggested_code='' THEN excluded.suggested_code ELSE findings.suggested_code END,
                            improvement_summary=CASE WHEN findings.improvement_summary='' THEN excluded.improvement_summary ELSE findings.improvement_summary END
                        """,
                        (
                            f.id,
                            run_id,
                            cat_val,
                            f.file_path,
                            f.line,
                            f.commit_sha,
                            f.evidence,
                            f.suggested_fix,
                            f.rule_id,
                            f.confidence,
                            should_esc,
                            reasons,
                            f.suggested_code,
                            f.improvement_summary,
                        ),
                    )
        finally:
            conn.close()

    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM findings WHERE run_id = ?", (run_id,))
            rows = cur.fetchall()
            findings = []
            for r in rows:
                findings.append({
                    "finding_id": r["id"],
                    "category": r["category"],
                    "file_path": r["file_path"],
                    "line": r["line"],
                    "commit_sha": r["commit_sha"],
                    "evidence": r["evidence"],
                    "suggested_fix": r["suggested_fix"],
                    "rule_id": r["rule_id"],
                    "confidence": r["confidence"],
                    "should_escalate": bool(r["should_escalate"]),
                    "escalation_reasons": json.loads(r["escalation_reasons"] or "[]"),
                    "suggested_code": r["suggested_code"] if "suggested_code" in r.keys() else "",
                    "improvement_summary": r["improvement_summary"] if "improvement_summary" in r.keys() else "",
                    "decision_status": r["decision_status"] if "decision_status" in r.keys() else "pending",
                    "branch_name": r["branch_name"] if "branch_name" in r.keys() else None,
                    "branch_url": r["branch_url"] if "branch_url" in r.keys() else None,
                    "commit_sha_applied": r["commit_sha_applied"] if "commit_sha_applied" in r.keys() else None,
                })
            return findings
        finally:
            conn.close()

    def update_finding_metadata(
        self, run_id: str, finding_id: str, *, suggested_code: str | None = None,
        improvement_summary: str | None = None, decision_status: str | None = None,
        branch_name: str | None = None, branch_url: str | None = None,
        commit_sha_applied: str | None = None,
    ) -> None:
        """Update the normalized projection without replacing the append-only history."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE findings SET suggested_code=COALESCE(?, suggested_code), "
                    "improvement_summary=COALESCE(?, improvement_summary), "
                    "decision_status=COALESCE(?, decision_status), "
                    "branch_name=COALESCE(?, branch_name), branch_url=COALESCE(?, branch_url), "
                    "commit_sha_applied=COALESCE(?, commit_sha_applied) WHERE id=? AND run_id=?",
                    (suggested_code, improvement_summary, decision_status, branch_name,
                     branch_url, commit_sha_applied, finding_id, run_id),
                )
                if cursor.rowcount != 1:
                    raise LookupError("finding_not_found")
        finally:
            conn.close()

    def reserve_finding_decision(
        self, *, project_id: str, run_id: str, finding_id: str, idempotency_key: str,
        request_id: str, event_id: str, request_sha256: str, request_json: str,
        decision: str, actor_id: str, recorded_at: str,
    ) -> tuple[str, dict[str, Any]]:
        """Persist an idempotency receipt before any branch-side effect."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT * FROM finding_decision_requests WHERE project_id=? AND run_id=? "
                    "AND finding_id=? AND idempotency_key=?",
                    (project_id, run_id, finding_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    row = dict(existing)
                    if row["request_sha256"] != request_sha256:
                        raise FindingDecisionStorageError("idempotency_conflict")
                    if row["state"] == "processing":
                        raise FindingDecisionStorageError("decision_in_progress")
                    return "replay", row

                active = conn.execute(
                    "SELECT 1 FROM finding_decision_requests WHERE project_id=? AND run_id=? "
                    "AND finding_id=? AND state='processing'",
                    (project_id, run_id, finding_id),
                ).fetchone()
                if active is not None:
                    raise FindingDecisionStorageError("decision_in_progress")
                approved = conn.execute(
                    "SELECT 1 FROM finding_decision_requests WHERE project_id=? AND run_id=? "
                    "AND finding_id=? AND decision='approve'",
                    (project_id, run_id, finding_id),
                ).fetchone()
                if approved is not None:
                    raise FindingDecisionStorageError("approval_already_recorded")
                prior_approval = conn.execute(
                    "SELECT 1 FROM finding_decisions WHERE project_id=? AND run_id=? "
                    "AND finding_id=? AND decision_status='approved'",
                    (project_id, run_id, finding_id),
                ).fetchone()
                if prior_approval is not None:
                    raise FindingDecisionStorageError("finding_already_approved")
                conn.execute(
                    "INSERT INTO finding_decision_requests "
                    "(request_id, project_id, run_id, finding_id, idempotency_key, request_sha256, "
                    "request_json, decision, actor_id, state, event_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)",
                    (request_id, project_id, run_id, finding_id, idempotency_key, request_sha256,
                     request_json, decision, actor_id, event_id, recorded_at, recorded_at),
                )
                return "reserved", {"request_id": request_id, "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            raise FindingDecisionStorageError("decision_conflict") from exc
        finally:
            conn.close()

    def complete_finding_decision(
        self, *, request_id: str, result_json: str, decision_status: str,
        suggested_code: str, improvement_summary: str, branch_name: str | None,
        branch_url: str | None, commit_sha_applied: str | None,
        audit_entry: SecurityAuditEntry, recorded_at: str,
    ) -> None:
        """Commit normalized metadata, immutable decision, audit, and replay receipt together."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                request = conn.execute(
                    "SELECT * FROM finding_decision_requests WHERE request_id=?", (request_id,),
                ).fetchone()
                if request is None or request["state"] != "processing":
                    raise FindingDecisionStorageError("decision_receipt_unavailable")
                cursor = conn.execute(
                    "UPDATE findings SET suggested_code=?, improvement_summary=?, decision_status=?, "
                    "branch_name=?, branch_url=?, commit_sha_applied=? WHERE id=? AND run_id=?",
                    (suggested_code, improvement_summary, decision_status, branch_name, branch_url,
                     commit_sha_applied, request["finding_id"], request["run_id"]),
                )
                if cursor.rowcount != 1:
                    raise FindingDecisionStorageError("finding_not_found")
                conn.execute(
                    "INSERT INTO finding_decisions "
                    "(event_id, request_id, project_id, run_id, finding_id, decision, decision_status, "
                    "actor_id, idempotency_key, request_sha256, request_json, result_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (request["event_id"], request_id, request["project_id"], request["run_id"],
                     request["finding_id"], request["decision"], decision_status, request["actor_id"],
                     request["idempotency_key"], request["request_sha256"], request["request_json"],
                     result_json, recorded_at),
                )
                self._insert_security_audit_event(conn, audit_entry)
                conn.execute(
                    "UPDATE finding_decision_requests SET state='completed', result_json=?, updated_at=? "
                    "WHERE request_id=? AND state='processing'",
                    (result_json, recorded_at, request_id),
                )
        finally:
            conn.close()

    def fail_finding_decision(
        self, *, request_id: str, result_json: str, error_status: int, error_code: str,
        error_message: str, suggested_code: str, improvement_summary: str,
        audit_entry: SecurityAuditEntry, recorded_at: str,
    ) -> None:
        """Persist failure replay state and audit without retrying a possibly applied write."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                request = conn.execute(
                    "SELECT * FROM finding_decision_requests WHERE request_id=?", (request_id,),
                ).fetchone()
                if request is None or request["state"] != "processing":
                    raise FindingDecisionStorageError("decision_receipt_unavailable")
                cursor = conn.execute(
                    "UPDATE findings SET suggested_code=?, improvement_summary=?, decision_status='failed', "
                    "branch_name=NULL, branch_url=NULL, commit_sha_applied=NULL WHERE id=? AND run_id=?",
                    (suggested_code, improvement_summary, request["finding_id"], request["run_id"]),
                )
                if cursor.rowcount != 1:
                    raise FindingDecisionStorageError("finding_not_found")
                conn.execute(
                    "INSERT INTO finding_decisions "
                    "(event_id, request_id, project_id, run_id, finding_id, decision, decision_status, "
                    "actor_id, idempotency_key, request_sha256, request_json, result_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?)",
                    (request["event_id"], request_id, request["project_id"], request["run_id"],
                     request["finding_id"], request["decision"], request["actor_id"],
                     request["idempotency_key"], request["request_sha256"], request["request_json"],
                     result_json, recorded_at),
                )
                self._insert_security_audit_event(conn, audit_entry)
                conn.execute(
                    "UPDATE finding_decision_requests SET state='failed', result_json=?, error_status=?, "
                    "error_code=?, error_message=?, updated_at=? WHERE request_id=? AND state='processing'",
                    (result_json, error_status, error_code, error_message, recorded_at, request_id),
                )
        finally:
            conn.close()

    def save_verifications(self, run_id: str, verifications: list[FindingVerification]) -> None:
        conn = self._get_connection()
        try:
            with conn:
                for v in verifications:
                    verdict_val = v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO verifications (
                            finding_id, run_id, verdict, reason, verified_by, evidence_ids
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            v.finding_id,
                            run_id,
                            verdict_val,
                            v.reason,
                            v.verified_by,
                            json.dumps(v.evidence_ids),
                        ),
                    )
        finally:
            conn.close()

    def get_verifications(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM verifications WHERE run_id = ?", (run_id,))
            rows = cur.fetchall()
            verifs = []
            for r in rows:
                verifs.append({
                    "finding_id": r["finding_id"],
                    "verdict": r["verdict"],
                    "reason": r["reason"],
                    "verified_by": r["verified_by"],
                    "evidence_ids": json.loads(r["evidence_ids"] or "[]"),
                })
            return verifs
        finally:
            conn.close()

    def save_llm_attempts(self, run_id: str, attempts: list[LLMCallAttempt]) -> None:
        conn = self._get_connection()
        try:
            with conn:
                for att in attempts:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO llm_calls (
                            attempt_id, run_id, stage, attempt_number, provider,
                            model, status, finish_reason, duration_seconds,
                            prompt_tokens, completion_tokens, validation_passed,
                            validation_errors, error_message, structured_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            att.attempt_id,
                            run_id,
                            att.stage,
                            att.attempt_number,
                            att.provider,
                            att.model,
                            att.status.value if hasattr(att.status, "value") else str(att.status),
                            att.finish_reason,
                            att.duration_seconds,
                            att.prompt_tokens,
                            att.completion_tokens,
                            1 if att.validation_passed else 0,
                            json.dumps(att.validation_errors),
                            att.error_message,
                            json.dumps(att.structured_data) if att.structured_data else None,
                        ),
                    )
        finally:
            conn.close()

    def save_report(
        self,
        report: FinalReport,
        report_id: Optional[str] = None,
        report_md: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        rep_id = report_id or report.report_id or f"rep-{uuid.uuid4().hex[:8]}"
        report.report_id = rep_id
        if run_id:
            report.run_id = run_id

        conn = self._get_connection()
        try:
            now_iso = datetime.now(ZoneInfo("America/Guayaquil")).isoformat()
            with conn:
                conn.execute(
                    """
                    INSERT INTO reports (
                        report_id, run_id, project_id, version, created_at,
                        cutoff_datetime, recommendation, human_decision,
                        decision_reason, decision_timestamp, data_json, markdown_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_id) DO UPDATE SET
                        human_decision=excluded.human_decision,
                        decision_reason=excluded.decision_reason,
                        decision_timestamp=excluded.decision_timestamp,
                        data_json=excluded.data_json,
                        markdown_text=excluded.markdown_text
                    """,
                    (
                        rep_id,
                        report.run_id,
                        report.project_id,
                        report.report_version,
                        now_iso,
                        report.cutoff_datetime.isoformat(),
                        report.recommendation,
                        report.human_decision.value if hasattr(report.human_decision, "value") else str(report.human_decision),
                        report.decision_reason,
                        report.decision_timestamp.isoformat() if report.decision_timestamp else None,
                        report.model_dump_json(),
                        report_md,
                    ),
                )
        finally:
            conn.close()

    def get_latest_report(self, project_id: str) -> Optional[FinalReport]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT data_json, human_decision, decision_reason, decision_timestamp, report_id, run_id
                FROM reports
                WHERE project_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            rep = FinalReport.model_validate_json(row["data_json"])
            rep.report_id = row["report_id"]
            rep.run_id = row["run_id"]
            rep.human_decision = HumanDecisionState(row["human_decision"])
            rep.decision_reason = row["decision_reason"]
            if row["decision_timestamp"]:
                rep.decision_timestamp = datetime.fromisoformat(row["decision_timestamp"])
            return rep
        finally:
            conn.close()

    def get_report(self, report_id: str) -> Optional[FinalReport]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT data_json, human_decision, decision_reason, decision_timestamp, report_id, run_id
                FROM reports
                WHERE report_id = ?
                """,
                (report_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            rep = FinalReport.model_validate_json(row["data_json"])
            rep.report_id = row["report_id"]
            rep.run_id = row["run_id"]
            rep.human_decision = HumanDecisionState(row["human_decision"])
            rep.decision_reason = row["decision_reason"]
            if row["decision_timestamp"]:
                rep.decision_timestamp = datetime.fromisoformat(row["decision_timestamp"])
            return rep
        finally:
            conn.close()

    def register_human_decision(
        self,
        report_id: str,
        decision: HumanDecisionState,
        reason: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> FinalReport:
        """
        Records the manager decision against a report version.
        Guarantees that AI cannot approve; only human decision updates this state.
        """
        now = datetime.now(ZoneInfo("America/Guayaquil"))
        now_iso = now.isoformat()
        decision_id = f"dec-{uuid.uuid4().hex[:8]}"
        decision_str = decision.value if hasattr(decision, "value") else str(decision)

        conn = self._get_connection()
        try:
            with conn:
                # 1. Insert into human_decisions audit table
                conn.execute(
                    """
                    INSERT INTO human_decisions (id, report_id, run_id, decision, reason, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (decision_id, report_id, run_id, decision_str, reason, now_iso),
                )

                # 2. Update reports table
                conn.execute(
                    """
                    UPDATE reports
                    SET human_decision = ?, decision_reason = ?, decision_timestamp = ?
                    WHERE report_id = ? OR run_id = ?
                    """,
                    (decision_str, reason, now_iso, report_id, report_id),
                )
        finally:
            conn.close()

        # Retrieve and update model_dump
        rep = self.get_report(report_id)
        if not rep and run_id:
            rep = self.get_report(run_id)
        if not rep:
            # Maybe report_id was run_id, look up by run_id
            conn = self._get_connection()
            try:
                cur = conn.execute("SELECT report_id FROM reports WHERE run_id = ? LIMIT 1", (report_id,))
                row = cur.fetchone()
                if row:
                    rep = self.get_report(row["report_id"])
            finally:
                conn.close()

        if not rep:
            raise ValueError(f"Report not found for identifier '{report_id}'")

        rep.human_decision = decision
        rep.decision_reason = reason
        rep.decision_timestamp = now

        # Update JSON in reports table
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE reports SET data_json = ? WHERE report_id = ?",
                    (rep.model_dump_json(), rep.report_id),
                )
        finally:
            conn.close()

        return rep

    def register_human_decision_with_audit(
        self,
        report_id: str,
        decision: HumanDecisionState,
        reason: Optional[str],
        run_id: str,
        *,
        project_id: str,
        expected_version: int,
        expected_human_decision: str,
        audit_entry: SecurityAuditEntry,
    ) -> tuple[FinalReport, bool]:
        """Atomically apply a version-bound web report decision and its verified audit event.

        The legacy ``register_human_decision`` path remains unchanged for CLI callers.
        """
        if self.read_only:
            raise PermissionError("Human decision storage is read-only")
        decision_value = decision.value if isinstance(decision, HumanDecisionState) else str(decision)
        try:
            decision_value = HumanDecisionState(decision_value).value
            audit_entry = SecurityAuditEntry.model_validate(audit_entry.model_dump())
        except (TypeError, ValueError):
            raise ValueError("Invalid human decision or audit entry") from None
        if (audit_entry.project_id != project_id or audit_entry.resource_id != report_id
                or audit_entry.permission_checked != "web:report_decision"
                or audit_entry.role != "local_operator" or audit_entry.decision != "granted"):
            raise ValueError("Human decision audit binding is invalid")

        now = datetime.now(ZoneInfo("America/Guayaquil"))
        now_iso = now.isoformat()
        connection = self._get_connection()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT report_id, run_id, project_id, version, human_decision, decision_reason, data_json "
                    "FROM reports WHERE report_id=? AND project_id=?",
                    (report_id, project_id),
                ).fetchone()
                if row is None:
                    raise ValueError("report_not_found")
                try:
                    report = FinalReport.model_validate_json(row["data_json"])
                except (TypeError, ValueError):
                    raise ValueError("report_binding_invalid") from None
                stored_run_id = row["run_id"]
                if (stored_run_id is None or stored_run_id != run_id
                        or report.report_id != report_id or report.run_id != run_id
                        or report.project_id != project_id
                        or report.report_version != int(row["version"])):
                    raise ValueError("report_binding_invalid")
                run = connection.execute(
                    "SELECT project_id FROM runs WHERE run_id=?", (run_id,),
                ).fetchone()
                if run is None or run["project_id"] != project_id:
                    raise ValueError("report_binding_invalid")

                existing_audit = connection.execute(
                    "SELECT project_id, actor_id, role, permission_checked, resource_id, decision, reason, "
                    "token_fingerprint FROM security_audit_log WHERE event_id=?",
                    (audit_entry.event_id,),
                ).fetchone()
                if existing_audit is not None:
                    expected_audit = (
                        audit_entry.project_id, audit_entry.actor_id, audit_entry.role,
                        audit_entry.permission_checked, audit_entry.resource_id, audit_entry.decision,
                        audit_entry.reason, audit_entry.token_fingerprint,
                    )
                    if tuple(existing_audit) != expected_audit:
                        raise ValueError("idempotency_conflict")
                    matching = connection.execute(
                        "SELECT 1 FROM human_decisions WHERE report_id=? AND run_id=? AND decision=? "
                        "AND reason IS ? LIMIT 1",
                        (report_id, run_id, decision_value, reason),
                    ).fetchone()
                    if (row["human_decision"] != decision_value or report.human_decision.value != decision_value
                            or row["decision_reason"] != reason or matching is None):
                        raise ValueError("decision_not_reconciled")
                    return report, True

                if (int(row["version"]) != expected_version
                        or row["human_decision"] != expected_human_decision
                        or report.human_decision.value != expected_human_decision):
                    raise ValueError("report_version_obsolete")
                existing_decision = connection.execute(
                    "SELECT 1 FROM human_decisions WHERE report_id=? LIMIT 1", (report_id,),
                ).fetchone()
                if existing_decision is not None:
                    raise ValueError("human_decision_exists")

                decision_id = f"dec-{uuid.uuid4().hex[:8]}"
                connection.execute(
                    "INSERT INTO human_decisions (id, report_id, run_id, decision, reason, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (decision_id, report_id, run_id, decision_value, reason, now_iso),
                )
                updated_report = report.model_copy(update={
                    "human_decision": HumanDecisionState(decision_value),
                    "decision_reason": reason,
                    "decision_timestamp": now,
                })
                cursor = connection.execute(
                    "UPDATE reports SET human_decision=?, decision_reason=?, decision_timestamp=?, data_json=? "
                    "WHERE report_id=? AND project_id=? AND run_id=? AND version=? AND human_decision=?",
                    (decision_value, reason, now_iso, updated_report.model_dump_json(), report_id,
                     project_id, run_id, expected_version, expected_human_decision),
                )
                if cursor.rowcount != 1:
                    raise ValueError("report_version_obsolete")
                self._insert_security_audit_event(connection, audit_entry)
                return updated_report, False
        finally:
            connection.close()

    def append_outbox_receipt(
        self, receipt: OutboxReceipt, message: OutboxMessage, force: bool = False
    ) -> None:
        """Record notification outbox delivery attempt and receipt into SQLite audit storage."""
        if self.read_only:
            raise PermissionError("Outbox storage is read-only")
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO notification_outbox
                    (delivery_id, message_id, project_id, window_id, channel,
                     status, file_path, dispatched_at, message_json, receipt_json, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt.delivery_id,
                        receipt.message_id,
                        receipt.project_id,
                        receipt.window_id,
                        receipt.channel,
                        receipt.status,
                        receipt.file_path,
                        receipt.dispatched_at.isoformat(),
                        message.model_dump_json(),
                        receipt.model_dump_json(),
                        receipt.detail,
                    ),
                )
        finally:
            conn.close()

    def list_outbox_receipts(self, project_id: str) -> list[OutboxReceipt]:
        """List all delivery receipts recorded for a project ordered chronologically."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT receipt_json FROM notification_outbox WHERE project_id = ? ORDER BY dispatched_at ASC",
                (project_id,),
            )
            return [OutboxReceipt.model_validate_json(row["receipt_json"]) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_outbox_receipt_for_window(
        self, project_id: str, window_id: str
    ) -> Optional[OutboxReceipt]:
        """Check if an outbox delivery was already successfully recorded for project and window."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT receipt_json FROM notification_outbox WHERE project_id = ? AND window_id = ? AND status = 'DELIVERED' ORDER BY dispatched_at DESC LIMIT 1",
                (project_id, window_id),
            )
            row = cur.fetchone()
            return OutboxReceipt.model_validate_json(row["receipt_json"]) if row else None
        finally:
            conn.close()

    @staticmethod
    def _insert_security_audit_event(conn: sqlite3.Connection, entry: SecurityAuditEntry) -> None:
        conn.execute(
            """INSERT INTO security_audit_log
            (event_id, project_id, actor_id, role, permission_checked,
             resource_id, decision, reason, token_fingerprint, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.event_id,
                entry.project_id,
                entry.actor_id,
                entry.role,
                entry.permission_checked,
                entry.resource_id,
                entry.decision,
                entry.reason,
                entry.token_fingerprint,
                entry.recorded_at.isoformat(),
            ),
        )

    @classmethod
    def _append_or_validate_security_audit_event(
        cls, conn: sqlite3.Connection, entry: SecurityAuditEntry,
    ) -> bool:
        """Append the audit event unless the same immutable event is already present."""
        entry = SecurityAuditEntry.model_validate(entry.model_dump())
        row = conn.execute(
            "SELECT project_id, actor_id, role, permission_checked, resource_id, decision, reason, "
            "token_fingerprint FROM security_audit_log WHERE event_id=?",
            (entry.event_id,),
        ).fetchone()
        expected = (
            entry.project_id, entry.actor_id, entry.role, entry.permission_checked,
            entry.resource_id, entry.decision, entry.reason, entry.token_fingerprint,
        )
        if row is not None:
            if tuple(row) != expected:
                raise ValueError("security_audit_event_conflict")
            return False
        cls._insert_security_audit_event(conn, entry)
        return True

    def append_security_audit_event(self, entry: SecurityAuditEntry) -> None:
        """Record an immutable security audit event."""
        conn = self._get_connection()
        try:
            with conn:
                self._insert_security_audit_event(conn, entry)
        finally:
            conn.close()

    def list_security_audit_events(self, project_id: str) -> list[SecurityAuditEntry]:
        """List all security audit events recorded for a project ordered chronologically."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """SELECT event_id, project_id, actor_id, role, permission_checked,
                          resource_id, decision, reason, token_fingerprint, recorded_at
                   FROM security_audit_log
                   WHERE project_id = ? OR project_id = 'GLOBAL'
                   ORDER BY recorded_at ASC""",
                (project_id,),
            )
            entries = []
            for row in cur.fetchall():
                entries.append(
                    SecurityAuditEntry(
                        event_id=row["event_id"],
                        project_id=row["project_id"],
                        actor_id=row["actor_id"],
                        role=row["role"],
                        permission_checked=row["permission_checked"],
                        resource_id=row["resource_id"],
                        decision=row["decision"],
                        reason=row["reason"],
                        token_fingerprint=row["token_fingerprint"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    )
                )
            return entries
        finally:
            conn.close()
