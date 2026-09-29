# MSK Knowledge Model

## 1. Storage Design

The MSK Project Knowledge Model is stored locally in `.msk/msk.db` (SQLite 3).

```text
.msk/
├── msk.db            # Primary transactional knowledge store
├── project.json      # Top-level project descriptor
├── policy.json       # Privacy & access policy
└── exports/
    ├── structure.json # Project tree and file inventory
    ├── symbols.json   # Extracted symbols and locations
    └── graph.json     # Graph edges and dependencies
```

## 2. Core Entities

### Project
- `id` (TEXT PRIMARY KEY)
- `name` (TEXT)
- `root_path` (TEXT)
- `created_at` (TEXT ISO8601)
- `updated_at` (TEXT ISO8601)

### File
- `id` (TEXT PRIMARY KEY)
- `project_id` (TEXT REFERENCES projects(id))
- `path` (TEXT relative to root)
- `language` (TEXT)
- `size_bytes` (INTEGER)
- `content_hash` (TEXT SHA-256)
- `structural_hash` (TEXT SHA-256)
- `modified_at` (TEXT ISO8601)

### Symbol
- `id` (TEXT PRIMARY KEY)
- `file_id` (TEXT REFERENCES files(id))
- `name` (TEXT)
- `type` (TEXT: class, function, method, interface, enum, module)
- `parent_symbol` (TEXT NULL)
- `line_start` (INTEGER)
- `line_end` (INTEGER)
- `visibility` (TEXT: public, private, protected)

### Dependency
- `id` (TEXT PRIMARY KEY)
- `source` (TEXT)
- `target` (TEXT)
- `type` (TEXT: package, module, external)

### Relationship
- `id` (TEXT PRIMARY KEY)
- `source_id` (TEXT)
- `target_id` (TEXT)
- `relationship_type` (TEXT: FILE_CONTAINS_SYMBOL, SYMBOL_IMPORTS_SYMBOL, SYMBOL_CALLS_SYMBOL, FILE_DEPENDS_ON_FILE, TEST_TARGETS_SYMBOL)

## 3. Regenerability Guarantee

The knowledge model is completely regeneratable. If `.msk/` is deleted, running `msk init` recreates the entire schema, database, and export files from the project source code.
