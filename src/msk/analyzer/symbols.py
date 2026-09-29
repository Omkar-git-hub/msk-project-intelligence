"""AST symbol extraction for Python, Java, JavaScript, and TypeScript."""

import re

import tree_sitter
from pydantic import BaseModel, Field


class ExtractedSymbol(BaseModel):
    """Represents a symbol discovered in source code."""

    name: str
    type: str  # class, interface, enum, function, method, constructor
    line_start: int
    line_end: int
    parent_symbol: str | None = None
    visibility: str = "public"  # public, private, protected
    parameters: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    is_test: bool = False
    is_api_endpoint: bool = False
    api_route: str | None = None
    api_method: str | None = None


def _node_text(node: tree_sitter.Node, code: bytes = b"") -> str:
    """Safely extract decoded text from a node using byte offsets or node.text fallback."""
    if code:
        return code[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
    try:
        if node.text:
            return node.text.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""


def _node_lines(node: tree_sitter.Node, code: bytes = b"") -> tuple[int, int]:
    """Calculate 1-based (line_start, line_end) safely using byte offsets.

    Avoids tree_sitter.Node.start_point / end_point which triggers a Windows
    fatal access violation in cyclic GC on Python 3.14 + tree-sitter 0.26.
    """
    if not code:
        return (1, 1)
    line_start = code[: node.start_byte].count(b"\n") + 1
    line_end = code[: node.end_byte].count(b"\n") + 1
    return (line_start, line_end)


def _extract_calls_from_node(body_node: tree_sitter.Node | None, code: bytes) -> list[str]:
    """Extract called function/method names from an AST node body safely.

    Uses an iterative traversal to prevent recursion depth issues and immediately
    extracts pure Python strings without holding references to C node memory.
    """
    if not body_node or not code:
        return []

    calls: list[str] = []
    seen: set[str] = set()
    stack = [body_node]

    while stack:
        curr = stack.pop()
        ntype = curr.type

        # Python call: (call function: ...)
        if ntype == "call":
            func = curr.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    name = _node_text(func, code)
                    if name and name not in seen:
                        seen.add(name)
                        calls.append(name)
                elif func.type == "attribute":
                    attr = func.child_by_field_name("attribute")
                    if attr:
                        name = _node_text(attr, code)
                        if name and name not in seen:
                            seen.add(name)
                            calls.append(name)
                    attr = None
            func = None

        # Java method invocation or constructor
        elif ntype == "method_invocation":
            mname = curr.child_by_field_name("name")
            if mname:
                name = _node_text(mname, code)
                if name and name not in seen:
                    seen.add(name)
                    calls.append(name)
            mname = None
        elif ntype == "object_creation_expression":
            tname = curr.child_by_field_name("type")
            if tname:
                name = _node_text(tname, code)
                if name and name not in seen:
                    seen.add(name)
                    calls.append(name)
            tname = None

        # JS/TS call expression or new
        elif ntype == "call_expression":
            func = curr.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    name = _node_text(func, code)
                    if name and name not in seen:
                        seen.add(name)
                        calls.append(name)
                elif func.type == "member_expression":
                    prop = func.child_by_field_name("property")
                    if prop:
                        name = _node_text(prop, code)
                        if name and name not in seen:
                            seen.add(name)
                            calls.append(name)
                    prop = None
            func = None
        elif ntype == "new_expression":
            ctor = curr.child_by_field_name("constructor")
            if ctor:
                name = _node_text(ctor, code)
                if name and name not in seen:
                    seen.add(name)
                    calls.append(name)
            ctor = None

        for child in curr.children:
            stack.append(child)
        curr = None

    return calls


def _parse_api_decorator(dec_text: str) -> tuple[bool, str | None, str | None]:
    """Parse HTTP route and method from decorator text.

    E.g., @app.get('/orders') -> (True, '/orders', 'GET')
          @router.post('/checkout') -> (True, '/checkout', 'POST')
    """
    methods = ["get", "post", "put", "delete", "patch", "options", "head"]
    pattern = rf"\.(?:{'|'.join(methods)})\s*\(\s*['\"]([^'\"]+)['\"]"
    match = re.search(pattern, dec_text, re.IGNORECASE)
    if match:
        route = match.group(1)
        # extract method name from decorator call
        for m in methods:
            if f".{m}(" in dec_text.lower():
                return (True, route, m.upper())
        return (True, route, "GET")
    return (False, None, None)


def extract_symbols(tree: tree_sitter.Tree, language: str, code: bytes = b"") -> list[ExtractedSymbol]:
    """Extract symbols from an AST tree based on language grammar."""
    if language == "python":
        return _extract_python_symbols(tree.root_node, code)
    if language == "java":
        return _extract_java_symbols(tree.root_node, code)
    if language in ("javascript", "typescript"):
        return _extract_js_ts_symbols(tree.root_node, code)
    return []


# ---------------------------------------------------------------------------
# Python AST Extractor
# ---------------------------------------------------------------------------


def _extract_python_symbols(root: tree_sitter.Node, code: bytes) -> list[ExtractedSymbol]:
    symbols: list[ExtractedSymbol] = []

    for raw_node in root.children:
        node = raw_node
        decorators: list[str] = []

        # Handle decorated definitions: (@dec ... def func(): ...)
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "decorator":
                    decorators.append(_node_text(child, code))
                elif child.type in ("class_definition", "function_definition"):
                    node = child
                child = None

        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                name_node = None
                continue
            class_name = _node_text(name_node, code)
            name_node = None  # release node reference
            if not class_name:
                continue

            is_test_class = class_name.startswith("Test") or class_name.endswith("Test")
            l_start, l_end = _node_lines(node, code)
            symbols.append(
                ExtractedSymbol(
                    name=class_name,
                    type="class",
                    line_start=l_start,
                    line_end=l_end,
                    visibility="private" if class_name.startswith("_") else "public",
                    is_test=is_test_class,
                )
            )

            # Traverse methods inside body
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    m_node = child
                    m_decs: list[str] = []
                    if m_node.type == "decorated_definition":
                        for c in m_node.children:
                            if c.type == "decorator":
                                m_decs.append(_node_text(c, code))
                            elif c.type == "function_definition":
                                m_node = c
                            c = None

                    if m_node.type == "function_definition":
                        mname_node = m_node.child_by_field_name("name")
                        if not mname_node:
                            mname_node = None
                            continue
                        method_name = _node_text(mname_node, code)
                        mname_node = None  # release node reference
                        if not method_name:
                            continue
                        mtype = "constructor" if method_name == "__init__" else "method"
                        vis = (
                            "private"
                            if method_name.startswith("_") and method_name != "__init__"
                            else "public"
                        )
                        cl_start, cl_end = _node_lines(m_node, code)
                        mbody = m_node.child_by_field_name("body")
                        calls = _extract_calls_from_node(mbody, code)
                        mbody = None

                        is_test_method = (
                            is_test_class
                            or method_name.startswith("test_")
                            or method_name.endswith("_test")
                        )

                        # Check API decorator
                        is_api, api_route, api_method = False, None, None
                        for d in m_decs:
                            is_api, api_route, api_method = _parse_api_decorator(d)
                            if is_api:
                                break

                        symbols.append(
                            ExtractedSymbol(
                                name=method_name,
                                type=mtype,
                                line_start=cl_start,
                                line_end=cl_end,
                                parent_symbol=class_name,
                                visibility=vis,
                                calls=calls,
                                is_test=is_test_method,
                                is_api_endpoint=is_api,
                                api_route=api_route,
                                api_method=api_method,
                            )
                        )
                    child = None
                    m_node = None
                body = None

        elif node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                name_node = None
                continue
            fname = _node_text(name_node, code)
            name_node = None  # release node reference
            if not fname:
                continue

            fl_start, fl_end = _node_lines(node, code)
            fbody = node.child_by_field_name("body")
            calls = _extract_calls_from_node(fbody, code)
            fbody = None

            is_test_func = fname.startswith("test_") or fname.endswith("_test")

            is_api, api_route, api_method = False, None, None
            for d in decorators:
                is_api, api_route, api_method = _parse_api_decorator(d)
                if is_api:
                    break

            symbols.append(
                ExtractedSymbol(
                    name=fname,
                    type="function",
                    line_start=fl_start,
                    line_end=fl_end,
                    visibility="private" if fname.startswith("_") else "public",
                    calls=calls,
                    is_test=is_test_func,
                    is_api_endpoint=is_api,
                    api_route=api_route,
                    api_method=api_method,
                )
            )

    try:
        del node  # noqa: F821
        del raw_node  # noqa: F821
    except NameError:
        pass
    return symbols


# ---------------------------------------------------------------------------
# Java AST Extractor
# ---------------------------------------------------------------------------


def _extract_java_symbols(root: tree_sitter.Node, code: bytes) -> list[ExtractedSymbol]:
    symbols: list[ExtractedSymbol] = []

    for node in root.children:
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                name_node = None
                continue
            entity_name = _node_text(name_node, code)
            name_node = None  # release node reference
            if not entity_name:
                continue
            kind = "class"
            if node.type == "interface_declaration":
                kind = "interface"
            elif node.type == "enum_declaration":
                kind = "enum"

            is_test_class = entity_name.startswith("Test") or entity_name.endswith("Test")
            vis = _get_java_visibility(node)
            l_start, l_end = _node_lines(node, code)
            symbols.append(
                ExtractedSymbol(
                    name=entity_name,
                    type=kind,
                    line_start=l_start,
                    line_end=l_end,
                    visibility=vis,
                    is_test=is_test_class,
                )
            )

            # Extract members
            body = node.child_by_field_name("body")
            if body:
                for member in body.children:
                    if member.type == "method_declaration":
                        mname_node = member.child_by_field_name("name")
                        if not mname_node:
                            mname_node = None
                            continue
                        mname = _node_text(mname_node, code)
                        mname_node = None  # release node reference
                        if not mname:
                            continue
                        ml_start, ml_end = _node_lines(member, code)
                        mbody = member.child_by_field_name("body")
                        calls = _extract_calls_from_node(mbody, code)
                        mbody = None

                        is_test_method = (
                            is_test_class
                            or mname.startswith("test")
                            or "Test" in _node_text(member, code)[:100]
                        )
                        # Check Spring mapping annotations
                        m_text = _node_text(member, code)
                        is_api = "@GetMapping" in m_text or "@PostMapping" in m_text or "@RequestMapping" in m_text

                        symbols.append(
                            ExtractedSymbol(
                                name=mname,
                                type="method",
                                line_start=ml_start,
                                line_end=ml_end,
                                parent_symbol=entity_name,
                                visibility=_get_java_visibility(member),
                                calls=calls,
                                is_test=is_test_method,
                                is_api_endpoint=is_api,
                            )
                        )
                    elif member.type == "constructor_declaration":
                        cname_node = member.child_by_field_name("name")
                        if not cname_node:
                            cname_node = None
                            continue
                        cname = _node_text(cname_node, code)
                        cname_node = None  # release node reference
                        if not cname:
                            continue
                        cl_start, cl_end = _node_lines(member, code)
                        cbody = member.child_by_field_name("body")
                        calls = _extract_calls_from_node(cbody, code)
                        cbody = None
                        symbols.append(
                            ExtractedSymbol(
                                name=cname,
                                type="constructor",
                                line_start=cl_start,
                                line_end=cl_end,
                                parent_symbol=entity_name,
                                visibility=_get_java_visibility(member),
                                calls=calls,
                            )
                        )

                    member = None
                body = None

    try:
        del node  # noqa: F821
    except NameError:
        pass
    return symbols


def _get_java_visibility(node: tree_sitter.Node) -> str:
    """Helper to detect Java modifiers (public, private, protected)."""
    for child in node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type in ("public", "private", "protected"):
                    result = mod.type
                    mod = None
                    child = None
                    return result
            child = None
    try:
        del child  # noqa: F821
    except NameError:
        pass
    return "public"


# ---------------------------------------------------------------------------
# JavaScript / TypeScript AST Extractor
# ---------------------------------------------------------------------------


def _extract_js_ts_symbols(root: tree_sitter.Node, code: bytes) -> list[ExtractedSymbol]:
    symbols: list[ExtractedSymbol] = []

    def process_node(node: tree_sitter.Node) -> None:
        target = node

        # Unwrap export statements
        if node.type in ("export_statement", "export_default_statement"):
            for child in node.children:
                if child.type in (
                    "class_declaration",
                    "function_declaration",
                    "interface_declaration",
                    "enum_declaration",
                    "lexical_declaration",
                    "variable_declaration",
                ):
                    target = child
                    child = None
                    break
                child = None

        if target.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = target.child_by_field_name("name")
            if not name_node:
                name_node = None
                return
            entity_name = _node_text(name_node, code)
            name_node = None  # release node reference
            if not entity_name:
                return
            kind = "class"
            if target.type == "interface_declaration":
                kind = "interface"
            elif target.type == "enum_declaration":
                kind = "enum"

            is_test_class = entity_name.startswith("Test") or entity_name.endswith("Test")
            tl_start, tl_end = _node_lines(target, code)
            symbols.append(
                ExtractedSymbol(
                    name=entity_name,
                    type=kind,
                    line_start=tl_start,
                    line_end=tl_end,
                    visibility="public",
                    is_test=is_test_class,
                )
            )

            # Class / Interface body
            body = target.child_by_field_name("body")
            if body:
                for member in body.children:
                    if member.type in ("method_definition", "method_signature"):
                        mname_node = member.child_by_field_name("name")
                        if not mname_node:
                            mname_node = None
                            continue
                        mname = _node_text(mname_node, code)
                        mname_node = None  # release node reference
                        if not mname:
                            continue
                        mkind = "constructor" if mname == "constructor" else "method"
                        vis = "private" if mname.startswith("#") or mname.startswith("_") else "public"
                        ml_start, ml_end = _node_lines(member, code)
                        mbody = member.child_by_field_name("body")
                        calls = _extract_calls_from_node(mbody, code)
                        mbody = None

                        is_test_method = is_test_class or mname.startswith("test")

                        symbols.append(
                            ExtractedSymbol(
                                name=mname,
                                type=mkind,
                                line_start=ml_start,
                                line_end=ml_end,
                                parent_symbol=entity_name,
                                visibility=vis,
                                calls=calls,
                                is_test=is_test_method,
                            )
                        )
                    member = None
                body = None

        elif target.type == "function_declaration":
            name_node = target.child_by_field_name("name")
            if not name_node:
                name_node = None
                return
            fname = _node_text(name_node, code)
            name_node = None  # release node reference
            if not fname:
                return
            fl_start, fl_end = _node_lines(target, code)
            fbody = target.child_by_field_name("body")
            calls = _extract_calls_from_node(fbody, code)
            fbody = None

            is_test_func = fname.startswith("test") or fname in ("it", "describe", "test")

            symbols.append(
                ExtractedSymbol(
                    name=fname,
                    type="function",
                    line_start=fl_start,
                    line_end=fl_end,
                    visibility="private" if fname.startswith("_") else "public",
                    calls=calls,
                    is_test=is_test_func,
                )
            )

        elif target.type in ("lexical_declaration", "variable_declaration"):
            for decl in target.children:
                if decl.type == "variable_declarator":
                    val = decl.child_by_field_name("value")
                    if val and val.type in ("arrow_function", "function_expression"):
                        name_node = decl.child_by_field_name("name")
                        if name_node:
                            fname = _node_text(name_node, code)
                            name_node = None  # release node reference
                            if fname:
                                vl_start, vl_end = _node_lines(target, code)
                                vbody = val.child_by_field_name("body")
                                calls = _extract_calls_from_node(vbody, code)
                                vbody = None

                                is_test_fn = fname.startswith("test")

                                symbols.append(
                                    ExtractedSymbol(
                                        name=fname,
                                        type="function",
                                        line_start=vl_start,
                                        line_end=vl_end,
                                        visibility="private" if fname.startswith("_") else "public",
                                        calls=calls,
                                        is_test=is_test_fn,
                                    )
                                )

                        name_node = None
                    val = None
                decl = None

    for top_node in root.children:
        process_node(top_node)
        top_node = None

    try:
        del top_node  # noqa: F821
    except NameError:
        pass
    return symbols
