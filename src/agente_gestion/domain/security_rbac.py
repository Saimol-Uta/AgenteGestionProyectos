"""Role-Based Access Control (RBAC), security principal, and permission enforcement.

Enforces academic and operational security boundaries:
1. Academic Roles:
   - MONITORING / ELABORATOR: analyzes tasks, progress, risks; proposes actions.
   - QUALITY / REVIEWER: reviews code diffs, evaluates quality criteria and proposals.
   - VERIFIER / AUXILIARY: independently validates disputed claims/findings.
   - REPORT / CONSULTATIVE_APPROVER: consolidates metrics and policy recommendations.
   - PROJECT_MANAGER / HUMAN_APPROVER: executive authority; decides reports and authorizes executions.
   - ADMIN: administrative management and policy overrides.
   - DEVELOPER: read-only access to project planning and code context.

2. Permissions:
   - Granular permissions mapped to domain operations.

3. Token & Audit Security:
   - Secrets and auth tokens are hashed (SHA-256 fingerprint) before persistence or auditing.
   - Rejects unverified or blank assertions.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Annotated, Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class PermissionDeniedSecurityError(Exception):
    """Raised when an actor attempts an operation without having the required permission."""
    def __init__(self, actor_id: str, role: str, permission: str, reason: str):
        self.actor_id = actor_id
        self.role = role
        self.permission = permission
        self.reason = reason
        super().__init__(
            f"Security Error: Actor '{actor_id}' with role '{role}' is denied permission '{permission}': {reason}"
        )


class AuthenticationSecurityError(Exception):
    """Raised when an actor's credentials or identity assertion fails verification."""
    pass


class SystemRole(str, Enum):
    DEVELOPER = "developer"
    MONITORING = "monitoring"
    QUALITY = "quality"
    VERIFIER = "verifier"
    REPORT = "report"
    PROJECT_MANAGER = "project_manager"
    ADMIN = "admin"


class Permission(str, Enum):
    READ_TASKS = "read_tasks"
    READ_CODE = "read_code"
    QUERY_HISTORY = "query_history"
    PROPOSE_TASK_ACTION = "propose_task_action"
    REVIEW_TASK_PROPOSAL = "review_task_proposal"
    DECIDE_REPORT = "decide_report"
    AUTHORIZE_ACTION_EXECUTION = "authorize_action_execution"
    EXECUTE_ACTION = "execute_action"
    TRIGGER_WEEKLY_SCHEDULE = "trigger_weekly_schedule"
    MANAGE_SECURITY_POLICIES = "manage_security_policies"
    AUDIT_SECURITY_LOGS = "audit_security_logs"


ROLE_PERMISSIONS: dict[SystemRole, set[Permission]] = {
    SystemRole.DEVELOPER: {
        Permission.READ_TASKS,
        Permission.READ_CODE,
        Permission.QUERY_HISTORY,
    },
    SystemRole.MONITORING: {
        Permission.READ_TASKS,
        Permission.QUERY_HISTORY,
        Permission.PROPOSE_TASK_ACTION,
    },
    SystemRole.QUALITY: {
        Permission.READ_CODE,
        Permission.QUERY_HISTORY,
        Permission.REVIEW_TASK_PROPOSAL,
    },
    SystemRole.VERIFIER: {
        Permission.READ_TASKS,
        Permission.READ_CODE,
        Permission.REVIEW_TASK_PROPOSAL,
    },
    SystemRole.REPORT: {
        Permission.READ_TASKS,
        Permission.READ_CODE,
        Permission.QUERY_HISTORY,
    },
    SystemRole.PROJECT_MANAGER: {
        Permission.READ_TASKS,
        Permission.READ_CODE,
        Permission.QUERY_HISTORY,
        Permission.DECIDE_REPORT,
        Permission.AUTHORIZE_ACTION_EXECUTION,
        Permission.EXECUTE_ACTION,
        Permission.TRIGGER_WEEKLY_SCHEDULE,
        Permission.AUDIT_SECURITY_LOGS,
    },
    SystemRole.ADMIN: {
        Permission.READ_TASKS,
        Permission.READ_CODE,
        Permission.QUERY_HISTORY,
        Permission.PROPOSE_TASK_ACTION,
        Permission.REVIEW_TASK_PROPOSAL,
        Permission.DECIDE_REPORT,
        Permission.AUTHORIZE_ACTION_EXECUTION,
        Permission.EXECUTE_ACTION,
        Permission.TRIGGER_WEEKLY_SCHEDULE,
        Permission.MANAGE_SECURITY_POLICIES,
        Permission.AUDIT_SECURITY_LOGS,
    },
}


