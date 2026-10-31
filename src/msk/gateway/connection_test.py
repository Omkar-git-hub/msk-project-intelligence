"""Six-stage connection testing engine for AI providers and models."""

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from msk.gateway.base import AIGatewayAdapter
from msk.gateway.credentials import CredentialManager
from msk.gateway.local import _safe_urlopen
from msk.gateway.models import (
    AuthType,
    GatewayRequest,
    ProviderConfig,
    ProviderType,
)
from msk.gateway.registry import get_default_registry

logger = logging.getLogger("msk.gateway.connection_test")


class StageStatus(StrEnum):
    __test__ = False
    """Result status for a connection test stage."""

    PASSED = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class StageResult(BaseModel):
    """Result for an individual test stage."""

    stage_name: str
    status: StageStatus
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestResult(BaseModel):
    """Full outcome of the 6-stage provider connection test."""

    provider_id: str
    provider_name: str
    model: str
    endpoint: str
    stages: dict[str, StageResult]
    latency_ms: float = 0.0
    overall: str = "FAILED"
    error_summary: str | None = None


class ConnectionTestRunner:
    """Orchestrates comprehensive, non-intrusive connection diagnostics for AI providers."""

    def __init__(self, registry=None) -> None:
        self.registry = registry or get_default_registry()

    def run_test(
        self,
        config: ProviderConfig,
        model_override: str | None = None,
    ) -> ConnectionTestResult:
        """Execute the 6-stage diagnostic test against a configured provider."""
        target_model = model_override or config.default_model
        stages: dict[str, StageResult] = {}
        t_start = time.perf_counter()

        # ----------------------------------------------------
        # Stage 1: Configuration Validation
        # ----------------------------------------------------
        config_ok = True
        config_err: str | None = None
        if not config.endpoint.startswith(("http://", "https://")):
            config_ok = False
            config_err = f"Invalid endpoint scheme in '{config.endpoint}'"
        elif not target_model:
            config_ok = False
            config_err = "No model configured or specified"

        stages["configuration"] = StageResult(
            stage_name="Configuration",
            status=StageStatus.PASSED if config_ok else StageStatus.FAIL,
            message=config_err or "Configuration schema and settings valid",
        )

        if not config_ok:
            return ConnectionTestResult(
                provider_id=config.provider_id,
                provider_name=config.display_name,
                model=target_model,
                endpoint=config.endpoint,
                stages=stages,
                latency_ms=0.0,
                overall="FAILED",
                error_summary=config_err,
            )

        # ----------------------------------------------------
        # Stage 2: Endpoint Connectivity
        # ----------------------------------------------------
        endpoint_ok = False
        endpoint_err: str | None = None
        if config.provider_type == ProviderType.MOCK:
            endpoint_ok = True
        else:
            try:
                health_url = f"{config.endpoint}/api/tags" if config.provider_type == ProviderType.OLLAMA else config.endpoint
                req = urllib.request.Request(
                    health_url,
                    headers={"User-Agent": "MSK-Connection-Tester"},
                    method="GET",
                )
                with _safe_urlopen(req, timeout=min(5.0, config.timeout_seconds)):
                    endpoint_ok = True
            except urllib.error.HTTPError as exc:
                # HTTP error (e.g. 401 or 404 or 405) proves the endpoint IS reachable
                endpoint_ok = True
                logger.debug("Endpoint HTTP check response: %s", exc.code)
            except urllib.error.URLError as exc:
                endpoint_ok = False
                endpoint_err = f"Endpoint unreachable: {exc.reason}"
            except Exception as exc:
                endpoint_ok = False
                endpoint_err = f"Endpoint connection failed: {exc}"

        stages["endpoint"] = StageResult(
            stage_name="Endpoint connectivity",
            status=StageStatus.PASSED if endpoint_ok else StageStatus.FAIL,
            message=endpoint_err or "Endpoint socket reachable",
        )

        if not endpoint_ok:
            return ConnectionTestResult(
                provider_id=config.provider_id,
                provider_name=config.display_name,
                model=target_model,
                endpoint=config.endpoint,
                stages=stages,
                latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                overall="FAILED",
                error_summary=endpoint_err,
            )

        # ----------------------------------------------------
        # Stage 3: Authentication
        # ----------------------------------------------------
        auth_ok = True
        auth_err: str | None = None
        api_token: str | None = None

        if config.auth_type != AuthType.NONE:
            if not config.credential_ref:
                auth_ok = False
                auth_err = f"Provider requires authentication ({config.auth_type.value}) but no credential_ref is set."
            else:
                api_token = CredentialManager.resolve_token(config.credential_ref)
                if not api_token:
                    auth_ok = False
                    auth_err = f"Credential reference '{config.credential_ref}' could not be resolved or is empty."

        stages["authentication"] = StageResult(
            stage_name="Authentication",
            status=StageStatus.PASSED if auth_ok else StageStatus.FAIL,
            message=auth_err or ("Credentials resolved" if config.auth_type != AuthType.NONE else "No auth required"),
        )

        if not auth_ok:
            return ConnectionTestResult(
                provider_id=config.provider_id,
                provider_name=config.display_name,
                model=target_model,
                endpoint=config.endpoint,
                stages=stages,
                latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                overall="FAILED",
                error_summary=auth_err,
            )

        # ----------------------------------------------------
        # Instantiate Adapter
        # ----------------------------------------------------
        try:
            adapter: AIGatewayAdapter = self.registry.create(config, api_key=api_token)
        except Exception as exc:
            err_msg = f"Failed to instantiate provider adapter: {exc}"
            stages["adapter_init"] = StageResult(
                stage_name="Adapter Initialization",
                status=StageStatus.FAIL,
                message=err_msg,
            )
            return ConnectionTestResult(
                provider_id=config.provider_id,
                provider_name=config.display_name,
                model=target_model,
                endpoint=config.endpoint,
                stages=stages,
                latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                overall="FAILED",
                error_summary=err_msg,
            )

        # ----------------------------------------------------
        # Stage 4: Model Availability
        # ----------------------------------------------------
        model_ok = True
        model_err: str | None = None
        if hasattr(adapter, "list_models"):
            try:
                available_models = adapter.list_models()
                if available_models:
                    # Check if target model or tag match exists
                    found = any(m.lower().startswith(target_model.lower()) for m in available_models)
                    if not found:
                        model_ok = False
                        model_err = (
                            f"Model '{target_model}' not found in provider catalog. "
                            f"Available models: {', '.join(available_models[:5])}"
                        )
            except Exception as exc:
                logger.debug("Could not inspect model catalog: %s", exc)

        stages["model_availability"] = StageResult(
            stage_name="Model availability",
            status=StageStatus.PASSED if model_ok else StageStatus.FAIL,
            message=model_err or f"Model '{target_model}' ready",
        )

        if not model_ok:
            return ConnectionTestResult(
                provider_id=config.provider_id,
                provider_name=config.display_name,
                model=target_model,
                endpoint=config.endpoint,
                stages=stages,
                latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                overall="FAILED",
                error_summary=model_err,
            )

        # ----------------------------------------------------
        # Stage 5: Minimal Test Inference
        # ----------------------------------------------------
        inference_ok = False
        inference_err: str | None = None
        test_latency = 0.0

        # Isolated minimal ping prompt - NEVER sends project source code
        test_req = GatewayRequest(
            prompt="ping",
            system_prompt="Respond strictly with 'pong'.",
            model=target_model,
            max_tokens=10,
        )

        t_inf_start = time.perf_counter()
        try:
            resp = adapter.complete(test_req)
            test_latency = (time.perf_counter() - t_inf_start) * 1000
            if resp and resp.content:
                inference_ok = True
            else:
                inference_err = "Provider returned an empty response"
        except urllib.error.HTTPError as exc:
            test_latency = (time.perf_counter() - t_inf_start) * 1000
            if exc.code == 401 or exc.code == 403:
                inference_err = "Authentication rejected: Invalid or unauthorized API key"
            elif exc.code == 404:
                inference_err = f"Model or endpoint not found (HTTP 404): {exc.reason}"
            elif exc.code == 429:
                inference_err = "Rate limit exceeded on provider"
            else:
                inference_err = f"Inference failed with HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            test_latency = (time.perf_counter() - t_inf_start) * 1000
            inference_err = f"Inference request failed: {exc}"

        stages["test_inference"] = StageResult(
            stage_name="Test inference",
            status=StageStatus.PASSED if inference_ok else StageStatus.FAIL,
            message=inference_err or "Minimal test inference succeeded",
        )

        total_latency = (time.perf_counter() - t_start) * 1000

        return ConnectionTestResult(
            provider_id=config.provider_id,
            provider_name=config.display_name,
            model=target_model,
            endpoint=config.endpoint,
            stages=stages,
            latency_ms=round(test_latency or total_latency, 2),
            overall="SUCCESS" if inference_ok else "FAILED",
            error_summary=inference_err,
        )
