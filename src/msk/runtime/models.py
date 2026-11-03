"""Data models for MSK Local-First Runtime Intelligence."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RuntimeLogLevel(StrEnum):
    """Standardized runtime log severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RuntimeEventType(StrEnum):
    """Classification of runtime observations."""

    LOG = "log"
    EXCEPTION = "exception"
    OBSERVATION = "observation"
    API_CALL = "api_call"
    PERFORMANCE = "performance"


class StackFrame(BaseModel):
    """A single normalized stack trace frame."""

    file_path: str
    line_number: int
    function_name: str
    code_snippet: str | None = None
    is_project_file: bool = False


class StackTrace(BaseModel):
    """Parsed multi-language stack trace representation."""

    exception_type: str
    message: str
    frames: list[StackFrame] = Field(default_factory=list)
    raw_text: str | None = None


class RuntimeEvent(BaseModel):
    """Discrete parsed and sanitized runtime event."""

    id: str
    project_id: str
    timestamp: str
    level: str = RuntimeLogLevel.INFO.value
    source: str | None = None
    message: str
    event_type: str = RuntimeEventType.LOG.value
    file_path: str | None = None
    line_number: int | None = None
    symbol_name: str | None = None
    exception_type: str | None = None
    stack_trace: StackTrace | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str
    created_at: str


class RuntimeErrorGroup(BaseModel):
    """Aggregated representation of recurring runtime errors."""

    id: str
    project_id: str
    fingerprint: str
    exception_type: str
    message_template: str
    first_seen: str
    last_seen: str
    occurrence_count: int = 1
    severity: str = RuntimeLogLevel.ERROR.value
    file_path: str | None = None
    line_number: int | None = None
    symbol_name: str | None = None
    sample_event_id: str | None = None
    resolved: bool = False


class RuntimeObservedFacts(BaseModel):
    """Empirical observations recorded from runtime execution."""

    event_id: str
    timestamp: str
    exception_type: str
    error_message: str
    file_path: str | None = None
    line_number: int | None = None
    symbol_name: str | None = None
    occurrence_count: int = 1
    frames: list[StackFrame] = Field(default_factory=list)
    observed_attributes: dict[str, Any] = Field(default_factory=dict)


class RuntimeInferredImpact(BaseModel):
    """Static knowledge inferences derived from runtime observation."""

    affected_file: str | None = None
    affected_symbol: str | None = None
    callers: list[str] = Field(default_factory=list)
    callees: list[str] = Field(default_factory=list)
    dependent_files: list[str] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)
    related_apis: list[str] = Field(default_factory=list)
    downstream_impact_count: int = 0
    recommended_actions: list[str] = Field(default_factory=list)


class RuntimeCorrelationReport(BaseModel):
    """Comprehensive report connecting a runtime failure to the project knowledge graph."""

    correlation_id: str
    project_id: str
    fingerprint: str
    observed_facts: RuntimeObservedFacts
    inferred_impact: RuntimeInferredImpact
    correlated_at: str


class RuntimeStatusSummary(BaseModel):
    """High-level status summary of project runtime intelligence."""

    project_name: str
    total_events: int
    total_errors: int
    total_unresolved_errors: int
    last_event_timestamp: str | None = None
    top_errors: list[RuntimeErrorGroup] = Field(default_factory=list)
    correlated_files_count: int = 0
    correlated_symbols_count: int = 0
