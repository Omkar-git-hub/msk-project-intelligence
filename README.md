# MSK

> **The project stays private. AI gets only what it needs.**

MSK is a **local-first Project Intelligence, Privacy, and Security layer for software engineering**.

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

## Quick Start

### 1. Installation

```bash
# Clone the repository
git clone <repo-url>
cd MSK

# Install using uv or pip
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -e ".[dev]"
```

### 2. Basic Commands

```bash
# Check environment and system readiness
msk doctor

# Initialize local project analysis
msk init

# View project intelligence summary
msk status

# Incrementally update when files change
msk update
```

---

## Architecture Overview

```text
msk/
├── common/        # Logging, hashing, path handling, errors
├── config/        # Project and policy configuration loaders
├── project/       # Root detection, streaming file scanner, ignore handling
├── analyzer/      # Tree-sitter AST parsing, language adapters, symbols, imports
├── storage/       # SQLite schema and transactional repository
├── knowledge/     # Knowledge graph models, builder, JSON serializers
├── graph/         # In-memory and SQL graph relationship queries
├── changes/       # SHA-256 content and structural fingerprinting, Git integration
└── cli/           # Typer CLI commands with Rich terminal UI
```

---

## Roadmap

- [x] **Phase 1**: Local Project Intelligence & CLI (`msk init`, `msk status`, `msk doctor`, Tree-sitter parser, SQLite repository)
- [ ] **Phase 2**: Extended Knowledge Model & Exports
- [ ] **Phase 3**: Incremental Change Tracking & Diffing
- [ ] **Phase 4**: Explainable Minimum-Context Engine
- [ ] **Phase 5**: Privacy, Secret & PII Scanning Engine
- [ ] **Phase 6**: Pluggable AI / LLM Gateway (Local & Cloud)
- [ ] **Phase 7**: AI Commands (`msk ask`, `msk explain`, `msk fix`)
- [ ] **Phase 8**: Error & Incident Investigation

---

## License

Apache-2.0
