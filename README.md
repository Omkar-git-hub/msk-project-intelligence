# MSK

> **The project stays private. AI gets only what it needs.**

MSK is a **local-first Project Intelligence, Privacy, and Security layer for software engineering**.

> [!WARNING]
> **Alpha software** (`v0.1.0-alpha.1`). APIs and CLI may change between releases.

---

## What is MSK?

A modern software project often contains sensitive business logic, database models, internal network topologies, and proprietary algorithms. Traditional AI assistants demand sending large portions (or entirety) of source repositories to external cloud providers.

MSK establishes a controlled, local-first intelligence boundary. It analyzes your software project locally on your machine, constructs an offline **Project Knowledge Model** (stored in SQLite), and determines the minimum relevant context required when interacting with AI systems.

```text
                    GIT (Source of truth)
                             │
                             ▼
              ┌─────────────────────────────┐
              │             MSK             │
              │                             │
              │  Local Project Intelligence │
              │  Knowledge Graph (SQLite)   │
              │  Security & Privacy Gateway │
              │  Minimum Context Engine     │
              └──────────────┬──────────────┘
                             │
                             ▼ (Policy-approved minimum context)
                            AI
```

### Key Principles

1. **Simple Outside. Complex Inside**: Familiar, Git-like CLI (`msk init`, `msk status`, `msk update`, `msk doctor`).
2. **Git is Source of Truth**: MSK adds intelligence on top of Git; it never replaces or tampers with your Git history.
3. **Offline-First & Local Analysis**: Core project scanning, Tree-sitter parsing, and knowledge model creation run 100% locally with zero internet dependency.
4. **Regeneratable Knowledge**: The `.msk/` directory contains metadata, fingerprints, symbols, and relationship graphs—never redundant source code duplicates. It can be recreated from scratch at any time.
5. **Security & Boundary Enforcement**: Secrets, credentials, and sensitive data are classified and blocked from leaving your boundary.

---

## Installation

### From source (recommended for alpha)

```bash
# Clone the repository
git clone https://github.com/Omkar-git-hub/msk-project-intelligence.git
cd msk-project-intelligence

# Create a virtual environment
python -m venv .venv

# Activate it
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# Install MSK
pip install -e .

# Or with development dependencies
pip install -e ".[dev]"
```

### From wheel

```bash
pip install dist/msk-0.1.0a1-py3-none-any.whl
```

---

## Quick Start

```bash
# 1. Check your environment is ready
msk doctor

# 2. Initialize project analysis (creates .msk/ directory)
msk init

# 3. View project intelligence summary
msk status

# 4. Incrementally update after code changes
msk update

# 5. Analyze impact before making code modifications
msk impact --file src/payment/service.py
msk impact --symbol process_payment --file src/payment/service.py

# 6. Force a full re-analysis
msk init --force
```

---

## CLI Reference

| Command      | Description                                                           |
| ------------ | --------------------------------------------------------------------- |
| `msk init`   | Analyze the project and build the Project Knowledge Model             |
| `msk status` | Display project intelligence: files, symbols, dependencies, security  |
| `msk update` | Incrementally update the knowledge model based on changed files       |
| `msk impact` | Analyze downstream impact of modifying a file, symbol, or dependency  |
| `msk doctor` | Diagnose environment: Python, Tree-sitter parsers, Git, database      |

### Global Options

| Option       | Description                    |
| ------------ | ------------------------------ |
| `--verbose`  | Enable verbose log output      |
| `--debug`    | Show full debug tracebacks     |
| `--help`     | Show help and exit             |

---

## Project Knowledge Model

MSK builds a structured model of your project stored in SQLite (`.msk/msk.db`), with JSON exports for debugging and interoperability (`.msk/exports/`).

### What MSK captures

| Entity              | Description                                                          |
| ------------------- | -------------------------------------------------------------------- |
| **Files**           | All source files with language, type (source/test/config), hash      |
| **Symbols**         | Classes, functions, methods extracted via Tree-sitter AST parsing    |
| **Dependencies**    | External package dependencies (requirements.txt, package.json, etc.) |
| **Relationships**   | File→Symbol, Symbol→Symbol calls, imports, test targets              |
| **Tests**           | Test classes and functions with their target symbols                  |
| **API Endpoints**   | Flask/FastAPI route decorators with HTTP methods                     |
| **Infrastructure**  | Dockerfile, docker-compose, GitHub Actions, Kubernetes, Terraform    |
| **Security**        | Secret/credential detection (privacy-preserving: no raw values)      |

