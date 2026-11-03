"""Multi-format log and runtime observation parser for MSK."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msk.knowledge.models import make_entity_id
from msk.runtime.models import (
    RuntimeEvent,
    RuntimeEventType,
    RuntimeLogLevel,
    StackFrame,
    StackTrace,
)
from msk.runtime.sanitizer import RuntimeSanitizer

# Common regex patterns for timestamp detection
_TIMESTAMP_ISO_PATTERN = re.compile(
    r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?"
)
_TIMESTAMP_SYSLOG_PATTERN = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)

# Common regex patterns for log level detection
_LEVEL_PATTERN = re.compile(
    r"(?:\[|\b)(DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)(?:\]|\b)",
    re.IGNORECASE,
)

# Python stack trace patterns
_PY_TRACEBACK_START = re.compile(r"Traceback \(most recent call last\):")
_PY_FRAME_PATTERN = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)'
)
_PY_EXCEPTION_LINE = re.compile(
    r"^(?P<type>[a-zA-Z_][a-zA-Z0-9_.]*(?:Error|Exception|Warning|Interrupt|Exit|Fault)):?\s*(?P<msg>.*)$"
)

# Java stack trace patterns
_JAVA_FRAME_PATTERN = re.compile(
    r"^\s*at (?P<class>[a-zA-Z0-9_$.]+)\.(?P<method>[a-zA-Z0-9_$<>]+)\((?P<file>[a-zA-Z0-9_$.]+):(?P<line>\d+)\)"
)
_JAVA_EXCEPTION_START = re.compile(
    r"^(?:Exception in thread \S+ )?(?P<type>[a-zA-Z0-9_$.]+(?:Exception|Error))(?::\s*(?P<msg>.*))?$"
)

# Node.js / JavaScript stack trace patterns
_JS_FRAME_PATTERN = re.compile(
    r"^\s*at (?:(?P<func>[a-zA-Z0-9_$<>.]+)\s+\()?(?P<file>(?:[a-zA-Z]:[\\/]|/|[.\w-]+[\\/])[^:)]+):(?P<line>\d+)(?::(?P<col>\d+))?\)?$"
)
_JS_EXCEPTION_START = re.compile(
    r"^(?P<type>[a-zA-Z0-9_$]*(?:Error|Exception)):?\s*(?P<msg>.*)$"
)

# API log pattern: e.g. "POST /api/v1/orders 500 in 142ms" or "GET /users HTTP/1.1 200"
_API_LOG_PATTERN = re.compile(
    r'\b(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?P<route>/[^\s?"]*)(?:\?[^\s"]*)?(?:\s+HTTP/[\d.]+)?["\']?\s+(?P<status>\d{3})(?:\s+(?:in\s+)?(?P<duration>\d+(?:\.\d+)?)\s*(?P<unit>ms|s))?',
    re.IGNORECASE,
)

# Normalization patterns for message templating and deduplication
_NORM_UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_NORM_HEX_PATTERN = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NORM_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_NORM_NUM_PATTERN = re.compile(r"\b\d{4,}\b")


class RuntimeLogParser:
    """Robust, multi-format parser for application logs, exceptions, and metrics."""

    def __init__(self, project_id: str, project_root: Path | None = None) -> None:
        self.project_id = project_id
        self.project_root = Path(project_root).resolve() if project_root else None
        self.sanitizer = RuntimeSanitizer(project_root=self.project_root)

    def parse_text(self, text: str, source: str | None = None) -> list[RuntimeEvent]:
        """Parse raw log text, handling single-line and multi-line structures safely."""
        if not text or not text.strip():
            return []

        lines = text.splitlines()
        events: list[RuntimeEvent] = []
        i = 0
        total_lines = len(lines)

        while i < total_lines:
            line = lines[i]
            trimmed = line.strip()

            if not trimmed:
                i += 1
                continue

            # Check for JSON line format first
            if trimmed.startswith("{") and trimmed.endswith("}"):
                try:
                    event = self._parse_json_log(trimmed, source=source)
                    if event:
                        events.append(event)
                        i += 1
                        continue
                except Exception:
                    # Fallback to standard line parsing if JSON load fails
                    pass

            # Check for multi-line Python Traceback
            if _PY_TRACEBACK_START.search(line):
                tb_lines = [line]
                i += 1
                while i < total_lines:
                    next_line = lines[i]
                    if next_line.startswith(" ") or next_line.startswith("\t"):
                        tb_lines.append(next_line)
                        i += 1
                    elif _PY_EXCEPTION_LINE.match(next_line.strip()):
                        tb_lines.append(next_line)
                        i += 1
                        break
                    else:
                        break
                event = self._parse_python_traceback(tb_lines, source=source)
                if event:
                    events.append(event)
                continue

            # Check for multi-line Java or Node stack traces
            java_match = _JAVA_EXCEPTION_START.match(trimmed)
            js_match = _JS_EXCEPTION_START.match(trimmed)
            if (java_match or js_match) and i + 1 < total_lines and (
                _JAVA_FRAME_PATTERN.match(lines[i + 1]) or _JS_FRAME_PATTERN.match(lines[i + 1])
            ):
                trace_lines = [line]
                i += 1
                while i < total_lines:
                    next_line = lines[i]
                    if (
                        _JAVA_FRAME_PATTERN.match(next_line)
                        or _JS_FRAME_PATTERN.match(next_line)
                        or next_line.strip().startswith("... ")
                        or next_line.strip().startswith("Caused by:")
                    ):
                        trace_lines.append(next_line)
                        i += 1
                    else:
                        break
                event = self._parse_generic_traceback(trace_lines, source=source)
                if event:
                    events.append(event)
                continue

            # Standard single-line log
            event = self._parse_standard_log_line(line, source=source)
            if event:
                events.append(event)
            i += 1

        return events

    def _parse_json_log(self, line: str, source: str | None = None) -> RuntimeEvent | None:
        """Parse structured JSON log entry."""
        data = json.loads(line)
        if not isinstance(data, dict):
            return None

        # Extract timestamp
        ts_val = (
            data.get("timestamp")
            or data.get("time")
            or data.get("datetime")
            or data.get("@timestamp")
            or data.get("ts")
        )
        timestamp = self._normalize_timestamp(str(ts_val)) if ts_val else self._current_timestamp()

        # Extract level
        raw_level = str(
            data.get("level")
            or data.get("severity")
            or data.get("log_level")
            or data.get("lvl")
            or "INFO"
        ).upper()
        level = self._normalize_level(raw_level)

        # Extract message
        raw_msg = str(
            data.get("message")
            or data.get("msg")
            or data.get("error")
            or data.get("description")
            or ""
        )
        message = self.sanitizer.sanitize_text(raw_msg)

        # Extract source
        log_source = str(data.get("source") or data.get("logger") or data.get("component") or source or "") or None

        # Extract file and symbol
        file_path = self.sanitizer.normalize_and_validate_path(
            data.get("file") or data.get("file_path") or data.get("filename")
        )
        line_num = data.get("line") or data.get("line_number")
        line_number = int(line_num) if line_num and str(line_num).isdigit() else None
        symbol_name = data.get("function") or data.get("func") or data.get("method") or data.get("symbol")

        # Extract exception / stack trace if embedded
        exception_type = data.get("exception_type") or data.get("error_type")
        raw_trace = data.get("stack") or data.get("stack_trace") or data.get("traceback")
        stack_trace = None
        if raw_trace and isinstance(raw_trace, str):
            parsed_events = self.parse_text(raw_trace, source=log_source)
            if parsed_events and parsed_events[0].stack_trace:
                stack_trace = parsed_events[0].stack_trace
                if not exception_type:
                    exception_type = stack_trace.exception_type
                if not file_path and stack_trace.frames:
                    file_path = stack_trace.frames[-1].file_path
                    line_number = stack_trace.frames[-1].line_number
                    symbol_name = stack_trace.frames[-1].function_name

        # Attributes
        attrs: dict[str, Any] = {}
        for k, v in data.items():
            if k not in (
                "timestamp", "time", "datetime", "@timestamp", "ts",
                "level", "severity", "log_level", "lvl",
                "message", "msg", "error", "description",
                "source", "logger", "component",
                "file", "file_path", "filename", "line", "line_number",
                "function", "func", "method", "symbol",
                "stack", "stack_trace", "traceback", "exception_type", "error_type",
            ):
                attrs[k] = v
        attrs = self.sanitizer.sanitize_attributes(attrs)

        # Determine event type
        event_type = RuntimeEventType.LOG.value
        if stack_trace or exception_type or level in (RuntimeLogLevel.ERROR.value, RuntimeLogLevel.CRITICAL.value):
            event_type = RuntimeEventType.EXCEPTION.value
        elif "status_code" in attrs or "status" in attrs or "method" in attrs:
            event_type = RuntimeEventType.API_CALL.value
        elif "duration_ms" in attrs or "latency_ms" in attrs:
            event_type = RuntimeEventType.PERFORMANCE.value

        fp = self._generate_fingerprint(
            exception_type=exception_type,
            file_path=file_path,
            line_number=line_number,
            symbol_name=symbol_name,
            message=message,
            level=level,
        )

        return RuntimeEvent(
            id=make_entity_id("rte", self.project_id, timestamp, fp),
            project_id=self.project_id,
            timestamp=timestamp,
            level=level,
            source=log_source,
            message=message,
            event_type=event_type,
            file_path=file_path,
            line_number=line_number,
            symbol_name=str(symbol_name) if symbol_name else None,
            exception_type=str(exception_type) if exception_type else None,
            stack_trace=stack_trace,
            attributes=attrs,
            fingerprint=fp,
            created_at=self._current_timestamp(),
        )

    def _parse_python_traceback(self, lines: list[str], source: str | None = None) -> RuntimeEvent | None:
        """Parse multi-line Python Traceback block."""
        frames: list[StackFrame] = []
        exception_type = "Exception"
        exception_msg = ""
        raw_text = "\n".join(lines)

        j = 0
        while j < len(lines):
            line = lines[j]
            frame_match = _PY_FRAME_PATTERN.search(line)
            if frame_match:
                raw_file = frame_match.group("file")
                line_no = int(frame_match.group("line"))
                func_name = frame_match.group("func")
                code_snippet = None
                if j + 1 < len(lines) and lines[j + 1].startswith("    "):
                    code_snippet = lines[j + 1].strip()
                    j += 1

                norm_file = self.sanitizer.normalize_and_validate_path(raw_file)
                is_proj = self._is_project_file(norm_file)
                frames.append(
                    StackFrame(
                        file_path=norm_file or raw_file,
                        line_number=line_no,
                        function_name=func_name,
                        code_snippet=self.sanitizer.sanitize_text(code_snippet),
                        is_project_file=is_proj,
                    )
                )
            else:
                exc_match = _PY_EXCEPTION_LINE.match(line.strip())
                if exc_match:
                    exception_type = exc_match.group("type")
                    exception_msg = exc_match.group("msg").strip()
            j += 1

        sanitized_msg = self.sanitizer.sanitize_text(exception_msg)
        stack_trace = StackTrace(
            exception_type=exception_type,
            message=sanitized_msg,
            frames=frames,
            raw_text=self.sanitizer.sanitize_text(raw_text),
        )

        # Primary failure locus is the topmost project frame, or the last frame
        top_frame = next((f for f in reversed(frames) if f.is_project_file), None) or (
            frames[-1] if frames else None
        )
        file_path = top_frame.file_path if top_frame else None
        line_number = top_frame.line_number if top_frame else None
        symbol_name = top_frame.function_name if top_frame else None

        full_message = f"{exception_type}: {sanitized_msg}" if sanitized_msg else exception_type
        fp = self._generate_fingerprint(
            exception_type=exception_type,
            file_path=file_path,
            line_number=line_number,
            symbol_name=symbol_name,
            message=sanitized_msg,
            level=RuntimeLogLevel.ERROR.value,
        )

        timestamp = self._current_timestamp()
        return RuntimeEvent(
            id=make_entity_id("rte", self.project_id, timestamp, fp),
            project_id=self.project_id,
            timestamp=timestamp,
            level=RuntimeLogLevel.ERROR.value,
            source=source,
            message=full_message,
            event_type=RuntimeEventType.EXCEPTION.value,
            file_path=file_path,
            line_number=line_number,
            symbol_name=symbol_name,
            exception_type=exception_type,
            stack_trace=stack_trace,
            attributes={},
            fingerprint=fp,
            created_at=timestamp,
        )

    def _parse_generic_traceback(self, lines: list[str], source: str | None = None) -> RuntimeEvent | None:
        """Parse multi-line Java or JavaScript/Node stack trace."""
        header = lines[0].strip()
        frames: list[StackFrame] = []
        exception_type = "Error"
        exception_msg = ""

        # Check Java header
        j_match = _JAVA_EXCEPTION_START.match(header)
        js_match = _JS_EXCEPTION_START.match(header)

        if j_match:
            exception_type = j_match.group("type")
            exception_msg = j_match.group("msg") or ""
        elif js_match:
            exception_type = js_match.group("type")
            exception_msg = js_match.group("msg") or ""
        else:
            parts = header.split(":", 1)
            exception_type = parts[0].strip()
            exception_msg = parts[1].strip() if len(parts) > 1 else ""

        for line in lines[1:]:
            # Try Java frame
            j_frame = _JAVA_FRAME_PATTERN.match(line)
            if j_frame:
                file_name = j_frame.group("file")
                line_no = int(j_frame.group("line"))
                method_name = f"{j_frame.group('class')}.{j_frame.group('method')}"
                norm_file = self.sanitizer.normalize_and_validate_path(file_name)
                is_proj = self._is_project_file(norm_file)
                frames.append(
                    StackFrame(
                        file_path=norm_file or file_name,
                        line_number=line_no,
                        function_name=method_name,
                        is_project_file=is_proj,
                    )
                )
                continue

            # Try JS frame
            js_frame = _JS_FRAME_PATTERN.match(line)
            if js_frame:
                file_str = js_frame.group("file")
                line_no = int(js_frame.group("line"))
                func_name = js_frame.group("func") or "<anonymous>"
                norm_file = self.sanitizer.normalize_and_validate_path(file_str)
                is_proj = self._is_project_file(norm_file)
                frames.append(
                    StackFrame(
                        file_path=norm_file or file_str,
                        line_number=line_no,
                        function_name=func_name,
                        is_project_file=is_proj,
                    )
                )

        sanitized_msg = self.sanitizer.sanitize_text(exception_msg)
        stack_trace = StackTrace(
            exception_type=exception_type,
            message=sanitized_msg,
            frames=frames,
            raw_text=self.sanitizer.sanitize_text("\n".join(lines)),
        )

        top_frame = next((f for f in frames if f.is_project_file), None) or (
            frames[0] if frames else None
        )
        file_path = top_frame.file_path if top_frame else None
        line_number = top_frame.line_number if top_frame else None
        symbol_name = top_frame.function_name if top_frame else None

        full_message = f"{exception_type}: {sanitized_msg}" if sanitized_msg else exception_type
        fp = self._generate_fingerprint(
            exception_type=exception_type,
            file_path=file_path,
            line_number=line_number,
            symbol_name=symbol_name,
            message=sanitized_msg,
            level=RuntimeLogLevel.ERROR.value,
        )

        timestamp = self._current_timestamp()
        return RuntimeEvent(
            id=make_entity_id("rte", self.project_id, timestamp, fp),
            project_id=self.project_id,
            timestamp=timestamp,
            level=RuntimeLogLevel.ERROR.value,
            source=source,
            message=full_message,
            event_type=RuntimeEventType.EXCEPTION.value,
            file_path=file_path,
            line_number=line_number,
            symbol_name=symbol_name,
            exception_type=exception_type,
            stack_trace=stack_trace,
            attributes={},
            fingerprint=fp,
            created_at=timestamp,
        )

    def _parse_standard_log_line(self, line: str, source: str | None = None) -> RuntimeEvent | None:
        """Parse standard text log line with timestamp and severity."""
        raw_line = line.strip()
        if not raw_line:
            return None

        # Extract timestamp
        timestamp = self._current_timestamp()
        rest = raw_line

        iso_match = _TIMESTAMP_ISO_PATTERN.search(raw_line)
        if iso_match:
            timestamp = self._normalize_timestamp(iso_match.group(1))
            rest = raw_line[iso_match.end():].strip()
        else:
            syslog_match = _TIMESTAMP_SYSLOG_PATTERN.search(raw_line)
            if syslog_match:
                timestamp = self._normalize_timestamp(syslog_match.group(1))
                rest = raw_line[syslog_match.end():].strip()

        # Extract severity level
        level = RuntimeLogLevel.INFO.value
        lvl_match = _LEVEL_PATTERN.search(rest)
        if lvl_match:
            level = self._normalize_level(lvl_match.group(1))
            # Remove level prefix from message
            start, end = lvl_match.span()
            rest = (rest[:start] + rest[end:]).strip().lstrip(":- ")

        # Check API log patterns
        attrs: dict[str, Any] = {}
        event_type = RuntimeEventType.LOG.value
        api_match = _API_LOG_PATTERN.search(rest)
        if api_match:
            event_type = RuntimeEventType.API_CALL.value
            method = api_match.group("method").upper()
            route = api_match.group("route")
            status = int(api_match.group("status"))
            attrs["http_method"] = method
            attrs["route"] = route
            attrs["status_code"] = status
            if api_match.group("duration"):
                dur = float(api_match.group("duration"))
                unit = api_match.group("unit") or "ms"
                attrs["duration_ms"] = dur * 1000.0 if unit == "s" else dur
            if status >= 400 and level == RuntimeLogLevel.INFO.value:
                level = RuntimeLogLevel.ERROR.value if status >= 500 else RuntimeLogLevel.WARNING.value

        # Check performance log hints
        if "duration" in rest.lower() or "latency" in rest.lower() or "slow query" in rest.lower():
            if event_type == RuntimeEventType.LOG.value:
                event_type = RuntimeEventType.PERFORMANCE.value

        message = self.sanitizer.sanitize_text(rest)
        attrs = self.sanitizer.sanitize_attributes(attrs)

        fp = self._generate_fingerprint(
            exception_type=None,
            file_path=None,
            line_number=None,
            symbol_name=None,
            message=message,
            level=level,
        )

        return RuntimeEvent(
            id=make_entity_id("rte", self.project_id, timestamp, fp),
            project_id=self.project_id,
            timestamp=timestamp,
            level=level,
            source=source,
            message=message,
            event_type=event_type,
            attributes=attrs,
            fingerprint=fp,
            created_at=self._current_timestamp(),
        )

    def _generate_fingerprint(
        self,
        exception_type: str | None,
        file_path: str | None,
        line_number: int | None,
        symbol_name: str | None,
        message: str,
        level: str,
    ) -> str:
        """Calculate deterministic fingerprint for grouping and deduplication."""
        # Normalize dynamic tokens in message
        norm_msg = message
        norm_msg = _NORM_UUID_PATTERN.sub("<UUID>", norm_msg)
        norm_msg = _NORM_HEX_PATTERN.sub("<HEX>", norm_msg)
        norm_msg = _NORM_IP_PATTERN.sub("<IP>", norm_msg)
        norm_msg = _NORM_NUM_PATTERN.sub("<NUM>", norm_msg)

        key_components = [
            exception_type or level,
            file_path or "",
            str(line_number or 0),
            symbol_name or "",
            norm_msg.strip()[:160],
        ]
        digest = hashlib.sha256(":".join(key_components).encode("utf-8")).hexdigest()[:16]
        return f"fp_{digest}"

    def _normalize_level(self, raw_level: str) -> str:
        """Map raw log severity string to canonical RuntimeLogLevel."""
        upper = raw_level.strip().upper()
        if upper in ("WARN", "WARNING"):
            return RuntimeLogLevel.WARNING.value
        if upper in ("ERR", "ERROR"):
            return RuntimeLogLevel.ERROR.value
        if upper in ("FATAL", "CRITICAL", "PANIC"):
            return RuntimeLogLevel.CRITICAL.value
        if upper == "DEBUG":
            return RuntimeLogLevel.DEBUG.value
        return RuntimeLogLevel.INFO.value

    def _normalize_timestamp(self, ts_str: str) -> str:
        """Convert various timestamp strings into ISO 8601 UTC format."""
        clean = ts_str.strip().strip("[]")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%b %d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(clean, fmt)
                if dt.year == 1900:  # syslog without year
                    dt = dt.replace(year=datetime.now(UTC).year)
                return dt.replace(tzinfo=UTC).isoformat()
            except ValueError:
                continue
        return self._current_timestamp()

    def _current_timestamp(self) -> str:
        """Return current time in ISO 8601 UTC format."""
        return datetime.now(UTC).isoformat()

    def _is_project_file(self, file_path: str | None) -> bool:
        """Determine if a stack frame file path belongs to the active project."""
        if not file_path:
            return False
        norm = file_path.replace("\\", "/")
        # Third-party / virtualenv exclusions
        if (
            "site-packages" in norm
            or "dist-packages" in norm
            or "/node_modules/" in norm
            or "lib/python" in norm
            or norm.startswith("/usr/")
            or norm.startswith("/opt/")
        ):
            return False
        if self.project_root:
            p = Path(file_path)
            if p.is_absolute():
                try:
                    p.resolve().relative_to(self.project_root)
                    return True
                except ValueError:
                    return False
        return not norm.startswith("/") and not (len(norm) > 2 and norm[1] == ":")
