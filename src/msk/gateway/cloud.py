"""Cloud AI provider adapters (OpenAI, Anthropic, Google Gemini) via standard library."""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from msk.common.errors import MSKError
from msk.gateway.base import BaseGatewayAdapter
from msk.gateway.local import _safe_urlopen
from msk.gateway.models import GatewayRequest, GatewayResponse

logger = logging.getLogger("msk.gateway.cloud")


class OpenAIAdapter(BaseGatewayAdapter):
    """Adapter for OpenAI API and Azure OpenAI chat completions."""

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        endpoint = self.config.endpoint.rstrip("/")
        if endpoint.endswith("/chat/completions"):
            url = endpoint
        elif endpoint.endswith("/v1"):
            url = f"{endpoint}/chat/completions"
        else:
            url = f"{endpoint}/v1/chat/completions"

        model_name = request.model or self.config.default_model

        if not self.api_key:
            raise MSKError(
                f"Missing API key for OpenAI provider '{self.provider_name}'.",
                hint=f"Set the credential reference (e.g. {self.config.credential_ref or 'env:OPENAI_API_KEY'}).",
            )

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

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(self.config.custom_headers)

        req_data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        t0 = time.perf_counter()
        try:
            with _safe_urlopen(http_req, timeout=self.config.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latency = (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise MSKError("OpenAI authentication failed: Invalid or expired API key.") from exc
            if exc.code == 404:
                raise MSKError(f"OpenAI model '{model_name}' or endpoint not found (HTTP 404).") from exc
            if exc.code == 429:
                raise MSKError("OpenAI rate limit exceeded or quota exhausted.") from exc
            raise MSKError(f"OpenAI API error (HTTP {exc.code}): {err_body[:200]}") from exc
        except Exception as exc:
            raise MSKError(f"Failed to communicate with OpenAI API: {exc}") from exc

        choices = data.get("choices", [])
        content = ""
        finish_reason = "stop"
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason", "stop")

        usage = data.get("usage", {})
        tokens_used = usage.get("total_tokens")

        return GatewayResponse(
            content=content,
            provider=self.provider_name,
            model=model_name,
            latency_ms=round(latency, 2),
            tokens_used=tokens_used,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def check_health(self) -> dict[str, Any]:
        """Health check querying OpenAI /v1/models endpoint."""
        endpoint = self.config.endpoint.rstrip("/")
        base_url = endpoint[:-17] if endpoint.endswith("/chat/completions") else endpoint
        models_url = f"{base_url}/models" if not base_url.endswith("/v1") else f"{base_url}/models"

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
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
                    "models_available": models[:10],
                }
        except Exception as exc:
            return {
                "status": "unreachable",
                "provider_id": self.provider_name,
                "endpoint": self.config.endpoint,
                "error": str(exc),
            }


class AnthropicAdapter(BaseGatewayAdapter):
    """Adapter for Anthropic Claude Messages API."""

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        endpoint = self.config.endpoint.rstrip("/")
        if endpoint.endswith("/v1/messages"):
            url = endpoint
        elif endpoint.endswith("/v1"):
            url = f"{endpoint}/messages"
        else:
            url = f"{endpoint}/v1/messages"

        model_name = request.model or self.config.default_model

        if not self.api_key:
            raise MSKError(
                f"Missing API key for Anthropic provider '{self.provider_name}'.",
                hint=f"Set the credential reference (e.g. {self.config.credential_ref or 'env:ANTHROPIC_API_KEY'}).",
            )

        messages = [{"role": "user", "content": request.prompt}]
        max_tokens = request.max_tokens or 4096

        body: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": request.temperature,
        }
        if request.system_prompt:
            body["system"] = request.system_prompt

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        headers.update(self.config.custom_headers)

        req_data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        t0 = time.perf_counter()
        try:
            with _safe_urlopen(http_req, timeout=self.config.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latency = (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise MSKError("Anthropic authentication failed: Invalid API key.") from exc
            if exc.code == 404:
                raise MSKError(f"Anthropic model '{model_name}' not found (HTTP 404).") from exc
            if exc.code == 429:
                raise MSKError("Anthropic rate limit exceeded.") from exc
            raise MSKError(f"Anthropic API error (HTTP {exc.code}): {err_body[:200]}") from exc
        except Exception as exc:
            raise MSKError(f"Failed to communicate with Anthropic API: {exc}") from exc

        content_blocks = data.get("content", [])
        content_text = ""
        for block in content_blocks:
            if block.get("type") == "text":
                content_text += block.get("text", "")

        usage = data.get("usage", {})
        tokens_used = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)

        return GatewayResponse(
            content=content_text,
            provider=self.provider_name,
            model=model_name,
            latency_ms=round(latency, 2),
            tokens_used=tokens_used or None,
            finish_reason=data.get("stop_reason", "stop"),
            raw_response=data,
        )

    def check_health(self) -> dict[str, Any]:
        """Health check verification for Anthropic endpoint."""
        return {
            "status": "healthy" if bool(self.api_key) else "missing_key",
            "provider_id": self.provider_name,
            "endpoint": self.config.endpoint,
        }


class GeminiAdapter(BaseGatewayAdapter):
    """Adapter for Google Gemini API."""

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        model_name = request.model or self.config.default_model
        endpoint = self.config.endpoint.rstrip("/")
        # Gemini REST URL pattern
        url = f"{endpoint}/v1beta/models/{model_name}:generateContent"

        if not self.api_key:
            raise MSKError(
                f"Missing API key for Google Gemini provider '{self.provider_name}'.",
                hint=f"Set the credential reference (e.g. {self.config.credential_ref or 'env:GEMINI_API_KEY'}).",
            )

        contents = [{"role": "user", "parts": [{"text": request.prompt}]}]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
            },
        }
        if request.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        if request.max_tokens:
            body["generationConfig"]["maxOutputTokens"] = request.max_tokens

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        headers.update(self.config.custom_headers)

        req_data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        t0 = time.perf_counter()
        try:
            with _safe_urlopen(http_req, timeout=self.config.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latency = (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and "API_KEY_INVALID" in err_body:
                raise MSKError("Gemini authentication failed: Invalid API key.") from exc
            if exc.code == 404:
                raise MSKError(f"Gemini model '{model_name}' not found (HTTP 404).") from exc
            if exc.code == 429:
                raise MSKError("Gemini rate limit exceeded.") from exc
            raise MSKError(f"Gemini API error (HTTP {exc.code}): {err_body[:200]}") from exc
        except Exception as exc:
            raise MSKError(f"Failed to communicate with Gemini API: {exc}") from exc

        candidates = data.get("candidates", [])
        content_text = ""
        finish_reason = "stop"
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            content_text = "".join(p.get("text", "") for p in parts)
            finish_reason = candidates[0].get("finishReason", "stop")

        usage = data.get("usageMetadata", {})
        tokens_used = usage.get("totalTokenCount")

        return GatewayResponse(
            content=content_text,
            provider=self.provider_name,
            model=model_name,
            latency_ms=round(latency, 2),
            tokens_used=tokens_used,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def check_health(self) -> dict[str, Any]:
        """Health check verification for Gemini endpoint."""
        return {
            "status": "healthy" if bool(self.api_key) else "missing_key",
            "provider_id": self.provider_name,
            "endpoint": self.config.endpoint,
        }
