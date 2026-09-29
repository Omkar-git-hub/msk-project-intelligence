"""Logging configuration for MSK with secret scrubbing."""

import logging
import re
from typing import ClassVar

from rich.logging import RichHandler

_KV_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|bearer|auth)\b(\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-\.]{8,})(['\"]?)"
)

_TOKEN_PATTERNS = [
    re.compile(r"\bghp_[0-9a-zA-Z]{36}\b"),
    re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
]


class SecretScrubbingFilter(logging.Filter):
    """Filter that scrubs detected secrets from log output."""

    REPLACEMENT: ClassVar[str] = "[REDACTED_SECRET]"

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            # Scrub key-value secrets
            msg = _KV_SECRET_PATTERN.sub(rf"\1\2{self.REPLACEMENT}\4", msg)
            # Scrub standalone tokens
            for pattern in _TOKEN_PATTERNS:
                msg = pattern.sub(self.REPLACEMENT, msg)
            record.msg = msg
        return True


def setup_logger(name: str = "msk", verbose: bool = False) -> logging.Logger:
    """Set up and configure the application logger."""
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    if not logger.handlers:
        handler = RichHandler(
            level=level,
            show_time=verbose,
            show_level=verbose,
            show_path=verbose,
            markup=True,
        )
        handler.addFilter(SecretScrubbingFilter())
        logger.addHandler(handler)

    return logger
