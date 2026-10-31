
"""Unit tests for secure credential management and token resolution."""

from msk.gateway.credentials import CredentialManager, mask_token


def test_mask_token():
    """Verify sensitive tokens are masked correctly without leaking content."""
    assert mask_token(None) == "[NOT SET]"
    assert mask_token("") == "[NOT SET]"
    assert mask_token("   ") == "[NOT SET]"
    assert mask_token("short") == "••••"

    # Verify long tokens are fully masked except for the last four characters.
    test_token = "dummy-openai-token-sample"
    masked_token = mask_token(test_token)

    assert masked_token == "••••••mple"
    assert test_token not in masked_token
    assert "token-sample" not in masked_token

    # Verify another long token.
    another_token = "dummy-anthropic-token"
    masked_anthropic = mask_token(another_token)

    assert masked_anthropic == "••••••oken"
    assert another_token not in masked_anthropic


def test_resolve_token_from_env(monkeypatch):
    """Verify resolution of tokens via environment variable references."""
    test_value = "dummy-test-value"

    monkeypatch.setenv("TEST_TOKEN_VAR", test_value)

    # env:VARIABLE syntax
    token = CredentialManager.resolve_token("env:TEST_TOKEN_VAR")
    assert token == test_value

    # Direct uppercase environment variable syntax
    direct_token = CredentialManager.resolve_token("TEST_TOKEN_VAR")
    assert direct_token == test_value

    # Missing or empty references safely return None.
    assert CredentialManager.resolve_token("env:NONEXISTENT_KEY") is None
    assert CredentialManager.resolve_token(None) is None
    assert CredentialManager.resolve_token("") is None


def test_get_credential_status(monkeypatch):
    """Verify credential status reporting and secret masking."""

    # No credential configured.
    status_none = CredentialManager.get_credential_status(None)

    assert not status_none["configured"]
    assert not status_none["present"]
    assert status_none["display"] == "[NONE]"

    # Configured credential.
    test_value = "dummy-credential-value"
    monkeypatch.setenv("TEST_TOKEN_VAR", test_value)

    status_set = CredentialManager.get_credential_status(
        "env:TEST_TOKEN_VAR"
    )

    assert status_set["configured"]
    assert status_set["present"]
    assert status_set["display"] == "env:TEST_TOKEN_VAR (••••••alue)"
    assert test_value not in status_set["display"]

    # Configured environment variable that does not exist.
    status_unset = CredentialManager.get_credential_status(
        "env:UNSET_KEY"
    )

    assert status_unset["configured"]
    assert not status_unset["present"]
    assert "[NOT SET]" in status_unset["display"]
