"""Static validation of model-generated programs (spec Modules 9 and 29).

The first of two independent defences. This one is a **static AST allowlist**:
it runs before any execution, on any operating system, with no container
required, and it is fully unit-testable. The second defence is the container in
`sandbox.py`. Neither is trusted alone.

An allowlist, not a denylist. A denylist of dangerous names is unwinnable -
`getattr(x, "__cl" + "ass__")` defeats any list of forbidden strings. Only
constructs explicitly permitted here are allowed to exist in the tree at all.

The canonical escape this blocks:

    ().__class__.__bases__[0].__subclasses__()

which walks from a literal tuple to every loaded class, including ones that open
files and spawn processes. Every step of that walk is dunder attribute access,
so banning dunder attribute access outright closes the whole family rather than
one instance of it.

`validate` returns a report instead of raising: a rejected program is a normal,
expected research event (it becomes an "execution failure" data point), not an
exceptional condition.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

__all__ = ["ValidationReport", "validate_program", "ALLOWED_IMPORTS", "ALLOWED_BUILTINS"]

# Only what arithmetic over financial evidence genuinely needs.
ALLOWED_IMPORTS = frozenset({"math", "decimal", "statistics", "fractions", "json"})

ALLOWED_BUILTINS = frozenset(
    {
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "int", "len", "list", "map", "max", "min", "pow", "print",
        "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple",
        "zip", "Decimal", "sqrt", "log", "exp", "isinstance",
    }
)

# Names that are never acceptable, even though the allowlist would already
# exclude them. Listed explicitly so a rejection reports a useful reason rather
# than a generic "name not permitted".
_EXPLICITLY_FORBIDDEN = frozenset(
    {
        "eval", "exec", "compile", "open", "input", "__import__", "getattr",
        "setattr", "delattr", "globals", "locals", "vars", "dir", "breakpoint",
        "memoryview", "help", "exit", "quit", "object", "type", "super",
        "classmethod", "staticmethod", "property", "id", "hash",
    }
)

_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Return, ast.Pass, ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.FunctionDef, ast.arguments, ast.arg, ast.Call, ast.keyword,
    ast.Name, ast.Load, ast.Store, ast.Del, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Slice,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.comprehension, ast.Starred, ast.Attribute,
    ast.Import, ast.ImportFrom, ast.alias,
    ast.JoinedStr, ast.FormattedValue,
)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    violations: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.defined_names: set[str] = set()

    def _reject(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"line {line}: {message}")

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            self._reject(node, f"disallowed syntax: {type(node).__name__}")
            return
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # The whole ().__class__.__bases__[0].__subclasses__() family dies here.
        if node.attr.startswith("__") or node.attr.endswith("__"):
            self._reject(node, f"dunder attribute access is forbidden: {node.attr!r}")
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORTS:
                self._reject(node, f"import not permitted: {alias.name!r}")
            self.defined_names.add((alias.asname or root).split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_IMPORTS:
            self._reject(node, f"import not permitted: from {node.module!r}")
        for alias in node.names:
            self.defined_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined_names.add(node.name)
        for arg in node.args.args + node.args.kwonlyargs:
            self.defined_names.add(arg.arg)
        if node.args.vararg:
            self.defined_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.defined_names.add(node.args.kwarg.arg)
        if node.decorator_list:
            self._reject(node, "decorators are not permitted")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.defined_names.add(node.id)
            return
        if node.id in _EXPLICITLY_FORBIDDEN:
            self._reject(node, f"forbidden builtin: {node.id!r}")
            return
        if node.id.startswith("__"):
            self._reject(node, f"dunder name is forbidden: {node.id!r}")
            return
        if node.id not in ALLOWED_BUILTINS and node.id not in self.defined_names:
            self._reject(node, f"unknown name (not an allowed builtin): {node.id!r}")

    def visit_comprehension(self, node: ast.comprehension) -> None:
        # Comprehension targets bind before their body is visited.
        for name in ast.walk(node.target):
            if isinstance(name, ast.Name):
                self.defined_names.add(name.id)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        for name in ast.walk(node.target):
            if isinstance(name, ast.Name):
                self.defined_names.add(name.id)
        self.generic_visit(node)


def validate_program(source: str, *, max_chars: int = 20_000) -> ValidationReport:
    """Statically validate generated code. Never executes anything.

    Two passes: names are collected once so that forward references inside
    function bodies do not read as unknown names, then the tree is checked.
    """
    if not source or not source.strip():
        return ValidationReport(False, ("empty program",))
    if len(source) > max_chars:
        return ValidationReport(False, (f"program exceeds {max_chars} characters",))

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ValidationReport(False, (f"syntax error: {exc.msg} (line {exc.lineno})",))

    # Pre-pass: gather every binding so order of definition does not matter.
    prepass = _Validator()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            prepass.defined_names.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            prepass.defined_names.add(node.name)
            for arg in node.args.args + node.args.kwonlyargs:
                prepass.defined_names.add(arg.arg)
        elif isinstance(node, ast.alias):
            prepass.defined_names.add((node.asname or node.name).split(".")[0])

    validator = _Validator()
    validator.defined_names = set(prepass.defined_names)
    validator.visit(tree)

    return ValidationReport(not validator.violations, tuple(validator.violations))
