"""Import statement extraction for Python, Java, JavaScript, and TypeScript."""

import re

import tree_sitter
from pydantic import BaseModel, Field


class ExtractedImport(BaseModel):
    """Represents an imported module or symbol."""

    raw: str
    module: str
    names: list[str] = Field(default_factory=list)
    is_relative: bool = False


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


def extract_imports(tree: tree_sitter.Tree, language: str, code: bytes = b"") -> list[ExtractedImport]:
    """Extract imports from an AST tree based on language grammar."""
    if language == "python":
        return _extract_python_imports(tree.root_node, code)
    if language == "java":
        return _extract_java_imports(tree.root_node, code)
    if language in ("javascript", "typescript"):
        return _extract_js_ts_imports(tree.root_node, code)
    return []


# ---------------------------------------------------------------------------
# Python Imports
# ---------------------------------------------------------------------------


def _extract_python_imports(root: tree_sitter.Node, code: bytes) -> list[ExtractedImport]:
    imports: list[ExtractedImport] = []

    for node in root.children:
        if node.type == "import_statement":
            raw = _node_text(node, code)
            names: list[str] = []
            for child in node.children:
                if child.type == "dotted_name":
                    names.append(_node_text(child, code))
                child = None  # release node reference
            for name in names:
                imports.append(
                    ExtractedImport(
                        raw=raw,
                        module=name,
                        names=[name],
                        is_relative=False,
                    )
                )

        elif node.type == "import_from_statement":
            raw = _node_text(node, code)
            mod_node = node.child_by_field_name("module_name")
            module = _node_text(mod_node, code) if mod_node else ""
            mod_node = None  # release node reference

            # Check relative dots
            dots = 0
            for child in node.children:
                if child.type == "relative_import":
                    dots = len(_node_text(child, code))
                    child = None  # release node reference
                elif child.type == "import":
                    child = None  # release node reference
                    break
                child = None  # release node reference

            is_relative = dots > 0 or module.startswith(".")

            imported_names: list[str] = []
            past_import_keyword = False
            for child in node.children:
                if child.type == "import":
                    past_import_keyword = True
                    child = None  # release node reference
                    continue
                if past_import_keyword:
                    if child.type in ("dotted_name", "identifier"):
                        imported_names.append(_node_text(child, code))
                    elif child.type == "aliased_import":
                        alias_name = child.child_by_field_name("name")
                        if alias_name:
                            imported_names.append(_node_text(alias_name, code))
                        alias_name = None  # release node reference
                child = None  # release node reference

            imports.append(
                ExtractedImport(
                    raw=raw,
                    module=module,
                    names=imported_names,
                    is_relative=is_relative,
                )
            )

    # Explicitly null loop variable to force refcount-based cleanup of Nodes
    # before any subsequent parse call triggers Python's cyclic GC.
    try:
        del node  # noqa: F821
    except NameError:
        pass
    return imports



# ---------------------------------------------------------------------------
# Java Imports
# ---------------------------------------------------------------------------


def _extract_java_imports(root: tree_sitter.Node, code: bytes) -> list[ExtractedImport]:
    imports: list[ExtractedImport] = []

    for node in root.children:
        if node.type == "import_declaration":
            raw = _node_text(node, code)
            clean = raw.removeprefix("import").removeprefix("static").strip().rstrip(";")
            clean = clean.strip()
            if clean:
                parts = clean.split(".")
                name = parts[-1]
                module = ".".join(parts[:-1]) if len(parts) > 1 else clean
                imports.append(
                    ExtractedImport(
                        raw=raw,
                        module=module,
                        names=[name],
                        is_relative=False,
                    )
                )

    try:
        del node  # noqa: F821
    except NameError:
        pass
    return imports


# ---------------------------------------------------------------------------
# JavaScript / TypeScript Imports
# ---------------------------------------------------------------------------


def _extract_js_ts_imports(root: tree_sitter.Node, code: bytes) -> list[ExtractedImport]:
    imports: list[ExtractedImport] = []

    for node in root.children:
        if node.type == "import_statement":
            raw = _node_text(node, code)
            source_node = node.child_by_field_name("source")
            if source_node:
                source_str = _node_text(source_node, code).strip("'\"")
                source_node = None  # release node reference
                is_rel = source_str.startswith(".")

                names: list[str] = []
                clause = node.child_by_field_name("clause")
                if clause:
                    clause_text = _node_text(clause, code)
                    clause = None  # release node reference
                    matches = re.findall(r"\b([a-zA-Z_$][a-zA-Z0-9_$]*)\b", clause_text)
                    names = [m for m in matches if m not in ("from", "as", "import", "type")]

                imports.append(
                    ExtractedImport(
                        raw=raw,
                        module=source_str,
                        names=names,
                        is_relative=is_rel,
                    )
                )
            else:
                source_node = None  # release node reference

    try:
        del node  # noqa: F821
    except NameError:
        pass
    return imports

