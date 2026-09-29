"""Tests for Tree-sitter parsing and symbol extraction."""

from msk.analyzer.parser import parse_bytes
from msk.analyzer.symbols import extract_symbols


def test_python_symbol_extraction() -> None:
    code = b"""
class OrderProcessor:
    def __init__(self, db):
        self.db = db

    def process(self, order):
        return True

    def _internal(self):
        pass

def standalone_function():
    pass
"""
    tree = parse_bytes(code, "python")
    assert tree is not None
    symbols = extract_symbols(tree, "python", code)
    classes = {s.name: s for s in symbols if s.type == "class"}
    constructors = {s.name: s for s in symbols if s.type == "constructor"}
    methods = {s.name: s for s in symbols if s.type == "method"}
    functions = {s.name: s for s in symbols if s.type == "function"}

    assert "OrderProcessor" in classes
    assert classes["OrderProcessor"].visibility == "public"

    assert "__init__" in constructors
    assert constructors["__init__"].parent_symbol == "OrderProcessor"

    assert "process" in methods
    assert "_internal" in methods
    assert methods["_internal"].visibility == "private"

    assert "standalone_function" in functions


def test_java_symbol_extraction() -> None:
    code = b"""
package com.example;

public class PaymentService {
    public PaymentService() {}
    public void execute() {}
    private void audit() {}
}

interface Worker {
    void run();
}
"""
    tree = parse_bytes(code, "java")
    assert tree is not None
    symbols = extract_symbols(tree, "java", code)
    classes = {s.name: s for s in symbols if s.type == "class"}
    constructors = {s.name: s for s in symbols if s.type == "constructor"}
    methods = {s.name: s for s in symbols if s.type == "method"}
    interfaces = {s.name: s for s in symbols if s.type == "interface"}

    assert "PaymentService" in classes
    assert classes["PaymentService"].visibility == "public"

    assert "PaymentService" in constructors

    assert "execute" in methods
    assert methods["execute"].visibility == "public"

    assert "audit" in methods
    assert methods["audit"].visibility == "private"

    assert "Worker" in interfaces


def test_typescript_symbol_extraction() -> None:
    code = b"""
export interface IAuth {
    login(): boolean;
}

export class AuthService implements IAuth {
    constructor() {}
    login(): boolean { return true; }
}

export function verifyToken(token: string): boolean {
    return true;
}
"""
    tree = parse_bytes(code, "typescript")
    assert tree is not None
    symbols = extract_symbols(tree, "typescript", code)
    classes = {s.name: s for s in symbols if s.type == "class"}
    interfaces = {s.name: s for s in symbols if s.type == "interface"}
    methods = {s.name: s for s in symbols if s.type == "method"}
    functions = {s.name: s for s in symbols if s.type == "function"}

    assert "IAuth" in interfaces
    assert "AuthService" in classes
    assert "login" in methods
    assert "verifyToken" in functions
