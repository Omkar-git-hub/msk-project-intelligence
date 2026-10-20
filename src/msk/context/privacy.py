"""Privacy and data minimization engine for MSK Context."""

import re
from pathlib import Path
from typing import ClassVar

from msk.analyzer.security import SENSITIVE_FILE_PATTERNS, is_sensitive_path

# Key-value assignment secret regex (case-insensitive)
_KV_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer|auth|access[_-]?token|private[_-]?key)\b"
    r"(\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-\.\/+=]{8,})(['\"]?)"
)

# Known provider token patterns
_TOKEN_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"),          # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                     # AWS Access Key ID
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),         # Slack tokens
    re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),                  # OpenAI / generic secret keys
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[^-]+-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL),
]


class PrivacyFilter:
    """Applies local-first privacy, secret scrubbing, and sensitive file protections."""

    REPLACEMENT: ClassVar[str] = "[REDACTED_SECRET]"

    def __init__(self) -> None:
        self.redactions_count = 0
        self.excluded_files: list[str] = []

    def sanitize_text(self, text: str | None) -> str:
        """Scrub detected secrets and credentials from a text string."""
        if not text:
            return ""

        sanitized = text

        # 1. Scrub token patterns
        for pattern in _TOKEN_PATTERNS:
            matches = list(pattern.finditer(sanitized))
            if matches:
                self.redactions_count += len(matches)
                sanitized = pattern.sub(self.REPLACEMENT, sanitized)

        # 2. Scrub key-value secrets
        def _kv_replacer(match: re.Match) -> str:
            self.redactions_count += 1
            return f"{match.group(1)}{match.group(2)}{self.REPLACEMENT}{match.group(4)}"

        sanitized = _KV_SECRET_PATTERN.sub(_kv_replacer, sanitized)

        return sanitized

    def is_sensitive_file(self, file_path: str | Path) -> bool:
        """Check if a file matches sensitive credential or private key patterns."""
        p = Path(file_path)
        norm_name = p.name.lower()

        # Check standard sensitive path patterns
        is_sens, _ = is_sensitive_path(file_path)
        if is_sens:
            return True

        for pat in SENSITIVE_FILE_PATTERNS:
            if re.search(pat, norm_name, re.IGNORECASE):
                return True

        return False

    def check_and_record_file(self, file_path: str) -> bool:
        """Return True if allowed, or False and record as excluded if sensitive."""
        if self.is_sensitive_file(file_path):
            if file_path not in self.excluded_files:
                self.excluded_files.append(file_path)
            return False
        return True

    def get_privacy_notes(self) -> list[str]:
        """Return standardized transparency notes about privacy protections."""
        notes = [
            "All context preparation executed strictly locally with zero external network transmission.",
            "Pattern-based secret scrubbing applied to input queries, error text, and stack traces.",
            "Sensitive credential files (.env, private keys, certificates, secrets) are excluded from context.",
            "Full source files are omitted in favor of minimal symbol definitions and structural metadata.",
            "Notice: Heuristic redaction cannot guarantee 100% detection of unknown or custom secret formats.",
        ]
        return notes
