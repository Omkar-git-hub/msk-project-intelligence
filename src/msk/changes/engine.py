"""Change engine for incremental file detection and classification."""

from dataclasses import dataclass, field

from msk.common.hashing import sha256_bytes
from msk.knowledge.models import FileEntity
from msk.project.scanner import ScannedFile


@dataclass
class FileChangeSet:
    """Classified changes between project filesystem and knowledge database."""

    added: list[ScannedFile] = field(default_factory=list)
    modified: list[ScannedFile] = field(default_factory=list)
    deleted: list[FileEntity] = field(default_factory=list)
    renamed: list[tuple[FileEntity, ScannedFile]] = field(default_factory=list)
    unchanged: list[tuple[FileEntity, ScannedFile]] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        """Total count of mutating file changes."""
        return len(self.added) + len(self.modified) + len(self.deleted) + len(self.renamed)


def detect_changes(
    scanned_files: list[ScannedFile],
    recorded_files: list[FileEntity],
) -> FileChangeSet:
    """Compare scanned filesystem inventory against recorded database file entities.

    Uses SHA-256 cryptographic content hashes to verify file identity,
    avoiding reliance on volatile filesystem modification timestamps alone.
    """
    scanned_by_path = {sf.relative_path: sf for sf in scanned_files}
    recorded_by_path = {rf.path: rf for rf in recorded_files}

    scanned_paths = set(scanned_by_path.keys())
    recorded_paths = set(recorded_by_path.keys())

    common_paths = scanned_paths & recorded_paths
    potential_added_paths = scanned_paths - recorded_paths
    potential_deleted_paths = recorded_paths - scanned_paths

    modified: list[ScannedFile] = []
    unchanged: list[tuple[FileEntity, ScannedFile]] = []

    # 1. Check common paths via content hash
    for path in sorted(common_paths):
        sf = scanned_by_path[path]
        rf = recorded_by_path[path]
        try:
            current_bytes = sf.absolute_path.read_bytes()
            current_hash = sha256_bytes(current_bytes)
        except OSError:
            current_hash = ""

        if current_hash and current_hash == rf.content_hash:
            unchanged.append((rf, sf))
        else:
            modified.append(sf)

    # 2. Check for renamed files by matching content hashes of added and deleted files
    deleted_by_hash: dict[str, list[FileEntity]] = {}
    for d_path in potential_deleted_paths:
        rf = recorded_by_path[d_path]
        deleted_by_hash.setdefault(rf.content_hash, []).append(rf)

    renamed: list[tuple[FileEntity, ScannedFile]] = []
    added: list[ScannedFile] = []
    claimed_deleted_paths: set[str] = set()

    for a_path in sorted(potential_added_paths):
        sf = scanned_by_path[a_path]
        try:
            current_bytes = sf.absolute_path.read_bytes()
            current_hash = sha256_bytes(current_bytes)
        except OSError:
            current_hash = ""

        candidates = deleted_by_hash.get(current_hash, [])
        # Only claim rename if exactly one candidate matches and hasn't been claimed yet
        unclaimed = [c for c in candidates if c.path not in claimed_deleted_paths]
        if len(unclaimed) == 1:
            matching_rf = unclaimed[0]
            renamed.append((matching_rf, sf))
            claimed_deleted_paths.add(matching_rf.path)
        else:
            added.append(sf)

    deleted: list[FileEntity] = [
        recorded_by_path[d_path]
        for d_path in sorted(potential_deleted_paths)
        if d_path not in claimed_deleted_paths
    ]

    return FileChangeSet(
        added=added,
        modified=modified,
        deleted=deleted,
        renamed=renamed,
        unchanged=unchanged,
    )
