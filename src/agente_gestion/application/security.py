"""Security gating, credential sanitization, and remote payload redaction."""

import re
from typing import Any, Optional


class RemoteProviderNotAllowedError(Exception):
    """Raised when remote AI invocation is attempted but allow_remote_ai is False."""
    pass


class RemotePayloadSecurityError(Exception):
    """Raised when an outbound payload contains secret files or sensitive credentials."""
    pass


# Sensitive file patterns that must never be sent to remote models
SECRET_FILE_PATTERNS = [
    re.compile(r"(^|[/\\])\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"(^|[/\\])id_rsa", re.IGNORECASE),
    re.compile(r"(^|[/\\])id_ed25519", re.IGNORECASE),
    re.compile(r"\.(pem|key|pkcs12|pfx|p12)$", re.IGNORECASE),
    re.compile(r"(^|[/\\])(secrets|credentials)\.(json|yaml|yml|toml)$", re.IGNORECASE),
]

# Sensitive content patterns (API keys, private keys, auth tokens)
SECRET_CONTENT_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private_key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws_access_key"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "google_api_key"),
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "github_personal_token"),
    (re.compile(r"(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{16,}['\"]?", re.IGNORECASE), "generic_secret_assignment"),
]


def assert_remote_allowed(allow_remote_ai: bool) -> None:
    """Verifies that remote AI processing has been explicitly enabled outside the provider."""
    if not allow_remote_ai:
        raise RemoteProviderNotAllowedError(
            "Remote AI provider invocation rejected: allow_remote_ai is disabled for this project/run."
        )


def check_for_secrets(data: Any) -> list[str]:
    """
    Recursively scans structured or scalar data for secret-bearing filenames and credential patterns.
    Returns a list of violation descriptions.
    """
    violations: list[str] = []

    def scan_str(val: str, key_hint: str = ""):
        # Check if the string represents a sensitive file path
        for pat in SECRET_FILE_PATTERNS:
            if pat.search(val):
                violations.append(f"Secret-bearing filename detected: '{val}'")
                break

        # Check content patterns
        for pat, desc in SECRET_CONTENT_PATTERNS:
            if pat.search(val):
                violations.append(f"Sensitive credential detected ({desc}) in field '{key_hint}'")
                break

    def recurse(node: Any, key_path: str = ""):
        if isinstance(node, str):
            scan_str(node, key_path)
        elif isinstance(node, dict):
            for k, v in node.items():
                curr_path = f"{key_path}.{k}" if key_path else str(k)
                # If key itself looks like file_path or path, check the value for secret file patterns
                if isinstance(v, str) and any(kw in str(k).lower() for kw in ("file", "path", "filename")):
                    for pat in SECRET_FILE_PATTERNS:
                        if pat.search(v):
                            violations.append(f"Secret-bearing filename in '{curr_path}': '{v}'")
                            break
                recurse(v, curr_path)
        elif isinstance(node, (list, tuple, set)):
            for i, elem in enumerate(node):
                recurse(elem, f"{key_path}[{i}]")

    recurse(data)
    return violations


def assert_safe_remote_payload(payload: Any) -> None:
    """
    Enforces security boundary: rejects payloads containing credentials, keys,
    or secret files before any transmission to a remote provider.
    """
    violations = check_for_secrets(payload)
    if violations:
        raise RemotePayloadSecurityError(
            f"Remote payload rejected due to security policy violations: {'; '.join(violations)}"
        )


def mask_secret(text: str, secret: Optional[str]) -> str:
    """Replaces any occurrence of a secret with [REDACTED] to protect logs and errors."""
    if not secret or len(secret) < 4:
        return text
    return text.replace(secret, "[REDACTED_CREDENTIAL]")
