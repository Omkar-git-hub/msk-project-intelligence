# MSK Phase 9 — Implementation Plan
## Local-First Runtime Intelligence & Static-Runtime Knowledge Correlation

### 1. Architectural Principles
- **Local-first & privacy by design**: The project stays private. AI gets only what it needs. All runtime analysis and storage occur strictly on the local machine within the `.msk` project directory.
- **Static-to-runtime correlation**: Connect runtime observations (logs, exceptions, stack traces, metrics) with the existing MSK Knowledge Model (files, symbols, call graphs, tests, APIs, dependencies) without duplicating or replacing existing graphs.
- **Explicit boundary between facts and inferences**: Clearly distinguish observed facts (stack frames, exceptions, timestamps, counts) from inferred possibilities (affected callers, potentially degraded APIs, suggested tests).
- **Zero code execution or intrusive instrumentation**: Do not execute or inject arbitrary code into user projects. Operate safely on developer-provided logs, traces, or runtime observations.
- **Fail-safe & resilient parsing**: Malformed, partial, or corrupted logs never crash MSK; graceful recovery and partial ingestion are enforced.
- **Zero credentials or secret leaks**: Scrub sensitive parameters, tokens, and credentials prior to storage and display using MSK privacy filters. Strictly avoid credential-shaped test data to guarantee clean Gitleaks scans.
- **Full backward compatibility**: Retain existing Phase 1–8 functionality, database schemas, and CLI behavior without regressions.
- **Workflow immutability**: `.github/workflows/` remains 100% unchanged.

---

### 2. Implementation Increments

#### Increment 1: Runtime Models, Sanitizer & Multi-Format Log Parser
- `src/msk/runtime/models.py`:
  - `RuntimeEvent`: Single parsed event (timestamp, level, message, event_type, source, file_path, line_number, symbol_name, exception_type, stack_trace, attributes).
  - `StackFrame`: Normalized stack trace frame (file_path, line_number, function_name, code_snippet, is_project_file).
  - `StackTrace`: List of frames, exception type, exception message.
  - `RuntimeErrorGroup`: Aggregated recurring error summary (fingerprint, exception_type, message_template, first_seen, last_seen, count, severity, file_path, symbol_name).
  - `RuntimeCorrelationReport`: Detailed mapping of runtime failure to static intelligence (file, symbol, callers, callees, dependent modules, tests, APIs).
  - `RuntimeImpactReport`: Evaluation of runtime impact distinguishing observed facts from inferred possibilities.
  - `RuntimeStatusSummary`: High-level summary of runtime observations for status commands.
- `src/msk/runtime/sanitizer.py`:
  - Sanitize runtime text and error messages using MSK's `PrivacyFilter`.
  - Scrub query params, auth headers, and sensitive key-values.
  - Project root path normalization and path traversal validation.
- `src/msk/runtime/parser.py`:
  - Multi-format parser supporting:
    - Structured JSON logs.
    - Standard timestamped log formats (`[YYYY-MM-DD HH:MM:SS] [LEVEL] message`, syslog, logfmt).
    - Multi-line stack traces (Python `Traceback`, Java `Exception in thread`, Node/JavaScript `Error: ... at ...`).
    - API metadata and performance observation lines.
  - Deterministic error fingerprinting for deduplication and grouping.
  - Robust handling of malformed lines, partial chunks, and empty inputs.
- Unit tests in `tests/test_runtime_parser.py` and `tests/test_runtime_sanitizer.py`.

#### Increment 2: Runtime Intelligence Storage & Retention Management
- Schema updates in `src/msk/storage/database.py`:
  - `runtime_events` table (project-scoped, indexed by timestamp, level, fingerprint, file).
  - `runtime_errors` table (fingerprint, recurrence count, first_seen, last_seen, severity).
  - Non-destructive migration in `initialize_schema()`.
- `src/msk/runtime/storage.py`:
  - `RuntimeStorage`: Transaction-safe SQLite operations for events, error groups, and timeline queries.
  - Project-specific isolation guaranteeing no cross-project data leakage.
  - Configurable retention pruning: `prune_events(max_events, retention_days)` to control `.msk` footprint.
- Unit tests in `tests/test_runtime_storage.py`.

#### Increment 3: Runtime and Static Knowledge Correlator
- `src/msk/runtime/correlator.py`:
  - `RuntimeCorrelator`: Bridge between runtime events and `KnowledgeRepository` / `KnowledgeGraph`.
  - File resolution: maps absolute or relative stack trace paths to indexed `FileEntity`.
  - Symbol resolution: matches line numbers and function names to `SymbolEntity` in the knowledge model.
  - Dependency & relationship navigation: retrieves callers/callees via `SYMBOL_CALLS_SYMBOL`, dependent files via `FILE_DEPENDS_ON_FILE`.
  - Test association: finds tests covering the affected symbol (`TEST_TARGETS_SYMBOL`) or file.
  - API endpoint association: detects API endpoints whose handlers call or are called by the affected symbol.
- Unit tests in `tests/test_runtime_correlator.py`.

#### Increment 4: Runtime Impact Analysis Engine
- `src/msk/runtime/impact.py`:
  - `RuntimeImpactEngine`: Analyzes blast radius of runtime failures.
  - Distinguishes observed facts (observed failure point, observed stack frames) from inferred possibilities (affected upstream callers, impacted APIs, potentially failing tests).
  - Generates actionable debugging and investigation recommendations.
- Unit tests in `tests/test_runtime_impact.py`.

#### Increment 5: Runtime Intelligence Engine
- `src/msk/runtime/engine.py`:
  - `RuntimeIntelligenceEngine`: Central facade orchestrating parsing, sanitization, correlation, storage, querying, and impact analysis.
- `src/msk/runtime/__init__.py`: Public exports.
- Integration tests in `tests/test_runtime_engine.py`.

#### Increment 6: Runtime CLI Integration
- `src/msk/cli/runtime.py`:
  - Typer CLI group `msk runtime`:
    - `msk runtime status`: Runtime metrics and error summary.
    - `msk runtime analyze`: Ingest and analyze log file or raw error string.
    - `msk runtime logs`: Ingest and inspect logs.
    - `msk runtime errors`: List observed runtime errors with filtering.
    - `msk runtime history`: Chronological timeline and failure sequences.
    - `msk runtime correlate`: Deep correlation report for an error or symbol.
    - `msk runtime prune`: Manage retention and cleanup.
- Register `runtime_app` in `src/msk/cli/main.py`.
- Update `src/msk/cli/status.py` and `src/msk/cli/doctor.py`.
- CLI integration tests in `tests/test_runtime_cli.py`.

#### Increment 7: Validation, Packaging & Documentation
- Update `README.md` with Phase 9 runtime commands and architecture.
- Full validation: `pytest -q`, `ruff check .`, `bandit -r src -c pyproject.toml`, `gitleaks protect --staged --redact --verbose --exit-code 2`.
- Verify workflow immutability (`git diff -- .github/workflows/`).
