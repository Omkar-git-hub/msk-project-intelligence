"""Parser for stack traces and error messages."""

import re
from typing import NamedTuple


class ErrorLocation(NamedTuple):
    """File, line number, and function/method symbol in an error."""

    file_path: str
    line_number: int | None
    symbol_name: str | None


class ParsedErrorContext(NamedTuple):
    """Structured information extracted from an error string or stack trace."""

    error_type: str | None
    error_message: str | None
    locations: list[ErrorLocation]
    extracted_files: list[str]
    extracted_symbols: list[str]


# Python: File "path/to/file.py", line 42, in function_name
_PY_FRAME_REGEX = re.compile(
    r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)(?:,\s+in\s+([a-zA-Z0-9_<>\.]+))?'
)

# Java: at com.example.MyClass.methodName(MyClass.java:123)
_JAVA_FRAME_REGEX = re.compile(
    r'at\s+([a-zA-Z0-9_\.]+)\.([a-zA-Z0-9_<>$]+)\s*\(([^:]+):(\d+)\)'
)

# JavaScript / TypeScript: at functionName (path/to/file.ts:12:34) or at path/to/file.js:12:34
_JS_FRAME_REGEX = re.compile(
    r'at\s+(?:([a-zA-Z0-9_$.<>\s]+)\s+\()?([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+):(\d+)(?::\d+)?\)?'
)

# Exception line: ErrorType: error description
_EXCEPTION_REGEX = re.compile(
    r'(?:^|\s)([A-Z][a-zA-Z0-9_]*(?:Error|Exception|Fault|Failure|Crash)):\s*(.+)$'
)
_FALLBACK_EXCEPTION_REGEX = re.compile(
    r'\b([A-Z][a-zA-Z0-9_]*(?:Error|Exception|Fault|Failure|Crash))\b'
)


def parse_error_text(error_text: str | None) -> ParsedErrorContext:
    """Parse stack traces and error messages to identify files, symbols, and error kinds."""
    if not error_text or not error_text.strip():
        return ParsedErrorContext(
            error_type=None,
            error_message=None,
            locations=[],
            extracted_files=[],
            extracted_symbols=[],
        )

    locations: list[ErrorLocation] = []
    extracted_files: set[str] = set()
    extracted_symbols: set[str] = set()
    error_type: str | None = None
    error_message: str | None = None

    for line in error_text.splitlines():
        trimmed = line.strip()

        # 1. Exception class and message
        exc_match = _EXCEPTION_REGEX.search(trimmed)
        if exc_match:
            error_type = exc_match.group(1)
            error_message = exc_match.group(2).strip()
        elif not error_type:
            fallback = _FALLBACK_EXCEPTION_REGEX.search(trimmed)
            if fallback:
                error_type = fallback.group(1)

        # 2. Python stack frames
        py_match = _PY_FRAME_REGEX.search(trimmed)
        if py_match:
            raw_path = py_match.group(1)
            line_no = int(py_match.group(2))
            sym = py_match.group(3)
            # Filter out non-code labels like <module>, <string>
            clean_sym = sym if (sym and not sym.startswith("<")) else None
            norm_path = raw_path.replace("\\", "/")
            locations.append(ErrorLocation(norm_path, line_no, clean_sym))
            extracted_files.add(norm_path)
            if clean_sym:
                extracted_symbols.add(clean_sym)
            continue

        # 3. Java stack frames
        java_match = _JAVA_FRAME_REGEX.search(trimmed)
        if java_match:
            cls_name = java_match.group(1)
            method_name = java_match.group(2)
            java_file = java_match.group(3)
            line_no = int(java_match.group(4))
            locations.append(ErrorLocation(java_file, line_no, method_name))
            extracted_files.add(java_file)
            if method_name and method_name != "<init>":
                extracted_symbols.add(method_name)
            # Also add simple class name as symbol
            simple_cls = cls_name.split(".")[-1]
            if simple_cls:
                extracted_symbols.add(simple_cls)
            continue

        # 4. JS/TS stack frames
        js_match = _JS_FRAME_REGEX.search(trimmed)
        if js_match:
            fn_name = js_match.group(1).strip() if js_match.group(1) else None
            js_path = js_match.group(2).replace("\\", "/")
            line_no = int(js_match.group(3))
            locations.append(ErrorLocation(js_path, line_no, fn_name))
            extracted_files.add(js_path)
            if fn_name:
                extracted_symbols.add(fn_name)
            continue

    # Extract bare file path mentions (e.g., path/to/file.py) if not caught by stack frame patterns
    bare_path_regex = re.compile(r'\b([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|ts|js|go|rs|json|yaml|yml|toml))\b')
    for match in bare_path_regex.finditer(error_text):
        candidate_path = match.group(1).replace("\\", "/")
        # Avoid matching URL domains or packages like requirements.txt unless plausible
        if "/" in candidate_path or "\\" in candidate_path or candidate_path.endswith((".py", ".java", ".ts", ".js")):
            extracted_files.add(candidate_path)

    # Extract words that look like identifiers in error message (e.g. name 'process_payment' is not defined)
    quote_ident_regex = re.compile(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]")
    for match in quote_ident_regex.finditer(error_text):
        extracted_symbols.add(match.group(1))

    return ParsedErrorContext(
        error_type=error_type,
        error_message=error_message,
        locations=locations,
        extracted_files=sorted(extracted_files),
        extracted_symbols=sorted(extracted_symbols),
    )
