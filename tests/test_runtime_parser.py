"""Tests for MSK Multi-format Runtime Log Parser."""

from msk.runtime.models import RuntimeEventType, RuntimeLogLevel
from msk.runtime.parser import RuntimeLogParser


def test_parse_empty_text():
    parser = RuntimeLogParser(project_id="proj_test")
    assert parser.parse_text("") == []
    assert parser.parse_text("   \n\n  ") == []


def test_parse_standard_text_log():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = "2026-11-03 14:30:00 [ERROR] Database connection lost during query execution"
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.level == RuntimeLogLevel.ERROR.value
    assert "Database connection lost" in ev.message
    assert ev.event_type == RuntimeEventType.LOG.value
    assert ev.fingerprint.startswith("fp_")


def test_parse_json_structured_log():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = (
        '{"timestamp": "2026-11-03T10:00:00Z", "level": "warning", '
        '"message": "Cache miss for entity", "file": "src/cache.py", "line": 42, '
        '"duration_ms": 12.5}'
    )
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.level == RuntimeLogLevel.WARNING.value
    assert ev.message == "Cache miss for entity"
    assert ev.file_path == "src/cache.py"
    assert ev.line_number == 42
    assert ev.attributes.get("duration_ms") == 12.5


def test_parse_python_traceback():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = """
Traceback (most recent call last):
  File "src/payment/engine.py", line 42, in process_transaction
    validate_account(account_id)
  File "src/payment/validator.py", line 15, in validate_account
    raise ValueError("Invalid account identifier")
ValueError: Invalid account identifier
"""
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.level == RuntimeLogLevel.ERROR.value
    assert ev.event_type == RuntimeEventType.EXCEPTION.value
    assert ev.exception_type == "ValueError"
    assert ev.file_path == "src/payment/validator.py"
    assert ev.line_number == 15
    assert ev.symbol_name == "validate_account"
    assert ev.stack_trace is not None
    assert len(ev.stack_trace.frames) == 2
    assert ev.stack_trace.frames[0].file_path == "src/payment/engine.py"
    assert ev.stack_trace.frames[0].function_name == "process_transaction"
    assert ev.stack_trace.frames[1].file_path == "src/payment/validator.py"


def test_parse_java_stack_trace():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = """
java.lang.NullPointerException: Object reference is null
  at com.example.service.OrderService.process(OrderService.java:55)
  at com.example.api.OrderController.handle(OrderController.java:23)
"""
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.exception_type == "java.lang.NullPointerException"
    assert ev.file_path == "OrderService.java"
    assert ev.line_number == 55
    assert ev.symbol_name == "com.example.service.OrderService.process"


def test_parse_nodejs_stack_trace():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = """
TypeError: Cannot read properties of undefined
  at processOrder (src/order.ts:42:15)
  at handleRequest (src/server.ts:18:9)
"""
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.exception_type == "TypeError"
    assert ev.file_path == "src/order.ts"
    assert ev.line_number == 42
    assert ev.symbol_name == "processOrder"


def test_parse_api_request_log():
    parser = RuntimeLogParser(project_id="proj_test")
    raw = '2026-11-03 12:00:00 [INFO] "POST /api/v1/checkout HTTP/1.1" 500 in 240ms'
    events = parser.parse_text(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == RuntimeEventType.API_CALL.value
    assert ev.level == RuntimeLogLevel.ERROR.value
    assert ev.attributes["http_method"] == "POST"
    assert ev.attributes["route"] == "/api/v1/checkout"
    assert ev.attributes["status_code"] == 500
    assert ev.attributes["duration_ms"] == 240.0


def test_error_fingerprinting_deduplication():
    parser = RuntimeLogParser(project_id="proj_test")
    # Two errors with dynamic UUIDs and line timestamps should produce the same fingerprint
    err1 = """
Traceback (most recent call last):
  File "src/worker.py", line 88, in execute_job
    raise TimeoutError("Job 12345678-1234-1234-1234-123456789abc timed out after 30 seconds")
TimeoutError: Job 12345678-1234-1234-1234-123456789abc timed out after 30 seconds
"""
    err2 = """
Traceback (most recent call last):
  File "src/worker.py", line 88, in execute_job
    raise TimeoutError("Job 87654321-4321-4321-4321-cba987654321 timed out after 30 seconds")
TimeoutError: Job 87654321-4321-4321-4321-cba987654321 timed out after 30 seconds
"""
    ev1 = parser.parse_text(err1)[0]
    ev2 = parser.parse_text(err2)[0]
    assert ev1.fingerprint == ev2.fingerprint


def test_parse_malformed_input_safety():
    parser = RuntimeLogParser(project_id="proj_test")
    garbage = ":::???---%%% random garbled binary-like \x00\x01\x02 content\n[INCOMPLETE_BRACKET"
    events = parser.parse_text(garbage)
    assert len(events) >= 1
    for ev in events:
        assert isinstance(ev.message, str)
