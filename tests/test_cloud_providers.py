"""Unit tests for Cloud AI adapters (OpenAI, Anthropic, Gemini)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from msk.common.errors import MSKError
from msk.gateway.cloud import AnthropicAdapter, GeminiAdapter, OpenAIAdapter
from msk.gateway.models import GatewayRequest, ProviderConfig, ProviderType
from msk.gateway.registry import get_default_registry


def test_openai_adapter_completion():
    """Verify OpenAIAdapter sends proper request and parses response."""
    cfg = ProviderConfig(
        provider_id="openai-test",
        display_name="OpenAI Test",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    adapter = OpenAIAdapter(cfg, api_key="sk-test-secret")

    mock_resp_data = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Database is PostgreSQL."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 75},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("msk.gateway.cloud._safe_urlopen", return_value=mock_resp) as mock_urlopen:
        req = GatewayRequest(
            prompt="Which DB is used?",
            system_prompt="Be concise.",
            model="gpt-4o",
        )
        resp = adapter.complete(req)

        assert resp.content == "Database is PostgreSQL."
        assert resp.model == "gpt-4o"
        assert resp.tokens_used == 75

        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "https://api.openai.com/v1/chat/completions"
        assert called_req.headers["Authorization"] == "Bearer sk-test-secret"
        payload = json.loads(called_req.data.decode("utf-8"))
        assert payload["model"] == "gpt-4o"
        assert len(payload["messages"]) == 2


def test_openai_adapter_missing_key():
    """Verify OpenAIAdapter raises MSKError when api_key is None."""
    cfg = ProviderConfig(
        provider_id="openai-test",
        display_name="OpenAI Test",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    adapter = OpenAIAdapter(cfg, api_key=None)
    with pytest.raises(MSKError) as exc:
        adapter.complete(GatewayRequest(prompt="hello"))
    assert "Missing API key" in str(exc.value)


def test_anthropic_adapter_completion():
    """Verify AnthropicAdapter messages format and header handling."""
    cfg = ProviderConfig(
        provider_id="claude-test",
        display_name="Anthropic Test",
        provider_type=ProviderType.ANTHROPIC,
        endpoint="https://api.anthropic.com/v1",
        default_model="claude-3-5-sonnet-20241022",
    )
    adapter = AnthropicAdapter(cfg, api_key="sk-ant-test")

    mock_resp_data = {
        "id": "msg-123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Billing logic resides in src/billing.py"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 40, "output_tokens": 15},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("msk.gateway.cloud._safe_urlopen", return_value=mock_resp) as mock_urlopen:
        req = GatewayRequest(
            prompt="Where is billing logic?",
            system_prompt="Answer directly.",
            model="claude-3-5-sonnet-20241022",
        )
        resp = adapter.complete(req)

        assert resp.content == "Billing logic resides in src/billing.py"
        assert resp.tokens_used == 55

        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "https://api.anthropic.com/v1/messages"
        assert called_req.headers["X-api-key"] == "sk-ant-test"
        assert called_req.headers["Anthropic-version"] == "2023-06-01"
        payload = json.loads(called_req.data.decode("utf-8"))
        assert payload["system"] == "Answer directly."
        assert len(payload["messages"]) == 1


def test_gemini_adapter_completion():
    """Verify GeminiAdapter REST format and header handling."""
    cfg = ProviderConfig(
        provider_id="gemini-test",
        display_name="Gemini Test",
        provider_type=ProviderType.GEMINI,
        endpoint="https://generativelanguage.googleapis.com",
        default_model="gemini-1.5-pro",
    )
    adapter = GeminiAdapter(cfg, api_key="AIzaSyTestKey")

    mock_resp_data = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Auth uses JWT and OAuth2."}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"totalTokenCount": 65},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("msk.gateway.cloud._safe_urlopen", return_value=mock_resp) as mock_urlopen:
        req = GatewayRequest(
            prompt="Explain auth",
            system_prompt="Short answer",
            model="gemini-1.5-pro",
        )
        resp = adapter.complete(req)

        assert resp.content == "Auth uses JWT and OAuth2."
        assert resp.tokens_used == 65

        called_req = mock_urlopen.call_args[0][0]
        assert "models/gemini-1.5-pro:generateContent" in called_req.full_url
        assert called_req.headers["X-goog-api-key"] == "AIzaSyTestKey"


def test_registry_creates_cloud_adapters():
    """Verify default registry instantiates all cloud providers."""
    reg = get_default_registry()

    openai_p = reg.create(
        ProviderConfig(
            provider_id="p-openai",
            display_name="OAI",
            provider_type=ProviderType.OPENAI,
            endpoint="https://api.openai.com/v1",
            default_model="gpt-4o",
        ),
        api_key="k1",
    )
    assert isinstance(openai_p, OpenAIAdapter)

    anthropic_p = reg.create(
        ProviderConfig(
            provider_id="p-claude",
            display_name="Claude",
            provider_type=ProviderType.ANTHROPIC,
            endpoint="https://api.anthropic.com/v1",
            default_model="claude-3-5",
        ),
        api_key="k2",
    )
    assert isinstance(anthropic_p, AnthropicAdapter)

    gemini_p = reg.create(
        ProviderConfig(
            provider_id="p-gemini",
            display_name="Gemini",
            provider_type=ProviderType.GEMINI,
            endpoint="https://generativelanguage.googleapis.com",
            default_model="gemini-1.5",
        ),
        api_key="k3",
    )
    assert isinstance(gemini_p, GeminiAdapter)
