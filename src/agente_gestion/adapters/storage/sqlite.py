"""SQLite storage adapter for execution runs, audit records, and human decisions."""

import json
import os
import sqlite3
import uuid
from datetime import datetime
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
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.llm import LLMCallAttempt, GenerationStatus
from agente_gestion.domain.evidence import EvidenceItem


class SQLiteStorageAdapter(StoragePort):
    """
    Robust SQLite persistence adapter with explicit connection management.
    Never holds open transactions across network boundaries.
    """

    def __init__(self, db_path: str = ".runtime/agente.db"):
        self.db_path = db_path
        self._ensure_dir_and_schema()

    def _get_connection(self) -> sqlite3.Connection:
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
        conn = self._get_connection()
        try:
            with conn:
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
                        snapshot.model_dump_json(),
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
