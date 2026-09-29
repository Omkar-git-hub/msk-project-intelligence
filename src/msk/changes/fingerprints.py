"""File and structural fingerprinting for change detection."""

from pathlib import Path
from pydantic import BaseModel

from msk.common.hashing import sha256_file


class FileFingerprint(BaseModel):
    """Pair of cryptographic content and structural digests."""

    path: str
    content_hash: str
    structural_hash: str


def generate_file_content_hash(path: str | Path) -> str:
    """Calculate the streaming SHA-256 fingerprint of a file."""
    return sha256_file(path)
