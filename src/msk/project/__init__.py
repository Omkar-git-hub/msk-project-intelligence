"""MSK project subsystem."""

from msk.project.detector import detect_ecosystems, find_project_root
from msk.project.ignore import DEFAULT_IGNORE_PATTERNS, IgnoreEngine
from msk.project.metadata import determine_project_name, extract_project_metadata
from msk.project.scanner import ProjectScanner, ScannedFile, is_binary_file

__all__ = [
    "DEFAULT_IGNORE_PATTERNS",
    "IgnoreEngine",
    "ProjectScanner",
    "ScannedFile",
    "detect_ecosystems",
    "determine_project_name",
    "extract_project_metadata",
    "find_project_root",
    "is_binary_file",
]
