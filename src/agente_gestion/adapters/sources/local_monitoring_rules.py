"""Local owner constraints, never authentication or action authorization."""

import json
from pathlib import Path

from agente_gestion.domain.report import DomainProposalRuleSet

MAX_MONITORING_RULES_BYTES = 1024 * 1024


class MonitoringRulesError(ValueError):
    """Static public failure without private input or filesystem details.

    ``reason`` separates an unreadable or malformed rules file from a valid file
    that belongs to another project. The public message stays identical so every
    caller keeps reporting the same static failure.
    """

    def __init__(self, reason: str = "invalid"):
        self.reason = reason
        super().__init__("Monitoring rules input invalid.")


def load_monitoring_rules(path: str | Path, project_id: str) -> DomainProposalRuleSet:
    """Read bounded JSON as the existing strict DTO and require exact run scope."""
    try:
        with open(path, "rb") as handle:
            content = handle.read(MAX_MONITORING_RULES_BYTES + 1)
        if len(content) > MAX_MONITORING_RULES_BYTES:
            raise ValueError
        rules = DomainProposalRuleSet.model_validate(json.loads(content.decode("utf-8")))
        if rules.project_id != project_id:
            raise MonitoringRulesError("project_mismatch")
        return rules
    except MonitoringRulesError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise MonitoringRulesError() from None
