"""Fresh source adapter factory creating isolated source instances per invocation (T5b)."""

from typing import Optional
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
    )


def create_code_source(
    settings: Settings,
    transport: Optional[HttpGetTransportPort] = None,
) -> CodeSourcePort:
    """Create a code diff source adapter conforming to CodeSourcePort."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)

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


def create_composite_source(
    settings: Settings,
    commit_sha: Optional[str] = None,
    transport: Optional[HttpGetTransportPort] = None,
) -> CompositeSourcesAdapter | FixtureSourcesAdapter:
    """Create a fresh CompositeSourcesAdapter coordinating multi-source retrieval."""
    if settings.source_mode == "fixtures":
        return FixtureSourcesAdapter(fixtures_dir=settings.fixtures_dir)

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
    )