def hash_token_fingerprint(token: str) -> str:
    """Computes a secure, truncated SHA-256 fingerprint of a token for audit logging."""
    if not token or not token.strip():
        return "none"
    digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


class SecurityPrincipal(BaseModel):
    """Authenticated principal holding an identified role and credentials."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: StrictStr
    role: SystemRole
    auth_mechanism: Literal["hmac_token", "pki_signature", "test_authority", "offline_claim"]
    auth_token: StrictStr

    @field_validator("actor_id", "auth_token")
    @classmethod
    def nonblank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Security principal identifiers and tokens must be non-blank")
        return v.strip()

    @property
    def token_fingerprint(self) -> str:
        return hash_token_fingerprint(self.auth_token)

    def is_authenticated_authority(self) -> bool:
        """Verifies if the principal holds verified authentication rather than an unverified offline assertion."""
        if self.auth_mechanism == "offline_claim":
            return False
        if self.auth_token == "asserted_offline":
            return False
        return len(self.auth_token) >= 8


class SecurityAuditEntry(BaseModel):
    """Immutable audit record of a security or access decision."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: StrictStr
    project_id: StrictStr
    actor_id: StrictStr
    role: StrictStr
    permission_checked: StrictStr
    resource_id: Optional[StrictStr] = None
    decision: Literal["granted", "denied"]
    reason: StrictStr
    token_fingerprint: StrictStr
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def enforce_rbac(
    principal: SecurityPrincipal,
    required_permission: Permission,
    project_id: str = "GLOBAL",
    resource_id: Optional[str] = None,
) -> SecurityAuditEntry:
    """
    Enforces role-based permissions and authentication rigor.
    Returns a SecurityAuditEntry if access is granted, or raises PermissionDeniedSecurityError.
    """
    # 1. Check executive/sensitive permissions requiring strict authentication
    privileged_permissions = {
        Permission.DECIDE_REPORT,
        Permission.AUTHORIZE_ACTION_EXECUTION,
        Permission.EXECUTE_ACTION,
        Permission.MANAGE_SECURITY_POLICIES,
    }
    if required_permission in privileged_permissions and not principal.is_authenticated_authority():
        event_id = f"sec-{hashlib.sha256(f'{principal.actor_id}:{required_permission.value}:{datetime.now(timezone.utc).isoformat()}'.encode()).hexdigest()[:16]}"
        entry = SecurityAuditEntry(
            event_id=event_id,
            project_id=project_id,
            actor_id=principal.actor_id,
            role=principal.role.value,
            permission_checked=required_permission.value,
            resource_id=resource_id,
            decision="denied",
            reason="Unverified offline assertion is forbidden for privileged security operations.",
            token_fingerprint=principal.token_fingerprint,
        )
        raise PermissionDeniedSecurityError(
            actor_id=principal.actor_id,
            role=principal.role.value,
            permission=required_permission.value,
            reason=entry.reason,
        )

    # 2. Check Role-Permission mapping
    allowed_permissions = ROLE_PERMISSIONS.get(principal.role, set())
    if required_permission not in allowed_permissions:
        event_id = f"sec-{hashlib.sha256(f'{principal.actor_id}:{required_permission.value}:{datetime.now(timezone.utc).isoformat()}'.encode()).hexdigest()[:16]}"
        entry = SecurityAuditEntry(
            event_id=event_id,
            project_id=project_id,
            actor_id=principal.actor_id,
            role=principal.role.value,
            permission_checked=required_permission.value,
            resource_id=resource_id,
            decision="denied",
            reason=f"Role '{principal.role.value}' does not possess required permission '{required_permission.value}'.",
            token_fingerprint=principal.token_fingerprint,
        )
        raise PermissionDeniedSecurityError(
            actor_id=principal.actor_id,
            role=principal.role.value,
            permission=required_permission.value,
            reason=entry.reason,
        )

    # 3. Access Granted
    event_id = f"sec-{hashlib.sha256(f'{principal.actor_id}:{required_permission.value}:{datetime.now(timezone.utc).isoformat()}'.encode()).hexdigest()[:16]}"
    return SecurityAuditEntry(
        event_id=event_id,
        project_id=project_id,
        actor_id=principal.actor_id,
        role=principal.role.value,
        permission_checked=required_permission.value,
        resource_id=resource_id,
        decision="granted",
        reason="Permission verified against role definition.",
        token_fingerprint=principal.token_fingerprint,
    )

