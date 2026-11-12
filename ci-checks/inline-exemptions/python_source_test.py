import textwrap
import unittest

from python_source import extract
from syntax import SourceError


def hits(source: str, path: str = "src/m.py") -> list[tuple[str, str, str]]:
    return extract(path, textwrap.dedent(source).encode())


def in_method(line: str) -> str:
    return f"import pytest\n\n\nclass C:\n    def run(self):\n        {line}\n"


class FormsTest(unittest.TestCase):
    def test_s8_forms_in_method(self) -> None:
        rows = [
            ("x = 1  # noqa: F841", "ruff", "F841"),
            ("y = f()  # type: ignore", "pyright", "*"),
            ("z = g()  # pyright: ignore[reportUnknownVariableType]", "pyright", "reportUnknownVariableType"),
            ("if a:  # pragma: no cover\n            pass", "coverage", "no cover"),
            ('pytest.skip("x")', "pytest", "skip"),
            ("h()  # fmt: skip", "ruff-format", "skip"),
            ("if b:  # pragma: no branch\n            pass", "coverage", "no branch"),
            ("k = 2  # noqa: V107", "vulture", "V107"),
        ]
        for line, tool, rule in rows:
            with self.subTest(line):
                self.assertEqual(hits(in_method(line)), [(tool, rule, "C.run")])

    def test_s9_decorator_and_module_level(self) -> None:
        source = """\
            # ruff: noqa
            import pytest


            @pytest.mark.skip
            def test_y():
                pass
            """
        self.assertEqual(sorted(hits(source, "tests/test_x.py")), [("pytest", "skip", "test_y"), ("ruff", "*", "")])

    def test_s15_same_key_counted(self) -> None:
        source = "def f():\n    a = 1  # noqa: E501\n    b = 1  # noqa: E501\n    c = 1  # noqa: E501\n"
        self.assertEqual(hits(source), [("ruff", "E501", "f")] * 3)

    def test_s28_other_spellings(self) -> None:
        rows = [
            ("x = 1  # NOQA:F841\n", "ruff"),
            ("if a:  #pragma nocover\n    pass\n", "coverage"),
            ("import pytest\npytestmark = pytest.mark.skip\n", "pytest"),
            ('class T:\n    def test(self):\n        self.skipTest("flaky")\n', "unittest"),
            ('import unittest\nraise unittest.SkipTest("x")\n', "unittest"),
            ("from pytest import mark\n\n\n@mark.skip\ndef test_a():\n    pass\n", "pytest"),
            ("# flake8: noqa\nx = 1\n", "ruff"),
            ("# yapf: disable\nx = 1\n", "ruff-format"),
        ]
        for source, tool in rows:
            with self.subTest(source):
                self.assertEqual([hit[0] for hit in hits(source)], [tool])

    def test_rules_and_lists(self) -> None:
        self.assertEqual(hits("x = 1  # noqa: E501,V107\n"), [("ruff", "E501", ""), ("vulture", "V107", "")])
        self.assertEqual(hits("x = 1  # noqa\n"), [("ruff", "*", "")])
        self.assertEqual(hits("x = 1  # type: ignore[attr-defined, misc]\n"), [("pyright", "attr-defined", ""), ("pyright", "misc", "")])
        self.assertEqual(hits("# pyright: basic, reportPrivateUsage=false\n"), [("pyright", "basic", ""), ("pyright", "reportPrivateUsage", "")])
        self.assertEqual(hits("# fmt: off\nx = 1\n# fmt: on\n"), [("ruff-format", "off", "")])

    def test_pytest_and_unittest_names(self) -> None:
        source = """\
            import pytest as pt
            import unittest
            from unittest import skipIf


            @pt.mark.xfail
            def test_a():
                pt.importorskip("numpy")
                pt.xfail("x")


            @unittest.skipUnless(True, "x")
            class T(unittest.TestCase):
                @skipIf(True, "x")
                def test_b(self):
                    pass

                @unittest.expectedFailure
                def test_c(self):
                    pass
            """
        self.assertEqual(
            sorted(hits(source)),
            [
                ("pytest", "importorskip", "test_a"),
                ("pytest", "xfail", "test_a"),
                ("pytest", "xfail", "test_a"),
                ("unittest", "expectedFailure", "T.test_c"),
                ("unittest", "skipIf", "T.test_b"),
                ("unittest", "skipUnless", ""),
            ],
        )

    def test_param_marks_and_no_type_check(self) -> None:
        source = """\
            import pytest
            import typing


            @pytest.mark.parametrize("x", [1, pytest.param(2, marks=pytest.mark.skip)])
            def test_a(x):
                pass


            @typing.no_type_check
            def g():
                pass
            """
        self.assertEqual(sorted(hits(source)), [("pyright", "no_type_check", "g"), ("pytest", "skip", "test_a")])

    def test_import_lines_are_not_hits(self) -> None:
        self.assertEqual(hits("from pytest import skip, mark\nfrom unittest import SkipTest, skip as s\n"), [])

    def test_s29_strings_are_not_comments(self) -> None:
        self.assertEqual(hits("x = '# noqa: E501'\ny = \"\"\"\n# type: ignore\n\"\"\"\n"), [])

    def test_s14_not_builtin(self) -> None:
        self.assertEqual(hits("x = 1  # nosec\n"), [])

    def test_s44_generated_headers(self) -> None:
        self.assertEqual(hits("# @generated\nx = 1\n", "src/gen.py"), [("generated", "generated", "")])
        header = "# Generated by the protocol buffer compiler.  DO NOT EDIT!\n# source: x.proto\nx = 1\n"
        self.assertEqual(hits(header, "src/x_pb2.py"), [("generated", "generated", "")])
        self.assertEqual(hits("#!/usr/bin/env python\n# @generated\n"), [("generated", "generated", "")])
        self.assertEqual(hits("x = 1\n# @generated\n"), [])

    def test_s10_jscpd(self) -> None:
        source = "def f():\n    # jscpd:ignore-start\n    x = 1\n    # jscpd:ignore-end\n"
        self.assertEqual(hits(source), [("jscpd", "ignore", "f")])


class FunctionsTest(unittest.TestCase):
    def test_s47_outside_functions(self) -> None:
        source = """\
            import unittest


            @unittest.skip("x")
            class C:
                x: int = 1  # type: ignore
            """
        self.assertEqual(sorted(hits(source)), [("pyright", "*", ""), ("unittest", "skip", "")])

    def test_nested_function_and_comment_above(self) -> None:
        source = """\
            class C:
                def run(self):
                    def inner():
                        return f()  # type: ignore
                    return inner

                # pragma: no cover
                def other(self):
                    return lambda: g()  # noqa: E731
            """
        self.assertEqual(
            hits(source), [("pyright", "*", "inner"), ("coverage", "no cover", "C.other"), ("ruff", "E731", "C.other")]
        )


class WhitelistTest(unittest.TestCase):
    def test_s31_vulture_whitelist(self) -> None:
        source = "from src.m import unused_func, Cls\n\nunused_func  # unused function\nCls.attr\n"
        self.assertEqual(
            hits(source, "src/vulture_whitelist.py"), [("vulture", "unused_func", ""), ("vulture", "Cls.attr", "")]
        )

    def test_other_files_are_not_whitelists(self) -> None:
        self.assertEqual(hits("unused_func\n", "src/m.py"), [])


class ErrorTest(unittest.TestCase):
    def test_s23_syntax_error(self) -> None:
        with self.assertRaises(SourceError) as raised:
            hits("def f(:\n    pass\n", "src/x.py")
        self.assertIn("syntax error", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
