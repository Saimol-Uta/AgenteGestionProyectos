"""Safe GitHub branch and pull request writes with an explicit local sandbox fallback."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable
from urllib.parse import quote

import requests

from agente_gestion.domain.code import validate_source_syntax
from agente_gestion.domain.code_action import (
    BranchExecutionResult,
    CodeSourceSnapshot,
    FindingActionOrigin,
    PullRequestExecutionResult,
)
from agente_gestion.ports.code_action_port import CodeActionPort


_REPOSITORY = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")
_BRANCH_FORBIDDEN = re.compile(r"[\x00-\x20~^:?*\\\[]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_TEXT_BYTES = 5 * 1024 * 1024
_FINDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?::{1,2}[A-Za-z0-9][A-Za-z0-9._-]*)*")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PROTECTED_PULL_REQUEST_BASE = "develop"
_PROTECTED_HEAD_BRANCHES = frozenset({"main", "master", "develop"})
_PULL_REQUEST_MANIFEST_DIRECTORY = ".pull_requests"
_SANDBOX_NUMBER_BASE = 1000
_SANDBOX_NUMBER_RANGE = 9000


def _same_repository(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.casefold() == right.casefold())


class CodeActionError(RuntimeError):
    """Safe, non-secret error from a refused or unavailable code action."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _validate_finding_id(finding_id: str) -> str:
    if (not isinstance(finding_id, str) or len(finding_id) > 128
            or not _FINDING_ID.fullmatch(finding_id)):
        raise CodeActionError("hallazgo_invalido", "El identificador del hallazgo no es válido.", 422)
    return finding_id


def _validate_actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip() or _CONTROL.search(actor):
        raise CodeActionError("actor_invalido", "El actor de la decisión no es válido.", 422)
    return actor


def _validate_patch_sha256(patch_sha256: str) -> str:
    if not isinstance(patch_sha256, str) or not _SHA256.fullmatch(patch_sha256):
        raise CodeActionError("parche_invalido", "El hash del parche revisado no es válido.", 422)
    return patch_sha256


class _RequestsGitHubClient:
    """Small injectable HTTP boundary; redirects are disabled for credential safety."""

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        response = requests.request(
            method, url, headers=headers, json=body, timeout=20, allow_redirects=False,
        )
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError):
            payload = {}
        return response.status_code, payload


