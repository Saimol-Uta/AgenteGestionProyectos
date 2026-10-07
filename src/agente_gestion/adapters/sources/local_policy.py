"""Local YAML adapter for quality policy rules."""

import math
from pathlib import Path
from typing import Any
import yaml
from pydantic import ValidationError

from agente_gestion.domain.policies import QualityRule
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import PolicySourcePort

SUPPORTED_OPERATORS = {
    "==",
    "!=",
    "<=",
    ">=",
    "<",
    ">",
    "is_true",
    "is_false",
    "true",
    "false",
}


class LocalYamlPolicyAdapter(PolicySourcePort):
    """
    Loads and validates quality policy rules from a local YAML file.

    Guarantees strict schema validation, duplicate ID rejection,
    and sanitized SourceError generation on any read/syntax/schema failure.
    """

    def __init__(self, policy_file_path: str | Path = "config/policies.yaml"):
        self.policy_file_path = Path(policy_file_path)

    @property
    def resource_path(self) -> Path:
        """Expose the configured file path resource cleanly for provenance."""
        return self.policy_file_path

    def get_policies(self, project_id: str) -> list[QualityRule]:
        """
        Load and parse quality rules from the configured YAML file.

        The project_id parameter is accepted for port compatibility; the local
        policies file defines project quality gates without fixture fallback.
        """
        try:
            with open(self.policy_file_path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f)
        except FileNotFoundError:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.not_found,
            ) from None
        except PermissionError:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.permission_denied,
            ) from None
        except IsADirectoryError:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.not_found,
            ) from None
        except (OSError, UnicodeDecodeError):
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.malformed_payload,
            ) from None
        except yaml.YAMLError:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.malformed_payload,
            ) from None
        except Exception:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.malformed_payload,
            ) from None

        if not isinstance(raw_data, dict):
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.malformed_payload,
            ) from None

        if "policies" not in raw_data or not isinstance(raw_data["policies"], list):
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.malformed_payload,
            ) from None

        raw_policies = raw_data["policies"]
        if not raw_policies:
            raise SourceError(
                SourceProvider.local_policy,
                SourceErrorCategory.configuration,
            ) from None

        rules: list[QualityRule] = []
        seen_ids: set[str] = set()

        for item in raw_policies:
            if not isinstance(item, dict):
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.malformed_payload,
                ) from None

            for req in ("id", "description", "metric_key"):
                val = item.get(req)
                if not isinstance(val, str) or not val.strip():
                    raise SourceError(
                        SourceProvider.local_policy,
                        SourceErrorCategory.malformed_payload,
                    ) from None

            rule_id = str(item["id"]).strip()
            if rule_id in seen_ids:
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.configuration,
                ) from None
            seen_ids.add(rule_id)

            op = str(item.get("operator", "==")).strip().lower()
            if op not in SUPPORTED_OPERATORS:
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.malformed_payload,
                ) from None

            if op in ("<=", ">=", "<", ">"):
                threshold = item.get("threshold")
                if threshold is None or isinstance(threshold, bool):
                    raise SourceError(
                        SourceProvider.local_policy,
                        SourceErrorCategory.malformed_payload,
                    ) from None
                try:
                    val = float(threshold)
                    if not math.isfinite(val):
                        raise SourceError(
                            SourceProvider.local_policy,
                            SourceErrorCategory.malformed_payload,
                        ) from None
                except (ValueError, TypeError, OverflowError):
                    raise SourceError(
                        SourceProvider.local_policy,
                        SourceErrorCategory.malformed_payload,
                    ) from None

            try:
                rule = QualityRule(**item)
            except (ValidationError, TypeError, ValueError):
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.malformed_payload,
                ) from None
            except Exception:
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.malformed_payload,
                ) from None

            rules.append(rule)

        return rules
