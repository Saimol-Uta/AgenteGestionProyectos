"""Fresh source adapter factory creating isolated source instances per invocation (T5b)."""

from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from agente_gestion.application.config import Settings
from agente_gestion.ports.sources import (
    CISourcePort,
    CodeSourcePort,
    PolicySourcePort,
    TaskSourcePort,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.adapters.sources.github import (
    GitHubCodeSourceAdapter,
    GitHubCodeSourceAdapter as GitHubSourceAdapter,
)
from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.local_policy import LocalYamlPolicyAdapter
from agente_gestion.adapters.sources.composite import CompositeSourcesAdapter
from agente_gestion.adapters.sources.http_transport import (
    BoundedGetTransport,
    HttpGetTransportPort,
)
from agente_gestion.adapters.sources.multirepo import MultiRepoSourcesAdapter


def _task_source_identity(source: TaskSourcePort) -> Optional[str]:
    """Fingerprint only known effective Trello scope; never serialize Settings/secrets."""
    if not isinstance(source, TrelloSourceAdapter):
        return None

    cfg = source.mapping_config
    # These collections are membership sets; label matching ignores case/whitespace.
    mapping = {
        "pending_list_ids": sorted(set(cfg.pending_list_ids)),
        "in_progress_list_ids": sorted(set(cfg.in_progress_list_ids)),
        "completed_list_ids": sorted(set(cfg.completed_list_ids)),
        "blocked_labels": sorted({label.strip().lower() for label in cfg.blocked_labels
                                  if label.strip()}),
        "included_list_ids": (sorted(set(cfg.included_list_ids))
                              if cfg.included_list_ids is not None else None),
    }
    scope = {"provider": "trello", "board_id": source.board_id,
             "state_strategy": source.state_strategy, "mapping": mapping}
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "trello-sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


def _task_source_identity_from_settings(settings: Settings) -> Optional[str]:
    """Fingerprint the project-wide Trello mapping without constructing an unbudgeted adapter."""
    if not (settings.trello_board_id and settings.trello_api_key and settings.trello_token):
        return None
    cfg = settings.get_trello_mapping()
    mapping = {
        "pending_list_ids": sorted(set(cfg.pending_list_ids)),
        "in_progress_list_ids": sorted(set(cfg.in_progress_list_ids)),
        "completed_list_ids": sorted(set(cfg.completed_list_ids)),
        "blocked_labels": sorted({label.strip().lower() for label in cfg.blocked_labels if label.strip()}),
        "included_list_ids": (sorted(set(cfg.included_list_ids)) if cfg.included_list_ids is not None else None),
    }
    scope = {
        "provider": "trello", "board_id": settings.trello_board_id,
        "state_strategy": settings.trello_state_strategy, "mapping": mapping,
    }
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "trello-sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


def create_task_source(
    settings: Settings,
    transport: Optional[HttpGetTransportPort] = None,
) -> TaskSourcePort:
    """Create a task source adapter conforming to TaskSourcePort."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)

    # Validate Trello configuration only
    if not settings.trello_board_id or not settings.trello_api_key or not settings.trello_token:
        raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

    api_key = settings.trello_api_key.get_secret_value()
    token = settings.trello_token.get_secret_value()
    mapping = settings.get_trello_mapping()

    tr = transport or BoundedGetTransport(
        SourceProvider.trello,
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.http_max_response_bytes,
    )
    return TrelloSourceAdapter(
        board_id=settings.trello_board_id,
        api_key=api_key,
        token=token,
        mapping_config=mapping,
        transport=tr,
        timezone_name=settings.timezone,
        state_strategy=settings.trello_state_strategy,
    )


def create_code_source(
    settings: Settings,
    transport: Optional[HttpGetTransportPort] = None,
) -> CodeSourcePort:
    """Create a code diff source adapter conforming to CodeSourcePort."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)
    if settings.github_repositories is not None:
        raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

    # Validate GitHub repository configuration only
    repo = settings.github_repo or settings.github_repository
    if not settings.github_owner or not repo:
        raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

    token = settings.github_token.get_secret_value() if settings.github_token else None
    branch = settings.github_branch or settings.github_ref

    tr = transport or BoundedGetTransport(
        SourceProvider.github,
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.http_max_response_bytes,
    )
    return GitHubSourceAdapter(
        owner=settings.github_owner,
        repo=repo,
        token=token,
        branch=branch,
        transport=tr,
    )


def create_ci_source(
    settings: Settings,
    commit_sha: Optional[str] = None,
    transport: Optional[HttpGetTransportPort] = None,
) -> CISourcePort:
    """Create a continuous integration source adapter conforming to CISourcePort."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)
    if settings.github_repositories is not None:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

    # Validate Actions configuration and require pinned target commit SHA
    repo = settings.github_repo or settings.github_repository
    if not settings.github_owner or not repo:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

    target_sha = commit_sha or settings.github_commit_sha
    if not target_sha:
        # Isolated Actions collection requires an explicit pinned target SHA before issuing HTTP
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

    token = settings.github_token.get_secret_value() if settings.github_token else None

    tr = transport or BoundedGetTransport(
        SourceProvider.github_actions,
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.http_max_response_bytes,
    )
    return GitHubActionsSourceAdapter(
        owner=settings.github_owner,
        repo=repo,
        token=token,
        target_commit_sha=target_sha,
        transport=tr,
    )


def create_policy_source(settings: Settings) -> PolicySourcePort:
    """Create a quality policy source adapter conforming to PolicySourcePort."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)

    return LocalYamlPolicyAdapter(policy_file_path=settings.policies_path)


def create_analysis_composite_source(
    settings: Settings,
    commit_sha: Optional[str] = None,
    transport: Optional[HttpGetTransportPort] = None,
    clock: Optional[Callable[[], float]] = None,
) -> CompositeSourcesAdapter | MultiRepoSourcesAdapter | FixtureSourcesAdapter:
    """Build an analysis source graph with per-component local fallback and a strict remote gate."""
    fixtures = FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)
    if settings.source_mode == "fixtures":
        return fixtures
    if settings.github_repositories is not None and commit_sha is not None:
        raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

    remote_allowed = settings.allow_remote_ai and not settings.is_fake
    if settings.github_repositories is not None and remote_allowed and settings.github_token:
        if commit_sha is not None:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        task_configured = bool(settings.trello_board_id and settings.trello_api_key and settings.trello_token)
        task_factory = (
            (lambda tr: create_task_source(settings, transport=tr))
            if task_configured else (lambda tr: FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir))
        )
        policy_factory = (
            (lambda: create_policy_source(settings))
            if Path(settings.policies_path).is_file()
            else (lambda: FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir))
        )
        return MultiRepoSourcesAdapter(
            settings,
            transport=transport,
            task_source_factory=task_factory,
            policy_source_factory=policy_factory,
            task_source_identity=_task_source_identity_from_settings(settings) if task_configured else None,
            default_timezone=ZoneInfo(settings.timezone),
            clock=clock,
        )
    repository = settings.github_repo or settings.github_repository
    repo_bound = bool(settings.github_owner and repository and settings.github_token)
    target_sha = commit_sha or settings.github_commit_sha

    task_source = (
        create_task_source(settings, transport=transport)
        if remote_allowed and settings.trello_board_id and settings.trello_api_key and settings.trello_token
        else fixtures
    )
    code_source = create_code_source(settings, transport=transport) if remote_allowed and repo_bound else fixtures
    ci_source = (
        create_ci_source(settings, commit_sha=target_sha, transport=transport)
        if remote_allowed and repo_bound and target_sha
        else fixtures
    )
    policy_source = (
        create_policy_source(settings)
        if Path(settings.policies_path).is_file()
        else fixtures
    )

    tz: Optional[ZoneInfo] = None
    if settings.timezone:
        try:
            tz = ZoneInfo(settings.timezone)
        except Exception:
            raise SourceError(SourceProvider.local_policy, SourceErrorCategory.configuration) from None

    return CompositeSourcesAdapter(
        task_source=task_source,
        code_source=code_source,
        ci_source=ci_source,
        policy_source=policy_source,
        default_commit_sha=target_sha,
        default_timezone=tz,
        known_secret_values=settings.get_known_secret_values(),
        task_source_identity=_task_source_identity(task_source),
    )