class GitHubCodeActionAdapter(CodeActionPort):
    """Create an exact-match patch on a new GitHub branch or under branches_dir."""

    def __init__(
        self,
        branches_dir: str = ".runtime/branches",
        *,
        token: str | None = None,
        repository: str | None = None,
        base_branch: str = "main",
        external_writes_enabled: bool = False,
        project_mode: str = "fixtures",
        http_client=None,
        source_lookup: Callable[[str, str], CodeSourceSnapshot | None] | None = None,
        origin: FindingActionOrigin | None = None,
    ):
        if not isinstance(branches_dir, str) or not branches_dir.strip():
            raise CodeActionError("sandbox_unconfigured", "No está configurada la carpeta local de ramas.", 503)
        self.branches_dir = branches_dir
        self.token = token
        self.repository = repository
        self.base_branch = self._validate_branch(base_branch)
        self.external_writes_enabled = external_writes_enabled is True
        self.project_mode = project_mode
        self.http_client = http_client or _RequestsGitHubClient()
        self.source_lookup = source_lookup
        self.origin = origin
        if repository is not None:
            self._validate_repository(repository)
        if origin is not None and (
            not origin.configured_for_code_action or not origin.repository
            or not origin.commit_sha or not origin.file_path or not origin.base_branch
            or repository is None or not _same_repository(repository, origin.repository)
            or self.base_branch != origin.base_branch
        ):
            raise CodeActionError(
                "fuente_no_verificable",
                "El adaptador no está enlazado al repositorio, SHA y rama base del hallazgo.", 409,
            )

    @staticmethod
    def _validate_repository(repo: str) -> str:
        if not isinstance(repo, str) or not _REPOSITORY.fullmatch(repo):
            raise CodeActionError("repositorio_invalido", "El repositorio configurado no es válido.", 422)
        owner, name = repo.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            raise CodeActionError("repositorio_invalido", "El repositorio configurado no es válido.", 422)
        return repo

    @staticmethod
    def _validate_branch(branch: str) -> str:
        if (not isinstance(branch, str) or not branch or branch == "@" or len(branch) > 240
                or branch.startswith(("/", "-")) or branch.endswith(("/", "."))
                or ".." in branch or "@{" in branch or "//" in branch
                or "\x7f" in branch or _BRANCH_FORBIDDEN.search(branch)):
            raise CodeActionError("rama_invalida", "El nombre de rama no es válido.", 422)
        parts = branch.split("/")
        if any(not part or part.startswith(".") or part.endswith(".lock") for part in parts):
            raise CodeActionError("rama_invalida", "El nombre de rama no es válido.", 422)
        return branch

    @staticmethod
    def _validate_file_path(file_path: str) -> str:
        if (not isinstance(file_path, str) or not file_path or len(file_path) > 512
                or file_path.startswith(("/", "\\")) or ":" in file_path
                or "\\" in file_path or "?" in file_path or "#" in file_path
                or _CONTROL.search(file_path)):
            raise CodeActionError("ruta_invalida", "La ruta del archivo no es segura.", 422)
        parts = file_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise CodeActionError("ruta_invalida", "La ruta del archivo no es segura.", 422)
        return file_path

    @staticmethod
    def _validate_text(text: str, field: str) -> str:
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise CodeActionError("parche_invalido", f"El campo {field} no contiene texto válido.", 422)
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise CodeActionError("parche_demasiado_grande", "El parche supera el tamaño permitido.", 422)
        return text

    @staticmethod
    def _patch(source: str, original_excerpt: str, suggested_code: str, file_path: str,
               *, complete: bool) -> str:
        if not complete:
            raise CodeActionError(
                "fuente_no_verificable", "Se requiere el archivo completo capturado para crear una rama.", 409,
            )
        if not original_excerpt or source.count(original_excerpt) != 1:
            raise CodeActionError(
                "parche_obsoleto_o_ambiguo",
                "El fragmento original no coincide exactamente una sola vez con el archivo; no se aplicó el parche.",
                409,
            )
        updated = source.replace(original_excerpt, suggested_code, 1)
        syntax_status = validate_source_syntax(file_path, updated)
        if syntax_status == "unsupported":
            raise CodeActionError(
                "validacion_sintaxis_no_soportada",
                "No hay un validador seguro disponible para la sintaxis de este tipo de archivo.", 422,
            )
        if syntax_status == "invalid":
            raise CodeActionError(
                "codigo_sugerido_invalido",
                "El código sugerido no conserva la sintaxis del archivo completo.", 422,
            )
        return updated

    def _source_snapshot(self, repo: str, file_path: str) -> CodeSourceSnapshot:
        if self.origin is not None and (
            not self.origin.configured_for_code_action
            or not _same_repository(repo, self.origin.repository)
            or not _same_repository(self.repository, self.origin.repository)
            or file_path != self.origin.file_path
        ):
            raise CodeActionError(
                "fuente_no_verificable", "El destino no coincide con el origen fijado del hallazgo.", 409,
            )
        if self.source_lookup is None:
            raise CodeActionError(
                "fuente_no_verificable", "No hay una fuente completa capturada para crear la rama.", 409,
            )
        value = self.source_lookup(repo, file_path)
        if (not isinstance(value, CodeSourceSnapshot) or not value.is_complete
                or not _same_repository(value.repository, repo) or value.file_path != file_path
                or not value.commit_sha or not value.source_sha256
                or hashlib.sha256(value.content.encode("utf-8")).hexdigest() != value.source_sha256
                or value.total_lines != len(value.content.splitlines())
                or (self.origin is not None and (
                    value.commit_sha.lower() != self.origin.commit_sha
                    or value.source_sha256 != self.origin.source_sha256
                    or (self.origin.source_blob_sha is not None
                        and value.source_blob_sha != self.origin.source_blob_sha)
                ))):
            raise CodeActionError(
                "fuente_no_verificable", "No hay una fuente completa y coherente para crear la rama.", 409,
            )
        return value

    def _sandbox_root(self) -> Path:
        root = Path(self.branches_dir).expanduser().absolute()
        current = Path(root.anchor)
        for part in root.parts[1:]:
            current = current / part
            if current.is_symlink():
                raise CodeActionError("ruta_sandbox_insegura", "La carpeta sandbox contiene un enlace simbólico.", 409)
        try:
            root.mkdir(parents=True, exist_ok=True)
            resolved = root.resolve(strict=True)
        except OSError:
            raise CodeActionError("sandbox_no_disponible", "No se pudo preparar la carpeta sandbox.", 503) from None
        if not resolved.is_dir():
            raise CodeActionError("sandbox_no_disponible", "La carpeta sandbox no es un directorio.", 503)
        return resolved

    def _write_sandbox(self, branch: str, file_path: str, content: str, repo: str | None = None) -> None:
        root = self._sandbox_root()
        scope_parts: list[str] = []
        if (self.origin is not None and repo is not None
                and (self.origin.multi_repository or self.origin.repository_identity is not None)):
            scope = hashlib.sha256(repo.casefold().encode("utf-8")).hexdigest()[:16]
            scope_parts = ["repositories", scope]
        relative_parts = [*scope_parts, *branch.split("/"), *file_path.split("/")]
        destination = root
        for part in relative_parts[:-1]:
            destination = destination / part
            try:
                destination.mkdir(exist_ok=True)
            except OSError:
                raise CodeActionError("sandbox_no_disponible", "No se pudo preparar el destino sandbox.", 503) from None
            if destination.is_symlink() or not destination.resolve().is_relative_to(root):
                raise CodeActionError("ruta_sandbox_insegura", "La ruta sandbox sale del directorio permitido.", 409)
        target = destination / relative_parts[-1]
        if target.is_symlink() or not target.resolve(strict=False).is_relative_to(root):
            raise CodeActionError("ruta_sandbox_insegura", "La ruta sandbox sale del directorio permitido.", 409)
        if target.exists():
            if not target.is_file():
                raise CodeActionError("destino_sandbox_conflictivo", "El destino sandbox ya está ocupado.", 409)
            try:
                existing = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise CodeActionError("destino_sandbox_conflictivo", "El archivo sandbox existente no se puede validar.", 409) from None
            if existing != content:
                raise CodeActionError("destino_sandbox_conflictivo", "La rama sandbox ya contiene otro contenido; no se sobrescribió.", 409)
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
        except FileExistsError:
            raise CodeActionError("destino_sandbox_conflictivo", "El destino sandbox cambió durante la escritura.", 409) from None
        except OSError:
            raise CodeActionError("sandbox_no_disponible", "No se pudo escribir el archivo sandbox.", 503) from None

    def _live_ready(self, repo: str) -> bool:
        return bool(
            self.external_writes_enabled and self.project_mode == "real"
            and self.token and self.repository and repo == self.repository
        )

    @staticmethod
    def _github_path(repo: str, path: str) -> str:
        return "/".join(quote(part, safe="") for part in path.split("/"))

    def _github_request(self, method: str, path: str, body=None, *, allow_list: bool = False):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self.token}",
        }
        try:
            status, payload = self.http_client.request(
                method, "https://api.github.com" + path, headers, body,
            )
        except (requests.RequestException, OSError, TimeoutError):
            raise CodeActionError(
                "github_no_disponible", "GitHub no respondió; no se confirmó ninguna rama remota.", 503,
            ) from None
        if not isinstance(status, int):
            raise CodeActionError("respuesta_github_invalida", "GitHub devolvió una respuesta no válida.", 502)
        if not isinstance(payload, dict) and not (allow_list and isinstance(payload, list)):
            raise CodeActionError("respuesta_github_invalida", "GitHub devolvió una respuesta no válida.", 502)
        return status, payload

    def _live_branch(
        self, repo: str, base_branch: str, target_branch: str, file_path: str,
        original_excerpt: str, suggested_code: str, finding_id: str,
        source_snapshot: CodeSourceSnapshot,
    ) -> BranchExecutionResult | None:
        owner, name = self._validate_repository(repo).split("/", 1)
        encoded_repo = f"{quote(owner, safe='')}/{quote(name, safe='')}"
        status, repository_info = self._github_request("GET", f"/repos/{encoded_repo}")
        if status in {401, 403, 404}:
            return None
        if status != 200:
            raise CodeActionError("github_no_disponible", "No se pudo confirmar el permiso de escritura de GitHub.", 503)
        permissions = repository_info.get("permissions")
        if not isinstance(permissions, dict) or permissions.get("push") is not True:
            return None

        safe_base = self._validate_branch(base_branch)
        safe_target = self._validate_branch(target_branch)
        ref_path = quote(safe_base, safe="/")
        status, ref = self._github_request("GET", f"/repos/{encoded_repo}/git/ref/heads/{ref_path}")
        if status != 200 or not isinstance(ref.get("object"), dict) or not ref["object"].get("sha"):
            raise CodeActionError("rama_base_no_disponible", "No se pudo leer la rama base configurada.", 503)
        base_sha = str(ref["object"]["sha"])
        if base_sha != source_snapshot.commit_sha:
            raise CodeActionError(
                "fuente_obsoleta", "La rama base ya no coincide con el commit de la fuente validada.", 409,
            )
        encoded_file = self._github_path(repo, file_path)
        status, file_data = self._github_request(
            "GET", f"/repos/{encoded_repo}/contents/{encoded_file}?ref={quote(source_snapshot.commit_sha, safe='')}"
        )
        if status != 200 or file_data.get("encoding") != "base64":
            raise CodeActionError("archivo_github_no_disponible", "No se pudo leer el archivo desde la rama base.", 409)
        try:
            source = base64.b64decode(file_data["content"], validate=True).decode("utf-8")
        except (KeyError, ValueError, UnicodeError, TypeError):
            raise CodeActionError("archivo_github_invalido", "El archivo remoto no es texto UTF-8 válido.", 409) from None
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > _MAX_TEXT_BYTES:
            raise CodeActionError("archivo_github_demasiado_grande", "El archivo remoto supera el tamaño permitido.", 422)
        remote_blob_sha = file_data.get("sha")
        if (source != source_snapshot.content
                or hashlib.sha256(source_bytes).hexdigest() != source_snapshot.source_sha256
                or (source_snapshot.source_blob_sha is not None
                    and remote_blob_sha != source_snapshot.source_blob_sha)):
            raise CodeActionError(
                "fuente_obsoleta", "La lectura de GitHub no coincide con la fuente completa validada.", 409,
            )
        patched = self._patch(source, original_excerpt, suggested_code, file_path, complete=True)
        create_body = {"ref": f"refs/heads/{safe_target}", "sha": source_snapshot.commit_sha}
        status, _created = self._github_request("POST", f"/repos/{encoded_repo}/git/refs", create_body)
        if status not in {200, 201}:
            raise CodeActionError("rama_github_no_creada", "GitHub no creó la rama de verificación.", 409)
        content_body = {
            "message": f"Aplicar sugerencia revisada para {finding_id}",
            "content": base64.b64encode(patched.encode("utf-8")).decode("ascii"),
            "branch": safe_target,
            "sha": file_data.get("sha"),
        }
        status, commit_data = self._github_request(
            "PUT", f"/repos/{encoded_repo}/contents/{encoded_file}", content_body,
        )
        commit = commit_data.get("commit") if isinstance(commit_data, dict) else None
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        if status not in {200, 201} or not isinstance(commit_sha, str) or not commit_sha:
            raise CodeActionError(
                "commit_github_no_confirmado",
                "La rama pudo existir, pero GitHub no confirmó el commit del archivo.", 503,
            )
        return BranchExecutionResult(
            execution_mode="live", branch_name=safe_target,
            branch_url=f"https://github.com/{repo}/tree/{quote(safe_target, safe='/')}",
            commit_sha_applied=commit_sha,
        )

    def create_verification_branch(
        self,
        repo: str,
        base_branch: str,
        target_branch: str,
        file_path: str,
        original_excerpt: str,
        suggested_code: str,
        finding_id: str,
        actor: str,
    ) -> BranchExecutionResult:
        repo = self._validate_repository(repo)
        base_branch = self._validate_branch(base_branch)
        target_branch = self._validate_branch(target_branch)
        if self.repository is not None and not _same_repository(repo, self.repository):
            raise CodeActionError(
                "fuente_no_verificable", "El repositorio solicitado no coincide con el destino configurado.", 409,
            )
        if target_branch == base_branch:
            raise CodeActionError("rama_destino_invalida", "La rama destino debe ser distinta de la rama base.", 422)
        file_path = self._validate_file_path(file_path)
        finding_id = _validate_finding_id(finding_id)
        actor = _validate_actor(actor)
        original_excerpt = self._validate_text(original_excerpt, "original_excerpt")
        suggested_code = self._validate_text(suggested_code, "suggested_code")
        if self.origin is not None and (
            not self.origin.configured_for_code_action
            or not _same_repository(repo, self.origin.repository)
            or not _same_repository(self.repository, self.origin.repository)
            or base_branch != self.origin.base_branch
            or finding_id != self.origin.finding_id
            or file_path != self.origin.file_path
            or self.origin.commit_sha is None
            or self.origin.excerpt_sha256 is None
            or hashlib.sha256(original_excerpt.encode("utf-8")).hexdigest() != self.origin.excerpt_sha256
        ):
            raise CodeActionError(
                "fuente_no_verificable", "La solicitud no coincide con el origen fijado del hallazgo.", 409,
            )
        snapshot = self._source_snapshot(repo, file_path)
        if len(snapshot.content.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise CodeActionError("fuente_demasiado_grande", "La fuente capturada supera el tamaño permitido.", 422)
        patched = self._patch(snapshot.content, original_excerpt, suggested_code, file_path, complete=True)
        if self._live_ready(repo):
            live_result = self._live_branch(
                repo, base_branch, target_branch, file_path, original_excerpt,
                suggested_code, finding_id, snapshot,
            )
            if live_result is not None:
                return live_result

        digest_input = json.dumps({
            "actor": actor.strip(), "base_commit_sha": snapshot.commit_sha,
            "branch": target_branch, "file_path": file_path,
            "finding_id": finding_id, "patched_content": patched,
            "repo": repo, "source_sha256": snapshot.source_sha256,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        commit_sha = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        self._write_sandbox(target_branch, file_path, patched, repo)
        return BranchExecutionResult(
            execution_mode="sandbox", branch_name=target_branch,
            branch_url=f"sandbox://branches/{quote(target_branch, safe='/')}",
            commit_sha_applied=commit_sha,
        )

    # ------------------------------------------------------- pull request publication

    def create_pull_request(
        self,
        *,
        repo: str,
        base_branch: str,
        branch_result: BranchExecutionResult,
        finding_id: str,
        actor: str,
        patch_sha256: str,
    ) -> PullRequestExecutionResult:
        """Publish one pull request for an existing branch, or recover it without duplicating it.

        The protected base is always ``develop`` and the head branch can never be a
        protected branch or the base itself. A sandbox branch is published as an
        explicitly simulated pull request; a live branch requires the existing live
        gate (``external_writes_enabled`` plus a real project mode and token).
        """
        repo = self._validate_repository(repo)
        if self.repository is not None and not _same_repository(repo, self.repository):
            raise CodeActionError(
                "fuente_no_verificable", "El repositorio solicitado no coincide con el destino configurado.", 409,
            )
        base_branch = self._validate_branch(base_branch)
        if base_branch != _PROTECTED_PULL_REQUEST_BASE:
            raise CodeActionError(
                "rama_base_no_protegida",
                "El pull request debe abrirse contra la rama base protegida develop.",
                422,
            )
        if not isinstance(branch_result, BranchExecutionResult):
            raise CodeActionError("resultado_de_rama_invalido", "El resultado de la rama no es válido.", 422)
        head_branch = self._validate_branch(branch_result.branch_name)
        if head_branch in _PROTECTED_HEAD_BRANCHES or head_branch == base_branch:
            raise CodeActionError(
                "rama_cabecera_invalida",
                "La rama de cabecera no puede ser una rama protegida ni la rama base.",
                422,
            )
        head_commit = branch_result.commit_sha_applied
        if not isinstance(head_commit, str) or not _COMMIT_SHA.fullmatch(head_commit):
            raise CodeActionError("commit_invalido", "El commit de la rama no es válido.", 422)
        finding_id = _validate_finding_id(finding_id)
        actor = _validate_actor(actor)
        patch_sha256 = _validate_patch_sha256(patch_sha256)

        identity = {
            "base_branch": base_branch,
            "finding_id": finding_id,
            "head_branch": head_branch,
            "head_commit_sha": head_commit,
            "patch_sha256": patch_sha256,
            "repository": repo,
        }
        if branch_result.execution_mode == "live":
            if not self._live_ready(repo):
                raise CodeActionError(
                    "publicacion_no_habilitada",
                    "La publicación real está deshabilitada; no se abrió ningún pull request.",
                    503,
                )
            return self._live_pull_request(
                repo, base_branch, head_branch, head_commit, finding_id, actor, patch_sha256,
            )
        return self._sandbox_pull_request(identity)

    def _sandbox_pull_request(self, identity: dict[str, str]) -> PullRequestExecutionResult:
        """Simulate one pull request and persist a durable, identity-bound manifest."""
        root = self._sandbox_root()
        directory = self._sandbox_manifest_directory(root)
        identity_digest = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        manifest_path = directory / f"{identity_digest}.json"
        stored = self._read_sandbox_manifest(manifest_path, root)
        if stored is not None:
            return self._replayed_pull_request(stored, identity)
        self._refuse_duplicated_publication(directory, root, identity)
        number = _SANDBOX_NUMBER_BASE + int(identity_digest[:8], 16) % _SANDBOX_NUMBER_RANGE
        result = PullRequestExecutionResult(
            execution_mode="sandbox",
            pull_request_number=number,
            pull_request_url=f"sandbox://pulls/{quote(identity['repository'], safe='/')}/{number}",
            state="simulated",
            repository=identity["repository"],
            base_branch=identity["base_branch"],
            head_branch=identity["head_branch"],
            head_commit_sha=identity["head_commit_sha"],
            recovered=False,
        )
        if not self._write_sandbox_manifest(manifest_path, root, {
            "identity": identity, "result": result.model_dump(mode="json"),
        }):
            stored = self._read_sandbox_manifest(manifest_path, root)
            if stored is None:
                raise CodeActionError(
                    "sandbox_no_disponible", "No se pudo confirmar el manifiesto sandbox.", 503,
                )
            return self._replayed_pull_request(stored, identity)
        return result

    def _sandbox_manifest_directory(self, root: Path) -> Path:
        directory = root / _PULL_REQUEST_MANIFEST_DIRECTORY
        try:
            directory.mkdir(exist_ok=True)
        except OSError:
            raise CodeActionError("sandbox_no_disponible", "No se pudo preparar el destino sandbox.", 503) from None
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise CodeActionError("ruta_sandbox_insegura", "La ruta sandbox sale del directorio permitido.", 409)
        return directory

    @staticmethod
    def _read_sandbox_manifest(path: Path, root: Path) -> dict | None:
        if path.is_symlink() or not path.resolve(strict=False).is_relative_to(root):
            raise CodeActionError("ruta_sandbox_insegura", "La ruta sandbox sale del directorio permitido.", 409)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox existente no se puede leer.", 409,
            ) from None
        try:
            stored = json.loads(raw)
        except (ValueError, TypeError):
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox existente no es válido.", 409,
            ) from None
        if not isinstance(stored, dict):
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox existente no es válido.", 409,
            )
        return stored

    @staticmethod
    def _write_sandbox_manifest(path: Path, root: Path, payload: dict) -> bool:
        """Write the manifest once; report False when it already existed."""
        if path.is_symlink() or not path.resolve(strict=False).is_relative_to(root):
            raise CodeActionError("ruta_sandbox_insegura", "La ruta sandbox sale del directorio permitido.", 409)
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            return False
        except OSError:
            raise CodeActionError("sandbox_no_disponible", "No se pudo escribir el manifiesto sandbox.", 503) from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
        except OSError:
            raise CodeActionError("sandbox_no_disponible", "No se pudo escribir el manifiesto sandbox.", 503) from None
        return True

    @staticmethod
    def _replayed_pull_request(stored: dict, identity: dict) -> PullRequestExecutionResult:
        if stored.get("identity") != identity:
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox pertenece a otra publicación.", 409,
            )
        payload = stored.get("result")
        if not isinstance(payload, dict):
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox no contiene un resultado válido.", 409,
            )
        try:
            result = PullRequestExecutionResult.model_validate(payload)
        except ValueError:
            raise CodeActionError(
                "destino_sandbox_conflictivo", "El manifiesto sandbox no contiene un resultado válido.", 409,
            ) from None
        return result.model_copy(update={"recovered": True})

    def _refuse_duplicated_publication(self, directory: Path, root: Path, identity: dict) -> None:
        """Refuse a second publication for the same branch under a different identity."""
        for candidate in sorted(directory.glob("*.json")):
            stored = self._read_sandbox_manifest(candidate, root)
            if stored is None:
                continue
            other = stored.get("identity")
            if not isinstance(other, dict):
                continue
            if (other.get("repository") == identity["repository"]
                    and other.get("base_branch") == identity["base_branch"]
                    and other.get("head_branch") == identity["head_branch"]
                    and other != identity):
                raise CodeActionError(
                    "pull_request_duplicada",
                    "Ya existe un pull request para esa rama con otra revisión; no se duplicó la publicación.",
                    409,
                )

    def _live_pull_request(
        self, repo: str, base_branch: str, head_branch: str, head_commit: str,
        finding_id: str, actor: str, patch_sha256: str,
    ) -> PullRequestExecutionResult:
        owner, name = self._validate_repository(repo).split("/", 1)
        encoded_repo = f"{quote(owner, safe='')}/{quote(name, safe='')}"
        status, repository_info = self._github_request("GET", f"/repos/{encoded_repo}")
        if status in {401, 403, 404}:
            raise CodeActionError(
                "permiso_github_insuficiente",
                "GitHub no habilitó la escritura de pull requests en este repositorio.",
                403,
            )
        if status != 200:
            raise CodeActionError(
                "github_no_disponible", "No se pudo confirmar el permiso de escritura de GitHub.", 503,
            )
        permissions = repository_info.get("permissions")
        if not isinstance(permissions, dict) or permissions.get("push") is not True:
            raise CodeActionError(
                "permiso_github_insuficiente",
                "El token no tiene permiso de escritura en este repositorio.",
                403,
            )
        self._validate_head_commit(encoded_repo, head_branch, head_commit)
        recovered = self._find_existing_pull_request(
            repo, encoded_repo, owner, base_branch, head_branch, head_commit,
        )
        if recovered is not None:
            return recovered
        body = {
            "title": f"Reviewed corrective change for {finding_id}",
            "head": head_branch,
            "base": base_branch,
            "body": (
                f"Finding: {finding_id}\n"
                f"Base branch: {base_branch}\n"
                f"Head branch: {head_branch}\n"
                f"Head commit: {head_commit}\n"
                f"Reviewed patch SHA-256: {patch_sha256}\n"
                f"Requested by: {actor}\n"
            ),
        }
        try:
            status, payload = self._github_request("POST", f"/repos/{encoded_repo}/pulls", body)
        except CodeActionError as exc:
            if exc.code == "github_no_disponible":
                raise CodeActionError(
                    "pull_request_no_confirmado",
                    "No se pudo confirmar si GitHub creó el pull request; no se repetirá la publicación a ciegas.",
                    503,
                ) from None
            raise
        if status >= 500:
            raise CodeActionError(
                "pull_request_no_confirmado",
                "GitHub no respondió de forma concluyente; no se repetirá la publicación a ciegas.",
                503,
            )
        if status not in {200, 201}:
            raise CodeActionError("pull_request_no_creado", "GitHub no creó el pull request.", 409)
        return self._pull_request_result(
            payload, repo, base_branch, head_branch, head_commit, recovered=False,
        )

    def _validate_head_commit(self, encoded_repo: str, head_branch: str, head_commit: str) -> None:
        ref_path = quote(head_branch, safe="/")
        status, ref = self._github_request("GET", f"/repos/{encoded_repo}/git/ref/heads/{ref_path}")
        if status == 404:
            raise CodeActionError(
                "rama_cabecera_no_disponible", "La rama de cabecera no existe en GitHub.", 409,
            )
        if status != 200 or not isinstance(ref.get("object"), dict) or not ref["object"].get("sha"):
            raise CodeActionError(
                "github_no_disponible", "No se pudo leer la rama de cabecera en GitHub.", 503,
            )
        if str(ref["object"]["sha"]) != head_commit:
            raise CodeActionError(
                "pull_request_cabeza_inconsistente",
                "La rama de cabecera ya no apunta al commit revisado; no se creó ningún pull request.",
                409,
            )

    def _find_existing_pull_request(
        self, repo: str, encoded_repo: str, owner: str, base_branch: str,
        head_branch: str, head_commit: str,
    ) -> PullRequestExecutionResult | None:
        """Look up the pull request of this exact head, base and repository before creating one."""
        query = (
            f"state=all&head={quote(f'{owner}:{head_branch}', safe='')}"
            f"&base={quote(base_branch, safe='')}&per_page=100"
        )
        status, payload = self._github_request(
            "GET", f"/repos/{encoded_repo}/pulls?{query}", allow_list=True,
        )
        if status != 200:
            raise CodeActionError(
                "github_no_disponible",
                "No se pudo consultar el estado de los pull requests; no se creó ninguno.",
                503,
            )
        matches = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            head = item.get("head")
            base = item.get("base")
            if not isinstance(head, dict) or not isinstance(base, dict):
                continue
            if head.get("ref") != head_branch or base.get("ref") != base_branch:
                continue
            head_repository = head.get("repo")
            if not isinstance(head_repository, dict):
                continue
            if not _same_repository(head_repository.get("full_name"), repo):
                continue
            matches.append(item)
        if not matches:
            return None
        bound = [item for item in matches if (item.get("head") or {}).get("sha") == head_commit]
        if not bound:
            raise CodeActionError(
                "pull_request_cabeza_inconsistente",
                "Ya existe un pull request para esa rama con otro commit; no se creó otro.",
                409,
            )
        item = bound[0]
        if item.get("state") != "open":
            raise CodeActionError(
                "pull_request_no_creado",
                "El pull request existente ya no está abierto; no se creó otro.",
                409,
            )
        return self._pull_request_result(
            item, repo, base_branch, head_branch, head_commit, recovered=True,
        )

    @staticmethod
    def _pull_request_result(
        payload: dict, repo: str, base_branch: str, head_branch: str, head_commit: str, *,
        recovered: bool,
    ) -> PullRequestExecutionResult:
        number = payload.get("number")
        url = payload.get("html_url")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise CodeActionError(
                "respuesta_github_invalida", "GitHub no devolvió un número de pull request válido.", 502,
            )
        if (not isinstance(url, str) or not url.startswith("https://") or len(url) > 512
                or _CONTROL.search(url) or any(character.isspace() for character in url)):
            raise CodeActionError(
                "respuesta_github_invalida", "GitHub no devolvió una URL de pull request válida.", 502,
            )
        head = payload.get("head")
        if isinstance(head, dict) and head.get("sha") and head["sha"] != head_commit:
            raise CodeActionError(
                "pull_request_cabeza_inconsistente",
                "El pull request apunta a otro commit; no se confirmó la publicación.",
                409,
            )
        return PullRequestExecutionResult(
            execution_mode="live", pull_request_number=number, pull_request_url=url,
            state="open", repository=repo, base_branch=base_branch, head_branch=head_branch,
            head_commit_sha=head_commit, recovered=recovered,
        )
