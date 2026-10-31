"""Secure credential management and token resolution for AI providers."""

import logging
import os
import re
from typing import Any

logger = logging.getLogger("msk.gateway.credentials")


def mask_token(token: str | None) -> str:
    """Safely mask an API token for console display and logs.

    Examples:
        'sk-proj-1234567890abcdef' -> 'sk-••••••cdef'
        'ghp_123456789' -> 'ghp_••••••6789'
        'short' -> '••••'
    """
    if not token or not token.strip():
        return "[NOT SET]"

    token_str = token.strip()
    if len(token_str) <= 8:
        return "••••"

    prefix = ""
    # Retain common provider prefix if present
    for pfx in ("sk-proj-", "sk-ant-", "sk-", "ghp_", "bearer "):
        if token_str.lower().startswith(pfx):
            prefix = token_str[: len(pfx)]
            break

    remaining = token_str[len(prefix) :]
    if len(remaining) <= 4:
        return f"{prefix}••••"

    suffix = remaining[-4:]
    return f"{prefix}••••••{suffix}"


class CredentialManager:
    """Secure credential resolver supporting environment variables and OS keyrings."""

    @staticmethod
    def resolve_token(credential_ref: str | None) -> str | None:
        """Resolve raw API token from a secure reference string.

        Supported reference formats:
            - 'env:OPENAI_API_KEY'
            - 'VAR_NAME' (treated as environment variable if format matches uppercase identifier)
            - 'keyring:service/account' or 'keychain:service/account'
        """
        if not credential_ref or not credential_ref.strip():
            return None

        ref = credential_ref.strip()

        # Handle 'env:VAR_NAME'
        if ref.lower().startswith("env:"):
            var_name = ref[4:].strip()
            return os.environ.get(var_name)

        # Handle 'keyring:service/account' or 'keychain:service/account'
        for prefix in ("keyring:", "keychain:"):
            if ref.lower().startswith(prefix):
                target = ref[len(prefix) :].strip()
                parts = target.split("/", 1)
                service = parts[0]
                account = parts[1] if len(parts) > 1 else "default"
                return CredentialManager._read_from_keyring(service, account)

        # Fallback to direct environment variable if alphanumeric/underscore
        if re.match(r"^[A-Z0-9_]+$", ref):
            return os.environ.get(ref)

        logger.debug("Unsupported credential reference format: %s", ref)
        return None

    @staticmethod
    def _read_from_keyring(service: str, account: str) -> str | None:
        """Read secret from system keyring if available, otherwise return None."""
        try:
            import keyring  # type: ignore[import-untyped]

            return keyring.get_password(service, account)
        except Exception as exc:
            logger.debug("System keyring lookup failed for %s/%s: %s", service, account, exc)
            return None

    @staticmethod
    def store_token(credential_ref: str, secret_token: str) -> bool:
        """Store secret in system keyring if reference uses keyring: syntax."""
        ref = credential_ref.strip()
        for prefix in ("keyring:", "keychain:"):
            if ref.lower().startswith(prefix):
                target = ref[len(prefix) :].strip()
                parts = target.split("/", 1)
                service = parts[0]
                account = parts[1] if len(parts) > 1 else "default"
                try:
                    import keyring  # type: ignore[import-untyped]

                    keyring.set_password(service, account, secret_token)
                    return True
                except Exception as exc:
                    logger.warning("Failed to store secret in keyring for %s/%s: %s", service, account, exc)
                    return False

        # Environment variables cannot be permanently modified from child process
        return False

    @staticmethod
    def get_credential_status(credential_ref: str | None) -> dict[str, Any]:
        """Inspect status of a credential reference without exposing the token."""
        if not credential_ref:
            return {
                "configured": False,
                "reference": None,
                "present": False,
                "display": "[NONE]",
            }

        token = CredentialManager.resolve_token(credential_ref)
        present = bool(token and token.strip())
        masked = mask_token(token) if present else "[NOT SET]"

        return {
            "configured": True,
            "reference": credential_ref,
            "present": present,
            "display": f"{credential_ref} ({masked})",
        }