### Supported Languages

| Language    | Parsing | Symbols | Imports | Call Graph |
| ----------- | ------- | ------- | ------- | ---------- |
| Python      | ✅      | ✅      | ✅      | ✅         |
| Java        | ✅      | ✅      | ✅      | ✅         |
| TypeScript  | ✅      | ✅      | ✅      | ✅         |
| JavaScript  | ✅      | ✅      | ✅      | ✅         |

### Relationship Types

| Type                          | Example                                    |
| ----------------------------- | ------------------------------------------ |
| `FILE_CONTAINS_SYMBOL`        | `controller.py` → `handle_payment()`       |
| `SYMBOL_IMPORTS_SYMBOL`       | `service.py` imports `PaymentRepository`   |
| `SYMBOL_CALLS_SYMBOL`         | `handle_payment()` calls `process_payment()`|
| `FILE_DEPENDS_ON_FILE`        | `controller.py` depends on `service.py`    |
| `TEST_TARGETS_SYMBOL`         | `test_process_payment` → `process_payment` |

### `.msk/` Directory Layout

```text
.msk/
├── msk.db          # SQLite knowledge database (git-ignored)
├── project.json    # Deterministic project config
├── policy.json     # Privacy/security policy
├── exports/        # JSON exports for debugging/interop
│   ├── structure.json
│   ├── symbols.json
│   ├── graph.json
│   ├── tests.json
│   ├── apis.json
│   ├── infrastructure.json
│   └── security.json
└── cache/          # Temporary cache (git-ignored)
```

The `.msk/` directory is **fully regeneratable** from source. You can delete it and run `msk init` to rebuild everything.

### Incremental Change Engine

When your codebase evolves, `msk update` incrementally updates the knowledge model:

1. **Cryptographic Change Detection**: Compares current files against SQLite records using streaming SHA-256 content hashes (not relying on volatile modification timestamps alone).
2. **Selective AST Analysis**: Analyzes **only** newly added, modified, or renamed files. Unchanged files are skipped, preserving existing knowledge records without re-parsing.
3. **Rename Detection**: Unambiguous file moves/renames are detected by matching content hashes across deleted and added paths.
4. **Stale Record Cleanup & Cascades**: Deleted or modified files cascade-delete old symbols, tests, APIs, and clean up orphaned relationship edges.
5. **Knowledge Graph Reconciliation**: Synchronizes `FILE_CONTAINS_SYMBOL`, `SYMBOL_IMPORTS_SYMBOL`, `SYMBOL_CALLS_SYMBOL`, `FILE_DEPENDS_ON_FILE`, and `TEST_TARGETS_SYMBOL` across all entities.
6. **Interruption-Safe Atomic Writes**: Database updates execute within a single atomic SQLite transaction with WAL mode. JSON exports are written via temporary `.tmp` files and swapped atomically using `os.replace`.

### Impact Analysis Engine

MSK answers: *"If this file, function, class, module, or dependency changes, what other parts of the project might be affected, and why?"*

The engine traverses verified relationships in the local knowledge graph (call graphs, imports, test-targets, containment, manifest dependencies) without external network calls or cloud dependencies:

1. **File Impact (`msk impact --file <path>`)**:
   - Identifies contained symbols, dependent files importing this file, direct callers of functions in this file, tests targeting this file's symbols, and associated API endpoints or infrastructure.
2. **Symbol Impact (`msk impact --symbol <name> [--file <path>]`)**:
   - Identifies direct callers, member functions / parent classes, dependent files, and tests targeting the specific symbol.
   - Disambiguates duplicate symbol names across different files using `--file`.
3. **Dependency Impact (`msk impact --dependency <package>`)**:
   - Identifies manifest files declaring the dependency and source files directly importing the package.
4. **Direct vs. Transitive Analysis**:
   - **Direct impact**: Items connected through verified depth-1 relationships.
   - **Transitive impact**: Multi-hop callers and dependent files reachable up to `--depth <N>` (default: 3). Use `--direct` for depth 1 only.
   - Cycle prevention and deduplication ensure deterministic results even in recursive or circular codebases.
5. **Machine-Readable JSON Output (`--json`)**:
   - Emits structured JSON suitable for CI pipelines, commit hooks, or developer tooling.

#### CLI Usage Examples

