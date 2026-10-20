"""Durable publication of a reviewed branch as a pull request.

The publication is the last step of the human-governed corrective workflow: a
manager approves one exact reviewed patch, the branch is created with that
content and this module turns that branch into one pull request against the
protected ``develop`` base.

Three properties are non-negotiable here:

* the branch checkpoint and the identity of the review that authorized it are
  persisted **before** the publication side effect, so a retry never creates a
  second branch, never repeats a human decision and only recovers the pull
  request that may be missing;
* a publication is only reported as published when the typed result matches the
  repository, head, base, commit and mode that were authorized; an injected
  result is never trusted because it says so;
* every transition is durable and audited, so an interface can read the real
  state of a publication instead of inferring it.

The ledger is created lazily on the first write, so an existing database is
never migrated just because it was opened.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import sqlite3
from typing import Any, Iterator, Optional

from agente_gestion.domain.code_action import BranchExecutionResult, PullRequestExecutionResult


PUBLICATION_BASE_BRANCH = "develop"
PROTECTED_HEAD_BRANCHES = frozenset({"main", "master", "develop"})

PUBLICATION_NOT_REQUESTED = "not_requested"
PUBLICATION_PUBLISHED = "published"
PUBLICATION_PARTIAL = "partial"

STAGE_BRANCH_CREATED = "branch_created"
STAGE_PUBLISHED = "published"
STAGE_PARTIAL = "partial"

LEDGER_TABLE = "finding_publications"


class PublicationError(RuntimeError):
    """Stable reason why a pull request could not be published or trusted."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PublicationOutcome:
    """The observable outcome of one publication attempt."""

    status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    pull_request_number: Optional[int] = None
    pull_request_url: Optional[str] = None
    pull_request_state: Optional[str] = None
    pull_request_simulated: Optional[bool] = None

    def result_fields(self) -> dict[str, object]:
        """The publication part of a decision receipt."""
        return {
            "publication_status": self.status,
            "publication_error_code": self.error_code,
            "publication_error_message": self.error_message,
            "pull_request_number": self.pull_request_number,
            "pull_request_url": self.pull_request_url,
            "pull_request_state": self.pull_request_state,
            "pull_request_simulated": self.pull_request_simulated,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publication_id(project_id: str, run_id: str, finding_id: str) -> str:
    canonical = "|".join((project_id, run_id, finding_id))
    return "fpub-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@contextmanager
def _storage_connection(storage: Any) -> Iterator[Optional[sqlite3.Connection]]:
    """Borrow one connection of the storage adapter and always give it back."""
    factory = getattr(storage, "_get_connection", None)
    if not callable(factory):
        yield None
        return
    connection = factory()
    try:
        yield connection
    finally:
        connection.close()


def _ensure_ledger(connection: sqlite3.Connection) -> None:
    """Create the publication ledger when it is used for the first time."""
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
            publication_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            stage TEXT NOT NULL CHECK(stage IN ('branch_created','published','partial')),
            repository TEXT NOT NULL,
            base_branch TEXT NOT NULL,
            head_branch TEXT NOT NULL,
            head_commit_sha TEXT NOT NULL,
            branch_url TEXT,
            branch_execution_mode TEXT NOT NULL,
            patch_sha256 TEXT NOT NULL,
            patch_review_id TEXT,
            pull_request_number INTEGER,
            pull_request_url TEXT,
            pull_request_state TEXT,
            pull_request_simulated INTEGER,
            publication_error_code TEXT,
            publication_error_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id, run_id, finding_id)
        )"""
    )
    connection.execute(
        f"""CREATE INDEX IF NOT EXISTS idx_finding_publications_lookup
            ON {LEDGER_TABLE}(project_id, run_id, finding_id)"""
    )


def _receipt(row: dict[str, Any]) -> dict[str, Any]:
    """The publication receipt of one durable ledger row."""
    simulated = row["pull_request_simulated"]
    stage = row["stage"]
    return {
        "publication_id": row["publication_id"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "finding_id": row["finding_id"],
        "publication_status": (
            PUBLICATION_PUBLISHED if stage == STAGE_PUBLISHED else PUBLICATION_PARTIAL
        ),
        "stage": stage,
        "publication_error_code": row["publication_error_code"],
        "publication_error_message": row["publication_error_message"],
        "pull_request_number": row["pull_request_number"],
        "pull_request_url": row["pull_request_url"],
        "pull_request_state": row["pull_request_state"],
        "pull_request_simulated": None if simulated is None else bool(simulated),
        "patch_review_id": row["patch_review_id"],
        "patch_sha256": row["patch_sha256"],
        "branch_name": row["head_branch"],
        "branch_url": row["branch_url"],
        "commit_sha_applied": row["head_commit_sha"],
        "repository": row["repository"],
        "base_branch": row["base_branch"],
        "execution_mode": row["branch_execution_mode"],
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def record_branch_checkpoint(
    storage: Any,
    *,
    project_id: str,
    run_id: str,
    finding_id: str,
    request_id: str,
    event_id: str,
    actor_id: str,
    idempotency_key: str,
    repository: str,
    base_branch: str,
    head_branch: str,
    head_commit_sha: str,
    branch_url: str | None,
    branch_execution_mode: str,
    patch_sha256: str,
    patch_review_id: str | None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Persist the branch checkpoint and the identity of its review.

    The checkpoint is written before any publication side effect, so a retry
    finds the exact branch and commit that were authorized. It is written once:
    a second call returns the persisted row instead of creating a second one.
    """
    moment = recorded_at or _utc_now()
    publication_id = _publication_id(project_id, run_id, finding_id)
    with _storage_connection(storage) as connection:
        if connection is None:
            raise PublicationError(
                "publicacion_no_disponible",
                "El almacenamiento no permite registrar la publicación.",
            )
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_ledger(connection)
            existing = connection.execute(
                f"SELECT * FROM {LEDGER_TABLE} WHERE project_id=? AND run_id=? AND finding_id=?",
                (project_id, run_id, finding_id),
            ).fetchone()
            if existing is not None:
                row = dict(existing)
                if (row["head_branch"] != head_branch
                        or row["head_commit_sha"] != head_commit_sha
                        or row["request_id"] != request_id):
                    raise PublicationError(
                        "publicacion_en_conflicto",
                        "Ya existe una publicación durable para este hallazgo con otra rama o commit.",
                    )
                return _receipt(row)
            connection.execute(
                f"INSERT INTO {LEDGER_TABLE} (publication_id, project_id, run_id, finding_id,"
                " request_id, event_id, actor_id, idempotency_key, stage, repository, base_branch,"
                " head_branch, head_commit_sha, branch_url, branch_execution_mode, patch_sha256,"
                " patch_review_id, attempts, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (
                    publication_id, project_id, run_id, finding_id, request_id, event_id,
                    actor_id, idempotency_key, STAGE_BRANCH_CREATED, repository, base_branch,
                    head_branch, head_commit_sha, branch_url, branch_execution_mode, patch_sha256,
                    patch_review_id, moment, moment,
                ),
            )
            stored = connection.execute(
                f"SELECT * FROM {LEDGER_TABLE} WHERE publication_id=?", (publication_id,),
            ).fetchone()
            return _receipt(dict(stored))


