"""Tests for MSK Runtime Sanitizer and privacy filtering."""

from pathlib import Path

import pytest

from msk.common.errors import MSKError
from msk.runtime.sanitizer import RuntimeSanitizer


def test_sanitize_text_normal_string():
    sanitizer = RuntimeSanitizer()
    clean = sanitizer.sanitize_text("Payment service processed order 1042 successfully.")
    assert clean == "Payment service processed order 1042 successfully."


def test_sanitize_attributes_sensitive_keys():
    sanitizer = RuntimeSanitizer()
    attrs = {
        "status_code": 200,
        "duration_ms": 45.2,
        "authorization": "test-sample-header-value",
        "api_key": "sample-config-value",
        "cookie": "session-indicator-1234",
    }
    cleaned = sanitizer.sanitize_attributes(attrs)
    assert cleaned["status_code"] == 200
    assert cleaned["duration_ms"] == 45.2
    assert cleaned["authorization"] == "[REDACTED_SECRET]"
    assert cleaned["api_key"] == "[REDACTED_SECRET]"
    assert cleaned["cookie"] == "[REDACTED_SECRET]"


def test_normalize_and_validate_path_relative(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    sanitizer = RuntimeSanitizer(project_root=project_root)

    res = sanitizer.normalize_and_validate_path("src/payment/processor.py")
    assert res == "src/payment/processor.py"


def test_normalize_and_validate_path_absolute_within_root(tmp_path: Path):
    project_root = tmp_path / "project"
    sub_file = project_root / "src" / "service.py"
    sub_file.parent.mkdir(parents=True)
    sub_file.write_text("# code")

    sanitizer = RuntimeSanitizer(project_root=project_root)
    res = sanitizer.normalize_and_validate_path(str(sub_file))
    assert res == "src/service.py"


def test_normalize_and_validate_path_traversal_detection(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    sanitizer = RuntimeSanitizer(project_root=project_root)

    with pytest.raises(MSKError) as exc_info:
        sanitizer.normalize_and_validate_path("../../outside.py")
    assert "Path traversal detected" in exc_info.value.message
