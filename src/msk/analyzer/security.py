"""Security and privacy boundary scanner for the Project Knowledge Model.

CRITICAL PRIVACY GUARANTEE:
This module NEVER stores or returns raw secret values, passwords, tokens,
or private keys. It only flags file sensitivity and emits sanitized findings
(rule ID, severity, line number, sanitized description).
"""

from pathlib import Path
import re
from pydantic import BaseModel

SENSITIVE_FILE_PATTERNS = [
    r"^\.env(?:\..+)?$",
    r".*\.(?:pem|key|pkcs12|p12|cert|crt)$",
    r"^id_(?:rsa|dsa|ed25519)(?:\..+)?$",
    r"^(?:credentials|service-account|client_secret.*)\.json$",
]

SECRET_RULES = [
    (
        "PRIVATE_KEY_HEADER",
        "CRITICAL",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "Private key header detected",
    ),
    (
        "AWS_ACCESS_KEY",
        "HIGH",
        re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
        "AWS Access Key ID pattern detected",
    ),
    (
        "GITHUB_TOKEN",
        "HIGH",
        re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"),
        "GitHub personal access token pattern detected",
    ),
    (
        "SLACK_TOKEN",
        "HIGH",
        re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
        "Slack token pattern detected",
    ),
    (
        "HARDCODED_CREDENTIAL_ASSIGNMENT",
        "MEDIUM",
        re.compile(
            rb"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
        "Potential hardcoded credential assignment detected",
    ),
]


class SecurityScanFinding(BaseModel):
    """Sanitized finding from security scanning."""

    rule_id: str
    severity: str
    line_number: int | None = None
    description: str


def is_sensitive_path(relative_path: str | Path) -> tuple[bool, list[str]]:
    """Determine if a file path matches sensitive file patterns."""
    p = Path(relative_path)
    filename = p.name.lower()
    flags: list[str] = []

    for pat in SENSITIVE_FILE_PATTERNS:
        if re.search(pat, filename, re.IGNORECASE):
            flags.append("SENSITIVE_FILE_PATTERN")
            return (True, flags)

    return (False, [])


def scan_content_for_secrets(relative_path: str, content: bytes) -> list[SecurityScanFinding]:
    """Scan content for sensitive patterns without storing matched strings.

    Returns only sanitized findings.
    """
    findings: list[SecurityScanFinding] = []
    if not content:
        return findings

    # Check path first
    is_sens, _ = is_sensitive_path(relative_path)
    if is_sens:
        findings.append(
            SecurityScanFinding(
                rule_id="SENSITIVE_FILE",
                severity="HIGH",
                line_number=1,
                description=f"File '{Path(relative_path).name}' is identified as a sensitive credential or key file",
            )
        )

    # Scan lines for content patterns
    lines = content.split(b"\n")
    for line_idx, line in enumerate(lines, 1):
        for rule_id, severity, regex, desc in SECRET_RULES:
            if regex.search(line):
                findings.append(
                    SecurityScanFinding(
                        rule_id=rule_id,
                        severity=severity,
                        line_number=line_idx,
                        description=f"{desc} on line {line_idx}",
                    )
                )

    return findings
