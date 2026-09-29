"""Language detection and categorization."""

from pathlib import Path

# Mapping from file extension to canonical language identifier
EXTENSION_MAP: dict[str, str] = {
    # Parsable with Tree-sitter in Phase 1
    ".py": "python",
    ".pyi": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    # Structured & Config
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "rst",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    # Additional future languages
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".tf": "terraform",
}

# Special filename mappings
EXACT_FILENAME_MAP: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "docker-compose.yml": "yaml",
    "docker-compose.yaml": "yaml",
    "Makefile": "makefile",
    "Jenkinsfile": "groovy",
    "Vagrantfile": "ruby",
}

PARSABLE_LANGUAGES: set[str] = {
    "python",
    "java",
    "javascript",
    "typescript",
}


def detect_language(path: str | Path) -> str | None:
    """Detect language of a file from its filename or extension."""
    file_path = Path(path)
    name = file_path.name

    if name in EXACT_FILENAME_MAP:
        return EXACT_FILENAME_MAP[name]

    ext = file_path.suffix.lower()
    return EXTENSION_MAP.get(ext)


def is_parsable_language(language: str | None) -> bool:
    """Return True if language has an active AST extractor."""
    return language in PARSABLE_LANGUAGES
