"""SQLite storage adapter for execution runs, audit records, and human decisions."""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agente_gestion.ports.storage import StoragePort
from agente_gestion.domain.run import (
    ExecutionRun,
    RunStatus,
    StageExecutionRecord,
    StageStatus,
    compute_sha256,
)
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.llm import LLMCallAttempt, GenerationStatus
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal


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
        raw = snapshot.model_dump_json()
        conn = self._get_connection()
        try:
            with conn:
                # Explicit BEGIN also makes legacy read-only saves fail before any
                # history-table lookup. Both writes commit or roll back together.
                conn.execute("BEGIN IMMEDIATE")
                self._insert_capture(conn, raw)
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
                        INSERT OR REPLACE INTO findings (
                            id, run_id, category, file_path, line, commit_sha,
                            evidence, suggested_fix, rule_id, confidence,
                            should_escalate, escalation_reasons
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                })
            return findings
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
