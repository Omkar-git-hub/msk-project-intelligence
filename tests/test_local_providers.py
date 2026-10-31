"""Unit tests for local AI adapters (Ollama, LocalOpenAI) and SSRF protection."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from msk.common.errors import MSKError
from msk.gateway.local import LocalOpenAIAdapter, OllamaAdapter, validate_local_endpoint
from msk.gateway.models import GatewayRequest, ModelConfig, ProviderConfig, ProviderType
from msk.gateway.registry import get_default_registry


def test_validate_local_endpoint_allowed():
    """Verify standard local endpoints pass validation."""
    validate_local_endpoint("http://localhost:11434")
    validate_local_endpoint("http://127.0.0.1:8000/v1")
    validate_local_endpoint("http://[::1]:11434")
    validate_local_endpoint("http://192.168.1.50:8000/v1")
    validate_local_endpoint("http://host.docker.internal:11434")


def test_validate_local_endpoint_blocks_metadata_ip():
    """Verify cloud metadata link-local IP (169.254.169.254) is rejected."""
    with pytest.raises(MSKError) as exc:
        validate_local_endpoint("http://169.254.169.254/latest/meta-data")
    assert "Security violation" in str(exc.value)


def test_ollama_adapter_creation_from_registry():
    """Verify Ollama adapter can be created via registry."""
    reg = get_default_registry()
    cfg = ProviderConfig(
        provider_id="my-ollama",
        display_name="My Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434",
        default_model="llama3",
        models=[ModelConfig(id="llama3")],
    )
    adapter = reg.create(cfg)
    assert isinstance(adapter, OllamaAdapter)
    assert adapter.provider_name == "my-ollama"


def test_ollama_adapter_completion():
    """Verify OllamaAdapter formats request and parses response from /api/chat."""
    cfg = ProviderConfig(
        provider_id="ollama",
        display_name="Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434",
        default_model="llama3",
    )
    adapter = OllamaAdapter(cfg)

    mock_response_data = {
        "model": "llama3",
        "message": {"role": "assistant", "content": "Payment API is handled in service.py."},
        "done": True,
        "eval_count": 25,
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        req = GatewayRequest(
            prompt="Where is payment handled?",
            system_prompt="You are a helpful assistant.",
            model="llama3",
        )
        resp = adapter.complete(req)

        assert resp.content == "Payment API is handled in service.py."
        assert resp.provider == "ollama"
        assert resp.model == "llama3"
        assert resp.tokens_used == 25

        # Check call arguments
        assert mock_urlopen.call_count == 1
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "http://localhost:11434/api/chat"
        payload = json.loads(called_req.data.decode("utf-8"))
        assert payload["model"] == "llama3"
        assert len(payload["messages"]) == 2


def test_ollama_adapter_daemon_offline():
    """Verify OllamaAdapter raises actionable MSKError when daemon is offline."""
    cfg = ProviderConfig(
        provider_id="ollama",
        display_name="Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434",
        default_model="llama3",
    )
    adapter = OllamaAdapter(cfg)

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        with pytest.raises(MSKError) as exc_info:
            adapter.complete(GatewayRequest(prompt="hello"))

        assert "Failed to communicate with Ollama" in str(exc_info.value)
        assert "ollama serve" in exc_info.value.hint


def test_ollama_adapter_list_models_and_health():
    """Verify OllamaAdapter health check and list_models."""
    cfg = ProviderConfig(
        provider_id="ollama",
        display_name="Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434",
        default_model="llama3",
    )
    adapter = OllamaAdapter(cfg)

    tags_data = {
        "models": [
            {"name": "llama3:latest"},
            {"name": "codellama:7b"},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(tags_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = adapter.list_models()
        assert models == ["llama3:latest", "codellama:7b"]

        health = adapter.check_health()
        assert health["status"] == "healthy"
        assert health["models_available"] == ["llama3:latest", "codellama:7b"]


def test_local_openai_adapter_completion():
    """Verify LocalOpenAIAdapter completion using /chat/completions."""
    cfg = ProviderConfig(
        provider_id="vllm-local",
        display_name="Local vLLM",
        provider_type=ProviderType.LOCAL_OPENAI,
        endpoint="http://localhost:8000/v1",
        default_model="mistral-7b",
    )
    adapter = LocalOpenAIAdapter(cfg, api_key="secret-token")

    mock_response_data = {
        "id": "chatcmpl-123",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Database is configured in db.py."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 50},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        req = GatewayRequest(prompt="Where is db configured?", model="mistral-7b")
        resp = adapter.complete(req)

        assert resp.content == "Database is configured in db.py."
        assert resp.tokens_used == 50

        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "http://localhost:8000/v1/chat/completions"
        assert called_req.headers.get("Authorization") == "Bearer secret-token"