def _transition(
    storage: Any, publication_id: str, statement: str, values: tuple[Any, ...], recorded_at: str,
) -> dict[str, Any]:
    with _storage_connection(storage) as connection:
        if connection is None:
            raise PublicationError(
                "publicacion_no_disponible",
                "El almacenamiento no permite registrar la publicación.",
            )
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_ledger(connection)
            connection.execute(statement, values)
            stored = connection.execute(
                f"SELECT * FROM {LEDGER_TABLE} WHERE publication_id=?", (publication_id,),
            ).fetchone()
            if stored is None:
                raise PublicationError(
                    "publicacion_no_disponible",
                    "No existe el registro durable de la publicación.",
                )
            return _receipt(dict(stored))


def mark_publication_published(
    storage: Any,
    *,
    publication_id: str,
    pull_request_number: int,
    pull_request_url: str,
    pull_request_state: str,
    pull_request_simulated: bool,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Record a confirmed publication. A published row is never downgraded."""
    moment = recorded_at or _utc_now()
    return _transition(
        storage, publication_id,
        f"UPDATE {LEDGER_TABLE} SET stage='published', pull_request_number=?, pull_request_url=?,"
        " pull_request_state=?, pull_request_simulated=?, publication_error_code=NULL,"
        " publication_error_message=NULL, attempts=attempts+1, updated_at=?"
        f" WHERE publication_id=? AND stage IN ('branch_created','partial')",
        (
            pull_request_number, pull_request_url, pull_request_state,
            1 if pull_request_simulated else 0, moment, publication_id,
        ),
        moment,
    )


def mark_publication_partial(
    storage: Any,
    *,
    publication_id: str,
    error_code: str,
    error_message: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Record an interrupted publication without losing the branch checkpoint."""
    moment = recorded_at or _utc_now()
    return _transition(
        storage, publication_id,
        f"UPDATE {LEDGER_TABLE} SET stage='partial', publication_error_code=?,"
        " publication_error_message=?, attempts=attempts+1, updated_at=?"
        f" WHERE publication_id=? AND stage IN ('branch_created','partial')",
        (error_code, error_message, moment, publication_id),
        moment,
    )


def latest_finding_publication(
    storage: Any, *, project_id: str, run_id: str, finding_id: str,
) -> Optional[dict[str, Any]]:
    """The durable publication receipt of one finding, or nothing.

    This is the read side of the publication ledger: an interface shows the
    real state of the branch and of the pull request without inferring it from
    a decision that may be older than the publication.
    """
    with _storage_connection(storage) as connection:
        if connection is None:
            return None
        try:
            row = connection.execute(
                f"SELECT * FROM {LEDGER_TABLE} WHERE project_id=? AND run_id=? AND finding_id=?",
                (project_id, run_id, finding_id),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return _receipt(dict(row))


def validate_publication(
    *,
    publication: Any,
    branch_result: BranchExecutionResult,
    repository: str,
    base_branch: str,
) -> None:
    """Refuse any publication that is not the one that was authorized.

    The typed result is checked against the branch that was created, the
    repository that was requested, the protected base and the execution mode.
    A result that says it is live is not enough: it has to match.
    """
    if not isinstance(publication, PullRequestExecutionResult):
        raise PublicationError(
            "resultado_de_pull_request_invalido",
            "El adaptador no devolvió un resultado de pull request tipado.",
        )
    if publication.execution_mode != branch_result.execution_mode:
        raise PublicationError(
            "modo_de_publicacion_inconsistente",
            "El modo de la publicación no coincide con el modo de la rama creada.",
        )
    if publication.repository.casefold() != repository.casefold():
        raise PublicationError(
            "publicacion_no_coincidente",
            "El pull request publicado pertenece a otro repositorio.",
        )
    if publication.base_branch != base_branch:
        raise PublicationError(
            "rama_base_no_protegida",
            "El pull request debe abrirse contra la rama base protegida develop.",
        )
    if publication.head_branch != branch_result.branch_name:
        raise PublicationError(
            "rama_cabecera_inconsistente",
            "El pull request publicado apunta a otra rama.",
        )
    if publication.head_commit_sha != branch_result.commit_sha_applied:
        raise PublicationError(
            "commit_de_cabecera_inconsistente",
            "El pull request publicado no apunta al commit revisado.",
        )
    expected_state = "simulated" if branch_result.execution_mode == "sandbox" else "open"
    if publication.state != expected_state:
        raise PublicationError(
            "estado_de_pull_request_inconsistente",
            "El estado del pull request no coincide con el modo de ejecución.",
        )
    if branch_result.execution_mode == "sandbox":
        expected_prefix = "sandbox://pulls/"
    else:
        expected_prefix = f"https://github.com/{publication.repository}/pull/"
    if (not publication.pull_request_url.startswith(expected_prefix)
            or not publication.pull_request_url.endswith(f"/{publication.pull_request_number}")):
        raise PublicationError(
            "url_de_pull_request_invalida",
            "La URL del pull request no corresponde al repositorio y al número publicados.",
        )


def publish_pull_request(
    *,
    code_action: Any,
    repository: str,
    branch_result: BranchExecutionResult,
    finding_id: str,
    actor: str,
    patch_sha256: str,
    base_branch: str = PUBLICATION_BASE_BRANCH,
) -> PublicationOutcome:
    """Publish one pull request for an existing branch and validate the result.

    The protected base is always ``develop`` and the head branch can never be a
    protected branch. Any failure, including an uncertain one, is reported as a
    refusal: nothing here ever claims a publication that was not confirmed.
    """
    if not callable(getattr(code_action, "create_pull_request", None)):
        raise PublicationError(
            "publicacion_no_disponible",
            "El adaptador de acciones de código no ofrece la publicación de pull requests.",
        )
    if not isinstance(branch_result, BranchExecutionResult):
        raise PublicationError(
            "resultado_de_rama_invalido",
            "El resultado de la rama no es válido.",
        )
    if branch_result.branch_name in PROTECTED_HEAD_BRANCHES or branch_result.branch_name == base_branch:
        raise PublicationError(
            "rama_cabecera_invalida",
            "La rama de cabecera no puede ser una rama protegida ni la rama base.",
        )
    try:
        publication = code_action.create_pull_request(
            repo=repository,
            base_branch=base_branch,
            branch_result=branch_result,
            finding_id=finding_id,
            actor=actor,
            patch_sha256=patch_sha256,
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or (str(exc) or None)
        raise PublicationError(
            code if isinstance(code, str) and code else "publicacion_no_confirmada",
            message or "El adaptador no pudo confirmar la publicación del pull request.",
        ) from None
    validate_publication(
        publication=publication, branch_result=branch_result, repository=repository,
        base_branch=base_branch,
    )
    return PublicationOutcome(
        status=PUBLICATION_PUBLISHED,
        pull_request_number=publication.pull_request_number,
        pull_request_url=publication.pull_request_url,
        pull_request_state=publication.state,
        pull_request_simulated=publication.state == "simulated",
    )


__all__ = [
    "LEDGER_TABLE",
    "PROTECTED_HEAD_BRANCHES",
    "PUBLICATION_BASE_BRANCH",
    "PUBLICATION_NOT_REQUESTED",
    "PUBLICATION_PARTIAL",
    "PUBLICATION_PUBLISHED",
    "PublicationError",
    "PublicationOutcome",
    "latest_finding_publication",
    "mark_publication_partial",
    "mark_publication_published",
    "publish_pull_request",
    "record_branch_checkpoint",
    "validate_publication",
]
