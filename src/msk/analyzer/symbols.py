"""AST symbol extraction for Python, Java, JavaScript, and TypeScript."""

from pydantic import BaseModel, Field
import tree_sitter


class ExtractedSymbol(BaseModel):
    """Represents a symbol discovered in source code."""

    name: str
    type: str  # class, interface, enum, function, method, constructor
    line_start: int
    line_end: int
    parent_symbol: str | None = None
    visibility: str = "public"  # public, private, protected
    parameters: list[str] = Field(default_factory=list)


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

    for node in root.children:
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                name_node = None
                continue
            class_name = _node_text(name_node, code)
            name_node = None  # release node reference
            if not class_name:
                continue
            l_start, l_end = _node_lines(node, code)
            symbols.append(
                ExtractedSymbol(
                    name=class_name,
                    type="class",
                    line_start=l_start,
                    line_end=l_end,
                    visibility="private" if class_name.startswith("_") else "public",
                )
            )

            # Traverse methods inside body
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "function_definition":
                        mname_node = child.child_by_field_name("name")
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
                        cl_start, cl_end = _node_lines(child, code)
                        symbols.append(
                            ExtractedSymbol(
                                name=method_name,
                                type=mtype,
                                line_start=cl_start,
                                line_end=cl_end,
                                parent_symbol=class_name,
                                visibility=vis,
                            )
                        )
                    child = None  # release node reference
                body = None  # release node reference

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
            symbols.append(
                ExtractedSymbol(
                    name=fname,
                    type="function",
                    line_start=fl_start,
                    line_end=fl_end,
                    visibility="private" if fname.startswith("_") else "public",
                )
            )


    # Explicitly null loop variables so tree-sitter Nodes are freed via
    # reference-counting rather than waiting for the cyclic GC.  On Python
    # 3.14 / tree-sitter 0.26 / Windows, Nodes whose owning Tree is swept
    # by the GC cause an access violation if any Python Node object is still
    # alive at GC sweep time.  Nulling here guarantees refcount drops to 0
    # immediately and the Tree frees cleanly.
    try:
        del node  # noqa: F821
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

            # Check visibility modifiers
            vis = _get_java_visibility(node)
            l_start, l_end = _node_lines(node, code)
            symbols.append(
                ExtractedSymbol(
                    name=entity_name,
                    type=kind,
                    line_start=l_start,
                    line_end=l_end,
                    visibility=vis,
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
                        symbols.append(
                            ExtractedSymbol(
                                name=mname,
                                type="method",
                                line_start=ml_start,
                                line_end=ml_end,
                                parent_symbol=entity_name,
                                visibility=_get_java_visibility(member),
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
                        symbols.append(
                            ExtractedSymbol(
                                name=cname,
                                type="constructor",
                                line_start=cl_start,
                                line_end=cl_end,
                                parent_symbol=entity_name,
                                visibility=_get_java_visibility(member),
                            )
                        )

                    member = None  # release node reference
                body = None  # release node reference

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
                    mod = None  # release node reference
                    child = None  # release node reference
                    return result
            child = None  # release node reference
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
                    child = None  # release node reference
                    break
                child = None  # release node reference

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

            tl_start, tl_end = _node_lines(target, code)
            symbols.append(
                ExtractedSymbol(
                    name=entity_name,
                    type=kind,
                    line_start=tl_start,
                    line_end=tl_end,
                    visibility="public",
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
                        symbols.append(
                            ExtractedSymbol(
                                name=mname,
                                type=mkind,
                                line_start=ml_start,
                                line_end=ml_end,
                                parent_symbol=entity_name,
                                visibility=vis,
                            )
                        )
                    member = None  # release node reference
                body = None  # release node reference

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
            symbols.append(
                ExtractedSymbol(
                    name=fname,
                    type="function",
                    line_start=fl_start,
                    line_end=fl_end,
                    visibility="private" if fname.startswith("_") else "public",
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
                                symbols.append(
                                    ExtractedSymbol(
                                        name=fname,
                                        type="function",
                                        line_start=vl_start,
                                        line_end=vl_end,
                                        visibility="private" if fname.startswith("_") else "public",
                                    )
                                )

                        name_node = None  # release node reference
                    val = None  # release node reference
                decl = None  # release node reference

    for top_node in root.children:
        process_node(top_node)
        top_node = None  # release node reference

    try:
        del top_node  # noqa: F821
    except NameError:
        pass
    return symbols
