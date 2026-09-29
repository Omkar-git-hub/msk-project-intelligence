# MSK Architecture

## 1. System Vision

MSK is designed as a local-first Project Intelligence, Privacy, and Security boundary. It acts as an intermediary between a codebase (where Git is the ultimate source of truth) and downstream AI models.

```text
                         DEVELOPER
                             │
                             ▼
                        MSK CLI
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
          PROJECT         CHANGE         CONTEXT
          ENGINE          ENGINE         ENGINE
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                  PROJECT KNOWLEDGE MODEL
                             │
             ┌───────────────┼────────────────┐
             │               │                │
             ▼               ▼                ▼
           Graph          History         Security
             │               │                │
             └───────────────┼────────────────┘
                             ▼
                       POLICY ENGINE
                             │
                    ┌────────┴────────┐
                    │                 │
                  ALLOW              BLOCK
                    │                 │
                    ▼                 ▼
                AI GATEWAY        LOCAL/PRIVATE
                    │                 LLM
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       OpenAI   Anthropic   Azure
```

## 2. Core Subsystems

### 2.1 Project Engine
- **Root Detector**: Identifies project root via Git anchors or language project manifests (`pyproject.toml`, `package.json`, `pom.xml`, etc.).
- **Ignore Engine**: Respects `.gitignore`, `.mskignore`, and built-in rules (e.g., skips `.git/`, `.msk/`, `node_modules/`, `target/`, binary files).
- **Scanner**: Memory-safe streaming directory walker with symlink escape prevention.

### 2.2 Analyzer Engine
- **Language Detection**: Robust extension and manifest-based detection.
- **Tree-sitter Parser**: Abstract Syntax Tree parsing for multi-language AST extraction (Python, Java, JavaScript, TypeScript, extensible to C/C++, Go, Rust).
- **Symbol Extractor**: Extracts classes, functions, methods, constructors, interfaces, and visibility.
- **Dependency Analyzer**: Extracts package-level manifests and import trees.

### 2.3 Storage & Knowledge Engine
- **SQLite Database (`.msk/msk.db`)**: Primary storage for project metadata, files, symbols, dependencies, and relationships.
- **Knowledge Graph**: Node-and-edge relationships mapped directly in SQLite tables.
- **JSON Exporter**: Exports human-readable snapshots (`structure.json`, `symbols.json`, `graph.json`) for auditability and interoperability.
- **Zero Redundant Storage**: Source code is never duplicated into `.msk/`.

### 2.4 Privacy & Security Boundary
- Classified metadata tracks potential secrets or PII without storing sensitive values.
- Future AI queries pass through policy verification before context leaves the host.
