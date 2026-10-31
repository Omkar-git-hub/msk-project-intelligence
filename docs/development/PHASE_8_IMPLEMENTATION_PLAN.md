# MSK Phase 8 — Implementation Plan
## Pluggable AI Gateway, Provider Management, Model Configuration & Developer AI Commands

### 1. Architectural Principles
- **Local-first & privacy by design**: The project stays private. AI gets only what it needs.
- **Fail-closed transmission gate**: Every context transmission must pass through `PolicyGatedGateway` and `PolicyEnforcementBoundary`.
- **Zero raw secrets persisted**: Credentials stored only in OS credential store or environment variables, referenced in `providers.json`.
- **Zero heavy SDK dependencies**: Use standard library HTTP requests (`urllib.request`) with robust timeout and error handling.
- **Full backward compatibility**: Retain existing Phase 1–7 behavior, schemas, and tests without regressions.
- **Workflow immutability**: `.github/workflows/` remains 100% unchanged.

---

### 2. Implementation Increments

#### Increment 1: Gateway Foundation & Core Models
- `src/msk/gateway/models.py`: Provider, model, request, response, and capability data models.
- `src/msk/gateway/base.py`: Protocol definition for `AIGatewayAdapter` and abstract base class.
- `src/msk/gateway/registry.py`: Registry and factory for resolving and instantiating adapters.
- Integration with `MockAIGateway` in `src/msk/policy/gateway.py`.
- Unit tests in `tests/test_gateway_models.py` and `tests/test_gateway_registry.py`.

#### Increment 2: Provider Configuration & Secure Credentials
- `src/msk/gateway/configuration.py`: Schema validation, atomic persistence (`.tmp` -> replace), CRUD operations for `.msk/config/providers.json`.
- `src/msk/gateway/credentials.py`: `CredentialManager` abstraction supporting `env:`, Windows Credential Manager, macOS Keychain, Linux Secret Service with token masking.
- `src/msk/cli/provider.py`: Subcommands `list`, `add`, `update`, `remove`, `enable`, `disable`, `set-default`.
- Unit tests in `tests/test_provider_configuration.py` and `tests/test_provider_credentials.py`.

#### Increment 3: Local AI Providers
- `src/msk/gateway/local.py`:
  - `OllamaAdapter`: Connect to local Ollama daemon, support model discovery (`/api/tags`), generation (`/api/chat`).
  - `LocalOpenAIAdapter`: Support local OpenAI-compatible endpoints (vLLM, llama.cpp, LocalAI).
  - Loopback validation and SSRF protection.
- Unit and integration tests in `tests/test_gateway_adapters.py`.

#### Increment 4: Connection Testing Framework & Provider CLI
- `src/msk/gateway/connection_test.py`:
  - 6-stage testing: Configuration, Endpoint Connectivity, Authentication, Model Availability, Test Inference, Latency.
  - Test prompt isolation (zero project code transmitted).
- `msk provider test [<provider-id>] [--model <model-name>]`.
- Unit tests in `tests/test_provider_connection.py`.

#### Increment 5: Cloud Providers & Policy Integration
- `src/msk/gateway/cloud.py`:
  - `OpenAIAdapter`: Standard-library HTTP adapter for OpenAI and Azure OpenAI.
  - `AnthropicAdapter`: Anthropic Messages API adapter.
  - `GeminiAdapter`: Google Gemini API adapter.
- `PolicyGatedGateway` enforcement, secret scrubbing, and audit logging.
- Unit and security tests in `tests/test_gateway_adapters.py` and `tests/test_gateway_privacy.py`.

#### Increment 6: Developer Commands (`msk ask`, `msk explain`)
- `src/msk/cli/ask.py`: `msk ask "<question>"` querying context and generating responses with citations.
- `src/msk/cli/explain.py`: `msk explain [--file] [--symbol] [--error] [--error-file]` with impact analysis and diagnosis.
- Rich terminal output and `--json` support.
- Unit and CLI integration tests in `tests/test_ask_cli.py` and `tests/test_explain_cli.py`.

#### Increment 7: CLI Diagnostics, Regression & Packaging
- Update `src/msk/cli/doctor.py` and `src/msk/cli/status.py` with provider status.
- Update `README.md` with full usage instructions.
- Execute full validation suite: `pytest`, `ruff check`, `bandit`, `python -m build`, `git diff --check`.
- Verify workflow immutability.
