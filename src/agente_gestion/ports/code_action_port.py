"""Port for controlled, human-approved code branch actions."""

from abc import ABC, abstractmethod

from agente_gestion.domain.code_action import BranchExecutionResult


class CodeActionPort(ABC):
    """Create a verification branch without mutating the protected source branch."""

    @abstractmethod
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
        """Create one exact-match file patch on a new branch and report where it exists."""
        raise NotImplementedError
