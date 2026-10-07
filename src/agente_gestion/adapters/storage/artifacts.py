"""Filesystem artifact storage for run snapshots, metrics, reports, and audit logs."""

import json
import os
from typing import Any, Optional

from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics
from agente_gestion.domain.report import MonitoringDraft, FinalReport
from agente_gestion.domain.findings import CandidateFinding, FindingVerification


class ArtifactStorageAdapter:
    """Persists structured run artifacts to disk under runs/<run_id>/."""

    def __init__(self, base_dir: str = "runs"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _get_run_dir(self, run_id: str) -> str:
        run_dir = os.path.join(self.base_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def save_run_artifacts(
        self,
        run_id: str,
        snapshot: WeeklySnapshot,
        metrics: DeterministicTaskMetrics,
        monitoring: Optional[MonitoringDraft] = None,
        code_findings: Optional[list[CandidateFinding]] = None,
        verifications: Optional[list[FindingVerification]] = None,
        report: Optional[FinalReport] = None,
        report_md: Optional[str] = None,
        execution_metrics: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Saves all generated artifacts for a completed run into runs/<run_id>/.
        Returns the directory path.
        """
        run_dir = self._get_run_dir(run_id)

        # 1. snapshot.json
        with open(os.path.join(run_dir, "snapshot.json"), "w", encoding="utf-8") as f:
            f.write(snapshot.model_dump_json(indent=2))

        # 2. metrics.json
        with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as f:
            f.write(metrics.model_dump_json(indent=2))

        # 3. monitoring.json
        if monitoring:
            with open(os.path.join(run_dir, "monitoring.json"), "w", encoding="utf-8") as f:
                f.write(monitoring.model_dump_json(indent=2))

        # 4. code_findings.json
        if code_findings is not None:
            findings_data = [f.model_dump() for f in code_findings]
            with open(os.path.join(run_dir, "code_findings.json"), "w", encoding="utf-8") as f:
                json.dump(findings_data, f, indent=2, default=str)

        # 5. verification.json
        if verifications is not None:
            verifs_data = [v.model_dump() for v in verifications]
            with open(os.path.join(run_dir, "verification.json"), "w", encoding="utf-8") as f:
                json.dump(verifs_data, f, indent=2, default=str)

        # 6. report.json
        if report:
            with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
                f.write(report.model_dump_json(indent=2))

        # 7. report.md
        if report_md:
            with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
                f.write(report_md)

        # 8. execution_metrics.json
        if execution_metrics is not None:
            with open(os.path.join(run_dir, "execution_metrics.json"), "w", encoding="utf-8") as f:
                json.dump(execution_metrics, f, indent=2, default=str)

        return run_dir

    def save_partial_artifacts(
        self,
        run_id: str,
        snapshot: Optional[WeeklySnapshot] = None,
        metrics: Optional[DeterministicTaskMetrics] = None,
        monitoring: Optional[MonitoringDraft] = None,
        code_findings: Optional[list[CandidateFinding]] = None,
        verifications: Optional[list[FindingVerification]] = None,
        execution_metrics: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Saves diagnostic partial artifacts on failure.
        Never saves report.json/report.md as complete when execution failed.
        """
        run_dir = self._get_run_dir(run_id)

        if snapshot:
            with open(os.path.join(run_dir, "snapshot.json"), "w", encoding="utf-8") as f:
                f.write(snapshot.model_dump_json(indent=2))

        if metrics:
            with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as f:
                f.write(metrics.model_dump_json(indent=2))

        if monitoring:
            with open(os.path.join(run_dir, "monitoring.json"), "w", encoding="utf-8") as f:
                f.write(monitoring.model_dump_json(indent=2))

        if code_findings is not None:
            findings_data = [f.model_dump() for f in code_findings]
            with open(os.path.join(run_dir, "code_findings.json"), "w", encoding="utf-8") as f:
                json.dump(findings_data, f, indent=2, default=str)

        if verifications is not None:
            verifs_data = [v.model_dump() for v in verifications]
            with open(os.path.join(run_dir, "verification.json"), "w", encoding="utf-8") as f:
                json.dump(verifs_data, f, indent=2, default=str)

        if execution_metrics is not None:
            with open(os.path.join(run_dir, "execution_metrics.json"), "w", encoding="utf-8") as f:
                json.dump(execution_metrics, f, indent=2, default=str)

        return run_dir
