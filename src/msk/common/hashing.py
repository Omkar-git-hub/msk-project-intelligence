"""Cryptographic and structural hashing utilities for MSK."""

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    """Calculate the SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Calculate the SHA-256 hex digest of a string."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 of a file in streaming chunks (memory-safe)."""
    hasher = hashlib.sha256()
    file_path = Path(path)
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_structural_hash(items: list[dict[str, Any]]) -> str:
    """Compute deterministic SHA-256 over a normalized list of structural dictionaries.

    Sorts keys and elements to guarantee idempotency.
    """
    serialized = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return sha256_text(serialized)