def create_composite_source(
    settings: Settings,
    commit_sha: Optional[str] = None,
    transport: Optional[HttpGetTransportPort] = None,
    clock: Optional[Callable[[], float]] = None,
) -> CompositeSourcesAdapter | MultiRepoSourcesAdapter | FixtureSourcesAdapter:
    """Create a fresh composite source; collection mode keeps repositories independently scoped."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)
    if settings.github_repositories is not None:
        if commit_sha is not None:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        return MultiRepoSourcesAdapter(
            settings,
            transport=transport,
            task_source_factory=lambda tr: create_task_source(settings, transport=tr),
            policy_source_factory=lambda: create_policy_source(settings),
            task_source_identity=_task_source_identity_from_settings(settings),
            default_timezone=ZoneInfo(settings.timezone),
            clock=clock,
        )

    task_source = create_task_source(settings, transport=transport)
    code_source = create_code_source(settings, transport=transport)

    repo = settings.github_repo or settings.github_repository
    if not settings.github_owner or not repo:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

    target_sha = commit_sha or settings.github_commit_sha
    token = settings.github_token.get_secret_value() if settings.github_token else None
    tr_ci = transport or BoundedGetTransport(
        SourceProvider.github_actions,
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.http_max_response_bytes,
    )
    ci_source = GitHubActionsSourceAdapter(
        owner=settings.github_owner,
        repo=repo,
        token=token,
        target_commit_sha=target_sha,
        transport=tr_ci,
    )

    policy_source = create_policy_source(settings)

    tz: Optional[ZoneInfo] = None
    if settings.timezone:
        try:
            tz = ZoneInfo(settings.timezone)
        except Exception:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.configuration,
            ) from None

    return CompositeSourcesAdapter(
        task_source=task_source,
        code_source=code_source,
        ci_source=ci_source,
        policy_source=policy_source,
        default_commit_sha=target_sha,
        default_timezone=tz,
        known_secret_values=settings.get_known_secret_values(),
        task_source_identity=_task_source_identity(task_source),
    )
