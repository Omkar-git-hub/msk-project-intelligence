"""Local AI provider adapters (Ollama, Local OpenAI-compatible) with SSRF protection."""

import ipaddress
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from msk.common.errors import MSKError
from msk.gateway.base import BaseGatewayAdapter
from msk.gateway.models import GatewayRequest, GatewayResponse, ProviderConfig

logger = logging.getLogger("msk.gateway.local")


def validate_local_endpoint(endpoint: str) -> None:
    """Validate that a local provider endpoint resolves to a safe local/private network address.

    Blocks cloud metadata endpoints (e.g. 169.254.169.254) to prevent Server-Side Request Forgery.
    """
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise MSKError(
            f"Invalid endpoint scheme '{parsed.scheme}'. Only http and https are allowed.",
            hint="Specify an endpoint starting with 'http://' or 'https://'.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise MSKError(
            f"Invalid endpoint URL: '{endpoint}' - missing host.",
            hint="Specify a valid URL such as 'http://localhost:11434'.",
        )

    # Allow localhost names directly
    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        return

    # Check IP addresses
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback:
            return
        if ip.is_link_local:
            raise MSKError(
                f"Security violation: Link-local/metadata IP '{hostname}' is not permitted for local providers.",
                hint="Use loopback addresses (127.0.0.1) or safe private LAN IPs.",
            )
        if ip.is_private:
            return
    except ValueError:
        # Not a raw IP address; standard local domain names allowed
        if hostname.lower().endswith(".local") or hostname.lower().endswith(".lan") or hostname.lower() == "host.docker.internal":
            return

    # If it's a public domain or unexpected remote IP, warn
    logger.warning("Local provider '%s' configured with non-loopback host '%s'", endpoint, hostname)


def _safe_urlopen(req: urllib.request.Request, timeout: float) -> Any:
    """Execute HTTP request with strict scheme audit to satisfy security requirements."""
    url = req.full_url
    if not (url.startswith("http://") or url.startswith("https://")):
        raise MSKError(f"Disallowed URL scheme in '{url}'. Only http:// and https:// are permitted.")
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310


class OllamaAdapter(BaseGatewayAdapter):
    """Adapter for local Ollama instance (default http://localhost:11434)."""

    def __init__(self, config: ProviderConfig, api_key: str | None = None) -> None:
        super().__init__(config, api_key=api_key)
        validate_local_endpoint(self.config.endpoint)

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Execute a completion via Ollama's /api/chat endpoint."""
        url = f"{self.config.endpoint}/api/chat"
        model_name = request.model or self.config.default_model

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        body = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens:
            body["options"]["num_predict"] = request.max_tokens

        req_data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with _safe_urlopen(http_req, timeout=self.config.timeout_seconds) as resp:
                resp_bytes = resp.read()
                latency_ms = (time.perf_counter() - t0) * 1000
                data = json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.URLError as exc:
            raise MSKError(
                f"Failed to communicate with Ollama at '{self.config.endpoint}': {exc.reason}",
                hint="Is Ollama running? Run 'ollama serve' or check your connection.",
            ) from exc
        except Exception as exc:
            raise MSKError(f"Unexpected error querying Ollama: {exc}") from exc

        message = data.get("message", {})
        content = message.get("content", "")
        tokens_used = data.get("eval_count") or data.get("prompt_eval_count")

        return GatewayResponse(
            content=content,
            provider=self.provider_name,
            model=model_name,
            latency_ms=round(latency_ms, 2),
            tokens_used=tokens_used,
            finish_reason="stop",
            raw_response=data,
        )

    def list_models(self) -> list[str]:
        """Fetch available local models from Ollama /api/tags."""
        url = f"{self.config.endpoint}/api/tags"
        try:
            req = urllib.request.Request(url, method="GET")
            with _safe_urlopen(req, timeout=self.config.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                return [m.get("name") for m in models if m.get("name")]
        except Exception as exc:
            logger.debug("Failed to list Ollama models: %s", exc)
            return []

    def check_health(self) -> dict[str, Any]:
        """Verify Ollama service is reachable and responsive."""
        url = f"{self.config.endpoint}/api/tags"
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, method="GET")
            with _safe_urlopen(req, timeout=min(5.0, self.config.timeout_seconds)) as resp:
                latency = (time.perf_counter() - t0) * 1000
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return {
                    "status": "healthy",
                    "provider_id": self.provider_name,
                    "endpoint": self.config.endpoint,
                    "latency_ms": round(latency, 2),
                    "models_available": models,
                }
        except urllib.error.URLError as exc:
            return {
                "status": "unreachable",
                "provider_id": self.provider_name,
                "endpoint": self.config.endpoint,
                "error": str(exc.reason),
            }
        except Exception as exc:
            return {
                "status": "error",
                "provider_id": self.provider_name,
                "endpoint": self.config.endpoint,
                "error": str(exc),
            }


class LocalOpenAIAdapter(BaseGatewayAdapter):
    """Adapter for local OpenAI-compatible backends (vLLM, llama.cpp, LocalAI)."""

    def __init__(self, config: ProviderConfig, api_key: str | None = None) -> None:
        super().__init__(config, api_key=api_key)
        validate_local_endpoint(self.config.endpoint)

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Execute a completion via /chat/completions."""
        endpoint = self.config.endpoint.rstrip("/")
        url = f"{endpoint}/chat/completions" if not endpoint.endswith("/chat/completions") else endpoint
        model_name = request.model or self.config.default_model

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        body: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.config.custom_headers)

        req_data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        t0 = time.perf_counter()
        try:
            with _safe_urlopen(http_req, timeout=self.config.timeout_seconds) as resp:
                resp_bytes = resp.read()
                latency_ms = (time.perf_counter() - t0) * 1000
                data = json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.URLError as exc:
            raise MSKError(
                f"Failed to communicate with local OpenAI service at '{self.config.endpoint}': {exc.reason}",
                hint="Verify the local service (vLLM, LocalAI, llama.cpp) is running.",
            ) from exc
        except Exception as exc:
            raise MSKError(f"Unexpected error querying local OpenAI service: {exc}") from exc

        choices = data.get("choices", [])
        content = ""
        finish_reason = "stop"
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            finish_reason = choices[0].get("finish_reason", "stop")

        usage = data.get("usage", {})
        tokens_used = usage.get("total_tokens")

        return GatewayResponse(
            content=content,
            provider=self.provider_name,
            model=model_name,
            latency_ms=round(latency_ms, 2),
            tokens_used=tokens_used,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def check_health(self) -> dict[str, Any]:
        """Verify local OpenAI service endpoint."""
        endpoint = self.config.endpoint.rstrip("/")
        base_url = endpoint[:-17] if endpoint.endswith("/chat/completions") else endpoint
        models_url = f"{base_url}/models"

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(models_url, headers=headers, method="GET")
            with _safe_urlopen(req, timeout=min(5.0, self.config.timeout_seconds)) as resp:
                latency = (time.perf_counter() - t0) * 1000
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                return {
                    "status": "healthy",
                    "provider_id": self.provider_name,
                    "endpoint": self.config.endpoint,
                    "latency_ms": round(latency, 2),
                    "models_available": models,
                }
        except Exception as exc:
            return {
                "status": "unreachable",
                "provider_id": self.provider_name,
                "endpoint": self.config.endpoint,
                "error": str(exc),
            }