```bash
# Analyze impact of modifying a service file (transitive up to depth 3)
msk impact --file src/payment/service.py

# Analyze direct impact only
msk impact --file src/payment/service.py --direct

# Analyze impact of a specific function
msk impact --symbol process_payment --file src/payment/service.py

# Bounded traversal depth
msk impact --file src/payment/service.py --depth 2

# Output machine-readable JSON
msk impact --file src/payment/service.py --json
```

#### Interpreting Results & Incomplete Knowledge

- **Missing Knowledge**: If a queried file or symbol is not indexed, MSK informs you and suggests running `msk update` (if changes are recent) or `msk init` (if the project has not been initialized).
- **Empty Results**: An item with 0 affected items indicates that it is indexed, but no other project files, callers, or tests depend on it.
- **Evidence-Based Explanations**: Every result item includes the explicit relationship type, traversal depth, and causation path.

---

## Privacy & Security

MSK is designed with privacy as a core architectural concern:

- **Local-only analysis**: All scanning and parsing happens on your machine. No data leaves your system.
- **No raw secrets stored**: The security scanner detects credentials but **never stores the actual secret values**. Only metadata (rule ID, severity, line number, sanitized description) is persisted.
- **Sensitive file detection**: Files matching patterns like `.env`, `*.key`, `*.pem`, `id_rsa` are flagged.
- **No source code copies**: The `.msk/` directory never contains copies of your source code—only extracted metadata.
- **Git-ignored databases**: SQLite databases and caches are git-ignored by default.

---

## Architecture

```text
msk/
├── common/        # Logging, hashing, path handling, errors
├── config/        # Project and policy configuration loaders
├── project/       # Root detection, streaming file scanner, ignore handling
├── analyzer/      # Tree-sitter AST parsing, language adapters, symbols, imports
│   ├── security.py       # Privacy-preserving secret scanner
│   └── infrastructure.py # Infrastructure config detector
├── storage/       # SQLite schema (V2) and transactional repository
├── knowledge/     # Knowledge graph models, builder, JSON serializers
├── graph/         # In-memory and SQL graph relationship queries
├── changes/       # SHA-256 content and structural fingerprinting, Git integration
├── impact/        # Impact analysis engine, graph traversal, deterministic reporting
└── cli/           # Typer CLI commands with Rich terminal UI
```

### Data Flow

```text
Source Files → Project Scanner → File Analyzer → Knowledge Builder → SQLite + JSON Exports
                                      ↓
                              Tree-sitter Parser
                              Symbol Extractor
                              Import Resolver
                              Security Scanner
                              Infrastructure Detector
```

---

## Development

### Prerequisites

- Python ≥ 3.11
- Git

### Setup

```bash
git clone https://github.com/Omkar-git-hub/msk-project-intelligence.git
cd msk-project-intelligence
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Linting

```bash
ruff check src/ tests/
```

### Building

```bash
python -m build
```

Produces `dist/msk-0.1.0a1-py3-none-any.whl` and `dist/msk-0.1.0a1.tar.gz`.

### Security Checks

```bash
bandit -r src/ -c pyproject.toml
pip-audit
```

---

## Alpha Limitations

This is `v0.1.0-alpha.1`. The following limitations apply:

- **No AI/LLM integration yet**: MSK currently builds and maintains the knowledge model locally. AI query commands (`msk ask`, `msk explain`) are planned for future phases.
- **Limited language support**: Python, Java, TypeScript, and JavaScript parsers currently enabled.
- **No remote/cloud features**: Everything runs 100% locally on your machine.
- **Schema evolution**: Non-destructive migrations keep SQLite schema backwards compatible across updates.
- **Windows note**: Tested on Windows 11 with Python 3.14. Tree-sitter AST nodes use safe byte-offset extraction.

---

## Roadmap

- [x] **Phase 1**: Local Project Intelligence & CLI
- [x] **Phase 2**: Hardened Knowledge Model (tests, APIs, infrastructure, security)
- [x] **Phase 3**: Incremental Change Engine & Knowledge Model Consistency
- [x] **Phase 4**: Impact Analysis Engine
- [ ] **Phase 5**: Privacy, Secret & PII Scanning Engine
- [ ] **Phase 6**: Pluggable AI / LLM Gateway (Local & Cloud)
- [ ] **Phase 7**: AI Commands (`msk ask`, `msk explain`, `msk fix`)
- [ ] **Phase 8**: Error & Incident Investigation

---

## License

Apache-2.0
