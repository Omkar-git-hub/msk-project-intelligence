"""Privacy scrubbing and path normalization for runtime intelligence."""

from pathlib import Path
from typing import Any

from msk.common.errors import MSKError
from msk.context.privacy import PrivacyFilter

# Keys in attribute dicts that should be redacted
_SENSITIVE_ATTR_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "private_key",
}


class RuntimeSanitizer:
    """Applies privacy scrubbing and project path validation to runtime data."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else None
        self._filter = PrivacyFilter()

    def sanitize_text(self, text: str | None) -> str:
        """Scrub secret-like patterns and sensitive keys from log messages or traces."""
        if not text:
            return ""
        return self._filter.sanitize_text(text)

    def sanitize_attributes(self, attrs: dict[str, Any] | None) -> dict[str, Any]:
        """Scrub sensitive keys and credential patterns from runtime attributes."""
        if not attrs:
            return {}

        clean: dict[str, Any] = {}
        for k, v in attrs.items():
            key_lower = str(k).lower().replace("_", "-")
            if key_lower in _SENSITIVE_ATTR_KEYS:
                clean[k] = "[REDACTED_SECRET]"
            elif isinstance(v, str):
                clean[k] = self.sanitize_text(v)
            elif isinstance(v, dict):
                clean[k] = self.sanitize_attributes(v)
            elif isinstance(v, list):
                clean[k] = [
                    self.sanitize_text(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                clean[k] = v
        return clean

    def normalize_and_validate_path(self, raw_path: str | Path | None) -> str | None:
        """Normalize file paths from stack traces or logs, preventing path traversal.

        - Relativizes paths within project_root to POSIX relative paths.
        - Checks for traversal attempts attempting to escape project_root.
        - Normalizes Windows backslashes to forward slashes.
        """
        if not raw_path:
            return None

        p_str = str(raw_path).strip()
        if not p_str:
            return None

        # Quick check for suspicious path traversal
        norm_str = p_str.replace("\\", "/")

        if self.project_root:
            p = Path(p_str)
            if p.is_absolute():
                try:
                    resolved = p.resolve()
                    rel = resolved.relative_to(self.project_root).as_posix()
                    return rel
                except ValueError:
                    # File is outside project root (e.g. system library or standard library)
                    return norm_str
            else:
                # Relative path: check for traversal escape
                target = (self.project_root / p).resolve()
                try:
                    rel = target.relative_to(self.project_root).as_posix()
                    return rel
                except ValueError as err:
                    raise MSKError(
                        f"Path traversal detected: '{p_str}' resolves outside project root.",
                        hint="Ensure file paths in logs are relative to the project directory.",
                    ) from err

        # When project_root is not provided, sanitize traversal sequences
        if "../" in norm_str or "..\\" in p_str:
            clean_parts: list[str] = []
            for part in norm_str.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if clean_parts:
                        clean_parts.pop()
                else:
                    clean_parts.append(part)
            return "/".join(clean_parts) or None

        return norm_str
