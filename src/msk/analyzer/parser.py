"""Tree-sitter parser adapter and cache."""

import logging
from typing import Any
import tree_sitter
import tree_sitter_language_pack as tslp

logger = logging.getLogger("msk.analyzer.parser")

_PARSER_CACHE: dict[str, tree_sitter.Parser] = {}


def get_parser(language: str) -> tree_sitter.Parser | None:
    """Get or instantiate a Tree-sitter parser for a given language."""
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]

    try:
        parser = tslp.get_parser(language)
        _PARSER_CACHE[language] = parser
        return parser
    except Exception as exc:
        logger.debug("Failed to acquire Tree-sitter parser for language '%s': %s", language, exc)
        return None


def parse_bytes(code: bytes, language: str) -> Any | None:
    """Parse byte content using Tree-sitter parser."""
    parser = get_parser(language)
    if not parser:
        return None

    try:
        return parser.parse(code)
    except Exception as exc:
        logger.debug("Tree-sitter parse error for language '%s': %s", language, exc)
        return None
