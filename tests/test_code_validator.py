"""Security tests for the generated-code validator (spec Modules 9, 29).

The escape attempts below are real techniques, not hypotheticals. If any of them
starts passing, generated code can reach the host and the failure is a security
incident rather than a test regression.
"""

import pytest

from backend.services.code_validator import validate_program


def ok(src: str) -> bool:
    return validate_program(src).ok


class TestLegitimatePrograms:
    """The validator is worthless if it rejects the programs it must permit."""

    def test_simple_arithmetic(self):
        assert ok("result = (125 - 100) / 100 * 100\nprint(result)")

    def test_decimal_import_and_use(self):
        assert ok(
            "from decimal import Decimal\n"
            "revenue = Decimal('1234.56')\n"
            "prior = Decimal('1000.00')\n"
            "print((revenue - prior) / prior * 100)"
        )

    def test_math_import(self):
        assert ok("import math\nprint(math.sqrt(16))")

    def test_function_definition_and_call(self):
        assert ok(
            "def growth(prev, curr):\n"
            "    return (curr - prev) / prev\n"
            "print(growth(100, 125))"
        )

    def test_forward_reference_between_functions(self):
        """Definition order must not matter."""
        assert ok(
            "def outer(x):\n"
            "    return inner(x) * 2\n"
            "def inner(x):\n"
            "    return x + 1\n"
            "print(outer(3))"
        )

    def test_list_comprehension(self):
        assert ok("values = [1, 2, 3]\nprint(sum([v * 2 for v in values]))")

    def test_loop_and_conditional(self):
        assert ok(
            "total = 0\n"
            "for i in range(10):\n"
            "    if i % 2 == 0:\n"
            "        total += i\n"
            "print(total)"
        )

    def test_json_output_protocol(self):
        assert ok("import json\nprint(json.dumps({'value': 25.0, 'unit': 'percent'}))")

    def test_fstring(self):
        assert ok("x = 5\nprint(f'value is {x}')")


class TestSandboxEscapes:
    """Real escape techniques. Each must be rejected."""

    def test_the_canonical_subclasses_walk(self):
        """().__class__.__bases__[0].__subclasses__() reaches every loaded class."""
        assert not ok("print(().__class__.__bases__[0].__subclasses__())")

    def test_globals_traversal_via_function_attribute(self):
        assert not ok("def f(): pass\nprint(f.__globals__)")

    def test_builtins_via_dunder(self):
        assert not ok("print(__builtins__)")

    def test_import_dunder_function(self):
        assert not ok("__import__('os').system('echo pwned')")

    @pytest.mark.parametrize(
        "src",
        [
            "import os",
            "import sys",
            "import subprocess",
            "import socket",
            "import shutil",
            "from os import system",
            "from subprocess import run",
            "import os.path",
        ],
    )
    def test_dangerous_imports_rejected(self, src):
        assert not ok(src + "\nprint(1)")

    @pytest.mark.parametrize(
        "name",
        ["eval", "exec", "compile", "open", "input", "getattr", "setattr",
         "globals", "locals", "vars", "breakpoint"],
    )
    def test_dangerous_builtins_rejected(self, name):
        assert not ok(f"print({name})")

    def test_string_concatenation_cannot_smuggle_an_attribute(self):
        """Defeats denylists; the allowlist rejects getattr outright."""
        assert not ok("print(getattr((), '__cl' + 'ass__'))")

    def test_mro_traversal(self):
        assert not ok("print(().__class__.__mro__)")

    def test_subclass_traversal_via_type(self):
        assert not ok("print(type(()).__subclasses__())")

    def test_code_object_access(self):
        assert not ok("def f(): pass\nprint(f.__code__)")

    def test_class_definition_is_not_permitted(self):
        """Class bodies open metaclass and descriptor tricks for no benefit here."""
        assert not ok("class Evil:\n    pass\nprint(1)")

    def test_lambda_is_not_permitted(self):
        assert not ok("f = lambda: 1\nprint(f())")

    def test_decorators_rejected(self):
        assert not ok("@staticmethod\ndef f():\n    return 1\nprint(f())")

    def test_with_statement_rejected(self):
        assert not ok("with open('x') as f:\n    print(f)")

    def test_try_except_rejected(self):
        """Exception handling can be used to probe the environment."""
        assert not ok("try:\n    x = 1\nexcept Exception:\n    pass\nprint(1)")

    def test_global_and_nonlocal_rejected(self):
        assert not ok("def f():\n    global x\n    x = 1\nprint(1)")

    def test_yield_rejected(self):
        assert not ok("def f():\n    yield 1\nprint(1)")

    def test_async_rejected(self):
        assert not ok("async def f():\n    return 1\nprint(1)")

    def test_walrus_in_unknown_name_still_checked(self):
        assert not ok("print(os)")


class TestMalformedInput:
    def test_empty_program(self):
        assert not ok("")

    def test_whitespace_only(self):
        assert not ok("   \n  \n")

    def test_syntax_error_reported_not_raised(self):
        report = validate_program("def f(:\n  pass")
        assert not report.ok
        assert any("syntax" in v.lower() for v in report.violations)

    def test_oversized_program_rejected(self):
        assert not validate_program("x = 1\n" * 10_000).ok

    def test_unknown_name_rejected(self):
        assert not ok("print(undefined_thing)")


class TestReportQuality:
    """A rejection must be diagnosable - it becomes research data."""

    def test_violations_name_the_reason(self):
        report = validate_program("import os\nprint(1)")
        assert report.violations
        assert any("os" in v for v in report.violations)

    def test_violations_carry_line_numbers(self):
        report = validate_program("x = 1\nimport os\n")
        assert any("line 2" in v for v in report.violations)

    def test_report_is_falsy_when_invalid(self):
        assert not validate_program("import os")
        assert validate_program("print(1)")

    def test_multiple_violations_all_reported(self):
        report = validate_program("import os\nimport sys\nprint(eval)")
        assert len(report.violations) >= 3
