"""Controlled G4 sandbox: React -> FastAPI -> SQLite -> GitHub sandbox adapter.

The served application never touches the project database, never reaches the
network and never writes outside a temporary scratch root. It seeds one
isolated run whose finding carries the whole authorization chain: a confirmed
defect, an exact patch and an independent favorable review of that patch, so
the interface can be exercised against the real backend policy instead of a
simulated one.

The GitHub adapter runs in sandbox mode: the branch and the commit are real
files in the scratch root and the pull request is explicitly simulated. No
credential is required and none is accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter  # noqa: E402
from agente_gestion.api.settings import ApiSettings, ProjectConfig  # noqa: E402
from agente_gestion.application.patch_review import PatchReviewService  # noqa: E402
from agente_gestion.domain.code import CodeDiffSnippet  # noqa: E402
from agente_gestion.domain.findings import (  # noqa: E402
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.patch_review import PatchReviewOutcome, PatchReviewVerdict  # noqa: E402
from agente_gestion.domain.policies import PolicyEvaluationResult  # noqa: E402
from agente_gestion.domain.report import FinalReport, RepositoryReportResult  # noqa: E402
from agente_gestion.domain.repositories import (  # noqa: E402
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus  # noqa: E402
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot  # noqa: E402
from agente_gestion.domain.tasks import DeterministicTaskMetrics  # noqa: E402

FRONTEND_ORIGIN = "http://127.0.0.1:4173"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 4176
PROJECT_ID = "G4-SANDBOX"
RUN_ID = "run-g4-sandbox"
SNAPSHOT_ID = "snapshot-g4-sandbox"
REPORT_ID = "report-g4-sandbox"
FINDING_ID = "backend:f-g4-sandbox"
TEST_OPERATOR_TOKEN = "g4-sandbox-operator-token-never-use-in-production"
REPOSITORY = "acme/backend"
REPOSITORY_ID = "backend"
BASE_BRANCH = "develop"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
FILE_PATH = "src/shared.py"
SOURCE = "def parse(value):\n    return value\n"
EXCERPT = "    return value"
SUGGESTED = "    return value.strip()"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
PATCH_SHA256 = hashlib.sha256(
    SOURCE.replace(EXCERPT, SUGGESTED, 1).encode("utf-8"),
).hexdigest()


class _DeterministicVerifier:
    """A fixed favorable verdict, so the demonstration never calls a model."""

    def review_patch(self, *, project_id, finding, patch, patched_region, evidence_ids=None, **kwargs):
        return PatchReviewOutcome(
            verdict=PatchReviewVerdict.favorable,
            reason=(
                "La correccion devuelve el valor normalizado y no altera el contexto "
                "del archivo completo revisado."
            ),
            evidence_ids=list(evidence_ids or []),
        )


def _preflight() -> Path:
    if os.environ.get("EXTERNAL_WRITES_ENABLED") != "false":
        raise RuntimeError("EXTERNAL_WRITES_ENABLED must be exactly false.")
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise RuntimeError("Python bytecode output must be disabled in the isolated server.")
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
        "GITHUB_TOKEN", "TRELLO_API_KEY", "TRELLO_TOKEN", "SMTP_USER", "SMTP_PASSWORD",
    ):
        if os.environ.get(name):
            raise RuntimeError(f"Credential environment variable must be absent: {name}.")
    return Path(tempfile.mkdtemp(prefix="g4-sandbox-"))


def _assert_owned_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((BACKEND_HOST, BACKEND_PORT))


def _seed(storage: SQLiteStorageAdapter) -> None:
    """Persist one run whose finding carries the whole authorization chain."""
    from datetime import datetime, timezone

    identity = RepositoryIdentity(id=REPOSITORY_ID, owner="acme", repo="backend")
    provenance = RepositoryProvenance(
        repository_identity=identity, commit_sha=COMMIT_SHA, source="multirepo",
    )
    finding = CandidateFinding(
        id=FINDING_ID, commit_sha=COMMIT_SHA, file_path=FILE_PATH, line=2,
        evidence=EXCERPT, evidence_ids=[COMMIT_SHA], suggested_code=SUGGESTED,
        suggested_fix="Normalizar el valor de entrada.",
        improvement_summary="Normaliza el valor de entrada.",
        repository_identity=identity, provenance=provenance,
    )
    snippet = CodeDiffSnippet(
        repository=REPOSITORY, repository_identity=identity, provenance=provenance,
        commit_sha=COMMIT_SHA, file_path=FILE_PATH, lines=[1, 2], content=SOURCE,
        evidence_scope="full_file", is_complete=True, total_lines=2, source_sha256=SOURCE_HASH,
    )
    state = RepositoryAnalysisState(
        identity=identity, branch=BASE_BRANCH, target_commit_sha=COMMIT_SHA,
        commit_resolution_status=CommitResolutionStatus.resolved,
    )
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    storage.save_snapshot(WeeklySnapshot(
        id=SNAPSHOT_ID, project_id=PROJECT_ID,
        cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc), captured_at=now,
        code_diffs=[snippet],
        repository_results=[RepositorySnapshotResult(
            repository_state=state, code_diffs=[snippet], is_complete=True,
        )],
    ))
    storage.save_findings(RUN_ID, [finding])
    storage.save_report(FinalReport(
        project_id=PROJECT_ID,
        cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metrics=DeterministicTaskMetrics(total_cards=0, included_cards=0, completed_cards=0),
        pending_findings=[finding], candidate_findings=[finding],
        policy_evaluations=PolicyEvaluationResult(
            evaluations=[], passed_rule_ids=[], failed_rule_ids=[], unknown_rule_ids=[],
            mandatory_failed_ids=[], mandatory_unknown_ids=[],
            recommended_action="evidencia_insuficiente",
        ),
        recommendation="evidencia_insuficiente",
        summary="Demostracion G4 aislada: revision y publicacion en sandbox.",
        report_id=REPORT_ID, run_id=RUN_ID,
        repository_results=[RepositoryReportResult(
            repository_state=state, pending_findings=[finding],
        )],
    ), report_id=REPORT_ID, run_id=RUN_ID)
    storage.save_run(ExecutionRun(
        run_id=RUN_ID, project_id=PROJECT_ID, status=RunStatus.awaiting_human,
        created_at=now, updated_at=now, snapshot_id=SNAPSHOT_ID,
        config_hash="a" * 64, snapshot_hash="b" * 64, report_id=REPORT_ID,
    ))
    storage.save_verifications(RUN_ID, [FindingVerification(
        finding_id=FINDING_ID, verdict=VerificationVerdict.confirmed,
        reason="Defecto confirmado con la evidencia del archivo completo.",
        verified_by="verifier_agent", evidence_ids=[COMMIT_SHA],
    )])
    PatchReviewService(storage, _DeterministicVerifier()).review(
        project_id=PROJECT_ID, run_id=RUN_ID, finding=finding,
        source=_source_snapshot(), original_excerpt=EXCERPT, suggested_code=SUGGESTED,
        repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        evidence_ids=[COMMIT_SHA],
    )


def _source_snapshot():
    from agente_gestion.domain.code_action import CodeSourceSnapshot

    return CodeSourceSnapshot(
        content=SOURCE, is_complete=True, repository=REPOSITORY, commit_sha=COMMIT_SHA,
        file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
    )


def _build_app(run_root: Path):
    from pydantic import SecretStr

    settings = ApiSettings(
        db_path=str(run_root / "g4-sandbox.sqlite"),
        runs_dir=str(run_root / "runs"),
        outbox_dir=str(run_root / "outbox"),
        job_ledger_path=str(run_root / "g4-sandbox-jobs.sqlite"),
        branches_dir=str(run_root / "branches"),
        projects=(ProjectConfig(
            id=PROJECT_ID, name="H5 (demo G4 sandbox)", source_mode="fixtures",
            repositories=(RepositoryConfiguration(
                id=REPOSITORY_ID, owner="acme", repo="backend", branch=BASE_BRANCH,
            ),),
        ),),
        analysis_enabled=False,
        governance_enabled=True,
        report_decisions_enabled=False,
        weekly_cycle_enabled=False,
        external_writes_enabled=False,
        github_repository=REPOSITORY,
        github_base_branch=BASE_BRANCH,
        operator_token=SecretStr(TEST_OPERATOR_TOKEN),
        operator_actor="gestora-g4-sandbox",
        operator_project_ids=(PROJECT_ID,),
        allowed_origins=(FRONTEND_ORIGIN,),
        allowed_hosts=(BACKEND_HOST,),
    )
    storage = SQLiteStorageAdapter(settings.db_path)
    _seed(storage)

    import agente_gestion.api.app as app_module

    app = app_module.create_app(settings)
    if app is app_module.app:
        raise RuntimeError("Refusing to serve the module-level app.")
    if app.state.settings.external_writes_enabled is not False:
        raise RuntimeError("The served app does not report external writes disabled.")
    if app.state.settings.github_token or app.state.settings.trello_api_key:
        raise RuntimeError("The served app must not carry external-service credentials.")

    manifest = {
        "demo_db": settings.db_path,
        "branches_dir": settings.branches_dir,
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "finding_id": FINDING_ID,
        "repository": REPOSITORY,
        "base_branch": BASE_BRANCH,
        "commit_sha": COMMIT_SHA,
        "file_path": FILE_PATH,
        "patch_sha256": PATCH_SHA256,
        "marker": "DEMOSTRACION G4 - CASO DE PRUEBA, SIN ESCRITURAS EXTERNAS",
    }
    (run_root / "sandbox-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return app


def main() -> int:
    run_root = _preflight()
    _assert_owned_port_available()
    app = _build_app(run_root)
    import uvicorn

    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
