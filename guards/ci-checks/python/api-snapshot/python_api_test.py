import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import python_api

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "python_api.py"
CORE = HERE.parent.parent / "api-snapshot" / "api_snapshot.py"
PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["uv_build>=0.13,<0.14"]
build-backend = "uv_build"
"""


def write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def project(root: Path, files: dict[str, str], name: str = "demo") -> Path:
    """A uv_build project whose files are given relative to src/."""
    write(root, {"pyproject.toml": PYPROJECT.format(name=name)})
    return write(root / "src", files).parent


def scope_setting(prefixes: str, working_directory: str = ".", language: str = "python") -> str:
    """A repo-guards.toml section setting api-snapshot-scope.<language> to prefixes, written as TOML."""
    return f"[project.{json.dumps(working_directory)}]\napi-snapshot-scope.{language} = {prefixes}\n"


def settings(root: Path, text: str) -> Path:
    return write(root, {".github/checks/repo-guards.toml": text})


def run(root: Path, output: Path, working_directory: str = ".", env: dict[str, str] | None = None):
    """Run the generator as the Generate step does: from the repo root and the working-directory key."""
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(root), working_directory, str(output)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def generate(
    files: dict[str, str], scope: tuple[str, ...] | None = None
) -> dict[tuple[str, str], python_api.Entry]:
    """Entries of a site holding the given files, by (parent, name)."""
    with tempfile.TemporaryDirectory() as tmp:
        site = write(Path(tmp), files)
        names = python_api.units(site)
        python_api.check_sources(site, names)
        entries = python_api.generate(site, names, scope)
    by_path = {(entry.parent, entry.name): entry for entry in entries}
    assert len(by_path) == len(entries), "two entries share a parent and name"
    return by_path


def compare(snapshot: Path, entries: Path, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(CORE),
            "--entries",
            str(entries),
            "--snapshot",
            str(snapshot),
            "--output",
            str(tmp / "new.json"),
            "--artifact",
            "python-api-snapshot-test",
            "--path",
            ".github/checks/api-python.json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": ""},
    )


STUB_OVERLOADS = "from typing import overload\n@overload\ndef g(x: int) -> int: ...\n@overload\ndef g(x: bytes) -> bytes: ...\n"
BOTH = "(x: int) -> int\n(x: bytes) -> bytes"
RICH = {
    "demo/__init__.py": '''\
        """Demo package."""
        from dataclasses import dataclass
        from enum import Enum
        from os import path
        from typing import Generic, TypeVar, overload

        from . import sub as sub_alias
        from ._impl import moved

        __all__ = [
            "Box", "Color", "LIMIT", "Point", "Pair", "aopen", "close", "ident", "kw", "moved", "open", "path",
            "sub_alias", "C", "D",
        ]
        __version__ = "1.0"
        T = TypeVar("T")
        LIMIT: int = 5


        @overload
        def open(x: int) -> int: ...
        @overload
        def open(x: str, *, mode: str = "r") -> str: ...
        def open(x, *, mode="r"):
            """Docs."""
            return x


        def close(a, /, b: int = 5, *args: str, c, **kw: object) -> None: ...


        def kw(a, *, b=1): ...


        async def aopen() -> None: ...


        def ident[V](x: V) -> V: ...


        def _helper(): ...


        class Color(Enum):
            RED = 1
            GREEN = "g"


        class Box(Generic[T]):
            size: int = 3

            def __init__(self, item: T) -> None:
                self._item = item

            @property
            def item(self) -> T:
                return self._item

            @classmethod
            def build(cls, n: int = 2) -> "Box": ...

            def _private(self): ...


        @dataclass(frozen=True)
        class Point:
            x: int
            y: int = 0


        class C[K: int, *Ts, **P]: ...


        class D[K: (int, str) = int]: ...


        type Pair[U] = tuple[U, U]
        ''',
    "demo/_impl.py": '''\
        def moved(p: list[int] = [1, 2]) -> dict[str, int]:
            """Docs."""
            return {}
        ''',
    "demo/sub/__init__.py": "def g(x=5): ...\n",
    "demo/sub/mod.py": "def h() -> None: ...\n",
}


class GenerateTest(unittest.TestCase):
    def test_reexport_from_private_module(self):
        entries = generate(RICH)
        self.assertEqual(
            entries[("demo", "moved")],
            python_api.Entry("demo", "demo", "function", "moved", "(p: list[int] = [1, 2]) -> dict[str, int]"),
        )
        self.assertFalse([key for key in entries if key[0].startswith("demo._impl") or key[1] == "_impl"])

    def test_private_names_absent(self):
        entries = generate(RICH)
        names = {name for _, name in entries}
        for private in ("_helper", "_private", "_item", "T", "Generic", "TypeVar", "overload", "Enum", "dataclass"):
            self.assertNotIn(private, names)

    def test_class_members_and_inherited(self):
        entries = generate(
            {
                "demo/__init__.py": """\
                    class _Base:
                        def m(self, a: int = 1) -> int: ...
                    class P(_Base):
                        x: int = 3
                        def n(self) -> None: ...
                        class Inner:
                            z: str
                    """
            }
        )
        self.assertEqual(entries[("demo.P", "m")].signature, "(self, a: int = 1) -> int")
        self.assertEqual(entries[("demo.P", "n")].signature, "(self) -> None")
        self.assertEqual(entries[("demo.P", "x")].kind, "attribute")
        self.assertEqual(entries[("demo.P", "Inner")].kind, "class")
        self.assertEqual(entries[("demo.P.Inner", "z")].signature, "instance-attribute: str")
        self.assertNotIn(("demo", "_Base"), entries)

    def test_public_submodule_walked(self):
        entries = generate(RICH)
        self.assertEqual(entries[("", "demo")], python_api.Entry("demo", "", "module", "demo", ""))
        self.assertEqual(entries[("demo", "sub")], python_api.Entry("demo", "demo", "module", "sub", ""))
        self.assertEqual(entries[("demo.sub", "g")].signature, "(x = 5)")
        self.assertEqual(entries[("demo.sub", "mod")].kind, "module")
        self.assertEqual(entries[("demo.sub.mod", "h")].signature, "() -> None")

    def test_signature_format(self):
        entries = generate(RICH)
        expected = {
            ("demo", "close"): ("function", "(a, /, b: int = 5, *args: str, c, **kw: object) -> None"),
            ("demo", "kw"): ("function", "(a, *, b = 1)"),
            ("demo", "aopen"): ("function", "async () -> None"),
            ("demo", "ident"): ("function", "[V](x: V) -> V"),
            ("demo", "LIMIT"): ("attribute", "module-attribute: int = 5"),
            ("demo", "Color"): ("class", "class(Enum)"),
            ("demo.Color", "RED"): ("attribute", "class-attribute instance-attribute = 1"),
            ("demo.Color", "GREEN"): ("attribute", "class-attribute instance-attribute = 'g'"),
            ("demo", "Box"): ("class", "class(Generic[T])"),
            ("demo.Box", "size"): ("attribute", "class-attribute instance-attribute: int = 3"),
            ("demo.Box", "__init__"): ("function", "(self, item: T) -> None"),
            ("demo.Box", "item"): ("attribute", "property: T"),
            ("demo.Box", "build"): ("function", "classmethod (cls, n: int = 2) -> Box"),
            ("demo", "Point"): ("class", "dataclass class"),
            ("demo.Point", "__init__"): ("function", "(self, x: int, y: int = 0) -> None"),
            ("demo.Point", "x"): ("attribute", "instance-attribute: int"),
            ("demo.Point", "y"): ("attribute", "class-attribute instance-attribute: int = 0"),
            ("demo", "C"): ("class", "class[K: int, *Ts, **P]"),
            ("demo", "D"): ("class", "class[K: (int, str) = int]"),
            ("demo", "Pair"): ("type alias", "type[U] = tuple[U, U]"),
            ("demo", "path"): ("alias", "os.path"),
            ("demo", "sub_alias"): ("module", "demo.sub"),
        }
        actual = {key: (entries[key].kind, entries[key].signature) for key in expected if key in entries}
        self.assertEqual(actual, expected)

    def test_default_change_changes_signature(self):
        five = generate({"demo/__init__.py": "def f(x=5): ...\n"})[("demo", "f")]
        six = generate({"demo/__init__.py": "def f(x=6): ...\n"})[("demo", "f")]
        self.assertEqual(five.key, six.key)
        self.assertEqual((five.signature, six.signature), ("(x = 5)", "(x = 6)"))

    def test_overloads_one_entry(self):
        entries = generate(RICH)
        self.assertEqual(
            entries[("demo", "open")],
            python_api.Entry(
                "demo", "demo", "function", "open", "(x: int) -> int\n(x: str, *, mode: str = 'r') -> str"
            ),
        )

    def test_stub_only_overloads(self):
        entries = generate(
            {
                "demo/__init__.pyi": """\
                    from typing import overload
                    @overload
                    def f(x: int) -> int: ...
                    @overload
                    def f(x: str) -> str: ...
                    @overload
                    def _hidden(x: int) -> int: ...
                    class C:
                        @overload
                        def m(self, x: int) -> int: ...
                        @overload
                        def m(self, x: str) -> str: ...
                    """
            }
        )
        expected = python_api.Entry("demo", "demo", "function", "f", "(x: int) -> int\n(x: str) -> str")
        self.assertEqual(entries[("demo", "f")], expected)
        self.assertEqual(entries[("demo.C", "m")].signature, "(self, x: int) -> int\n(self, x: str) -> str")
        self.assertNotIn(("demo", "_hidden"), entries)

    def test_stub_only_overloads_inherited_overridden_exported(self):
        entries = generate(
            {
                "demo/__init__.pyi": """\
                    from typing import overload
                    from ._impl import g
                    __all__ = ["Base", "Sub", "Over", "g"]
                    class Base:
                        @overload
                        def m(self, x: int) -> int: ...
                        @overload
                        def m(self, x: str) -> str: ...
                        def n(self) -> None: ...
                    class Sub(Base): ...
                    class Over(Base):
                        @overload
                        def n(self, x: int) -> int: ...
                        @overload
                        def n(self, x: str) -> str: ...
                    """,
                "demo/_impl.pyi": """\
                    from typing import overload
                    @overload
                    def g(x: int) -> int: ...
                    @overload
                    def g(x: bytes) -> bytes: ...
                    """,
            }
        )
        both = "(self, x: int) -> int\n(self, x: str) -> str"
        self.assertEqual(entries[("demo.Sub", "m")].signature, both)
        self.assertEqual(entries[("demo.Over", "n")].signature, both)
        self.assertEqual(entries[("demo.Base", "n")].signature, "(self) -> None")
        self.assertEqual(
            entries[("demo", "g")], python_api.Entry("demo", "demo", "function", "g", "(x: int) -> int\n(x: bytes) -> bytes")
        )

    def test_stub_only_overloads_star_export(self):
        entries = generate({"demo/__init__.pyi": "from ._impl import *\n", "demo/_impl.pyi": STUB_OVERLOADS})
        self.assertEqual(entries[("demo", "g")], python_api.Entry("demo", "demo", "function", "g", BOTH))

    def test_stub_only_overloads_merged_with_source(self):
        entries = generate(
            {
                "demo/__init__.py": "class C:\n    pass\n",
                "demo/__init__.pyi": STUB_OVERLOADS
                + "class C:\n"
                + "    @overload\n    def g(self, x: int) -> int: ...\n"
                + "    @overload\n    def g(self, x: bytes) -> bytes: ...\n",
                "demo/sub.py": "",
                "demo/sub.pyi": STUB_OVERLOADS,
                "demo/attr.py": "g = len\n",
                "demo/attr.pyi": STUB_OVERLOADS,
            }
        )
        self.assertEqual(entries[("demo", "g")], python_api.Entry("demo", "demo", "function", "g", BOTH))
        self.assertEqual(entries[("demo.C", "g")].signature, "(self, x: int) -> int\n(self, x: bytes) -> bytes")
        self.assertEqual(entries[("demo.sub", "g")], python_api.Entry("demo", "demo.sub", "function", "g", BOTH))
        self.assertEqual(entries[("demo.attr", "g")], python_api.Entry("demo", "demo.attr", "function", "g", BOTH))

    def test_stub_overloads_attach_to_source_implementation(self):
        entries = generate({"demo/__init__.py": "def g(x):\n    return x\n", "demo/__init__.pyi": STUB_OVERLOADS})
        self.assertEqual(entries[("demo", "g")], python_api.Entry("demo", "demo", "function", "g", BOTH))

    def test_labels_sorted(self):
        entries = generate(
            {
                "demo/__init__.py": """\
                    from abc import ABC, abstractmethod
                    class K(ABC):
                        @classmethod
                        @abstractmethod
                        def make(cls) -> None: ...
                    """
            }
        )
        self.assertEqual(entries[("demo.K", "make")].signature, "abstractmethod classmethod (cls) -> None")

    def test_dunder_attributes_skipped(self):
        entries = generate(
            {
                "demo/__init__.py": """\
                    __version__ = "1"
                    __author__ = "x"
                    class K:
                        __slots__ = ()
                        __hash__ = None
                        def __eq__(self, other: object) -> bool: ...
                    """
            }
        )
        self.assertEqual(
            sorted(entries),
            [("", "demo"), ("demo", "K"), ("demo.K", "__eq__")],
        )

    def test_entry_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write(Path(tmp), RICH)
            entries = python_api.generate(site, python_api.units(site))
        self.assertEqual(entries, sorted(entries))
        for entry in entries:
            self.assertEqual(entry._fields, ("unit", "parent", "kind", "name", "signature"))
            self.assertTrue(all(isinstance(value, str) for value in entry))
            self.assertEqual(entry.unit, "demo")
            self.assertEqual(entry.parent.split(".")[0], "demo" if entry.parent else "")


SCOPED = {
    "demo/__init__.py": "def top() -> None: ...\n",
    "demo/sub/__init__.py": """\
        from ._impl import Moved
        __all__ = ["K", "Moved", "f"]
        def f() -> None: ...
        class K:
            def m(self) -> None: ...
        """,
    "demo/sub/_impl.py": "class Moved:\n    def go(self) -> None: ...\n",
    "demo/sub/io.py": "def g() -> None: ...\n",
    "demo/subx.py": "def h() -> None: ...\n",
    "other/__init__.py": "def o() -> None: ...\n",
}


class ScopeTest(unittest.TestCase):
    def test_submodule_prefix_keeps_only_its_module_tree(self):
        self.assertEqual(
            set(generate(SCOPED, ("demo.sub",))),
            {
                ("demo", "sub"),
                ("demo.sub", "f"),
                ("demo.sub", "K"),
                ("demo.sub.K", "m"),
                ("demo.sub", "Moved"),
                ("demo.sub.Moved", "go"),
                ("demo.sub", "io"),
                ("demo.sub.io", "g"),
            },
        )

    def test_several_prefixes_across_units(self):
        self.assertEqual(
            set(generate(SCOPED, ("demo.sub.io", "other"))),
            {("demo.sub", "io"), ("demo.sub.io", "g"), ("", "other"), ("other", "o")},
        )

    def test_unit_prefixes_keep_everything(self):
        self.assertEqual(generate(SCOPED, ("demo", "other")), generate(SCOPED))

    def test_prefix_is_a_module_not_a_class_or_private_module(self):
        # Class members belong to the class's module; a re-export belongs to the exporting module.
        for scope in (("demo.sub.K",), ("demo.sub._impl",), ("missing",)):
            with self.subTest(scope=scope):
                self.assertEqual(generate(SCOPED, scope), {})

    def test_in_scope_compares_by_segment(self):
        scope = ("demo.sub",)
        self.assertTrue(python_api.in_scope("demo.sub", scope))
        self.assertTrue(python_api.in_scope("demo.sub.io", scope))
        self.assertFalse(python_api.in_scope("demo.subx", scope))
        self.assertFalse(python_api.in_scope("demo", scope))
        self.assertTrue(python_api.in_scope("demo", None))


class ReadScopeTest(unittest.TestCase):
    WHERE = '[project."."] api-snapshot-scope.python'

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def read(self, text: str | None, working_directory: str = ".") -> python_api.Scope:
        if text is not None:
            settings(self.root, text)
        return python_api.read_scope(self.root, working_directory)

    def assert_rejected(self, text: str, *needles: str) -> None:
        with self.assertRaises(python_api.Error) as caught:
            self.read(text)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))

    def test_unset_is_the_whole_project(self):
        cases = {
            "no file": None,
            "no section": '[project."other"]\nsource-dirs = ["src"]\n',
            "path only": '[project."."]\napi-snapshot.python = ".github/checks/api-python.json"\n',
            "other language only": scope_setting('["kvm_op::driver"]', language="rust"),
            "other working directory": scope_setting('["pkg.sub"]', "sub"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.read(text))

    def test_valid_prefixes_in_order(self):
        self.assertEqual(self.read(scope_setting('["pkg.sub", "pkg", "_pkg.a1"]')), ("pkg.sub", "pkg", "_pkg.a1"))
        self.assertEqual(self.read(scope_setting('["paquet.módulo"]')), ("paquet.módulo",))

    def test_own_working_directory_section(self):
        text = scope_setting('["a.b"]') + scope_setting('["c.d"]', "svc/api")
        self.assertEqual(self.read(text, "svc/api"), ("c.d",))
        self.assertEqual(self.read(text, "."), ("a.b",))

    def test_invalid_prefixes_name_key_and_value(self):
        for prefix in ["pkg/sub", "pkg.", ".pkg", "pkg..sub", "pkg-sub", "1pkg", "", "pkg.sub.*", "a\x00", "a\nb"]:
            with self.subTest(prefix=prefix):
                text = scope_setting(json.dumps(["pkg", prefix]))
                self.assert_rejected(text, self.WHERE, python_api.quote(prefix), "dotted module name")

    def test_empty_list_is_rejected(self):
        self.assert_rejected(scope_setting("[]"), self.WHERE, "at least one")

    def test_bad_setting_types(self):
        for text, needle in [
            ('[project."."]\napi-snapshot-scope = ["a"]\n', '[project."."] api-snapshot-scope is not a table'),
            (scope_setting('"pkg.sub"'), "is not an array of strings"),
            (scope_setting("[1]"), "is not an array of strings"),
            (scope_setting('[["a"]]'), "is not an array of strings"),
            (scope_setting('{ a = "b" }'), "is not an array of strings"),
            ('[project]\n"." = 1\n', '[project."."] is not a table'),
            ('project = "x"\n', "[project] is not a table"),
            ("[project\n", "cannot read .github/checks/repo-guards.toml"),
        ]:
            with self.subTest(text=text):
                self.assert_rejected(text, needle)


class SiteTest(unittest.TestCase):
    def test_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            hostile = "x\n::error::forged"
            for directory in ("pkg", "ns", "_private", "demo-0.1.dist-info", "demo-0.1.data", hostile, "class"):
                (site / directory).mkdir()
            for file in ("single.py", "stubbed.pyi", "_hidden.py", "README.txt", "x.pth"):
                (site / file).write_text("")
            self.assertEqual(python_api.units(site), ["ns", "pkg", "single", "stubbed"])

    def test_no_unit_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write(Path(tmp), {"_private/__init__.py": "", "demo-stubs/__init__.pyi": ""})
            with self.assertRaisesRegex(python_api.Error, "no public package or module"):
                python_api.units(site)

    def test_bad_wheel_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = write(Path(tmp), {"demo-0.1-py3-none-any.whl": "not a zip"}) / "demo-0.1-py3-none-any.whl"
            with self.assertRaisesRegex(python_api.Error, 'cannot extract "demo-0.1-py3-none-any.whl": BadZipFile'):
                python_api.extract([wheel], Path(tmp) / "site")

    def test_no_wheel_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(Path(tmp) / "p", {"demo/__init__.py": ""})
            done = subprocess.CompletedProcess([], 0)
            with mock.patch("subprocess.run", return_value=done), self.assertRaisesRegex(python_api.Error, "no wheel"):
                python_api.build(root, Path(tmp) / "wheels")

    def test_syntax_error_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write(
                Path(tmp),
                {
                    "demo/__init__.py": "x = 1\n",
                    "demo/broken.py": "::error::x(\n",
                    "demo/templates/page.py": "{{ not python }}\n",
                    "demo/not-a-module.py": "{{ not python }}\n",
                },
            )
            with self.assertRaises(python_api.Error) as caught:
                python_api.check_sources(site, ["demo"])
            message = str(caught.exception)
            self.assertIn('"demo/broken.py"', message)
            (site / "demo/broken.py").unlink()
            python_api.check_sources(site, ["demo"])

    def test_quote_neutralises_workflow_commands(self):
        quoted = python_api.quote("::error::x\n##[error]y")
        self.assertFalse(quoted.startswith("::"))
        self.assertNotIn("#", quoted)
        self.assertNotIn("\n", quoted)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_public_entry_and_private_absent(self):
        root = project(self.tmp / "p", {"demo/__init__.py": "def open(x: int = 5) -> int: ...\ndef _helper(): ...\n"})
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        items = json.loads((self.tmp / "out.json").read_text(encoding="utf-8"))
        self.assertIn(
            {"unit": "demo", "parent": "demo", "kind": "function", "name": "open", "signature": "(x: int = 5) -> int"},
            items,
        )
        self.assertFalse([item for item in items if item["name"] == "_helper"])
        keys = [(item["unit"], item["parent"], item["kind"], item["name"]) for item in items]
        self.assertEqual(keys, sorted(keys))
        self.assertIn("api snapshot (python): 2 entries from 1 wheel(s), units: \"demo\"", result.stdout)

    def test_two_runs_byte_identical(self):
        root = project(self.tmp / "p", RICH)
        first = run(root, self.tmp / "one.json")
        second = run(root, self.tmp / "two.json")
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
        data = (self.tmp / "one.json").read_bytes()
        self.assertEqual(data, (self.tmp / "two.json").read_bytes())
        self.assertNotIn(str(self.tmp).encode(), data)
        self.assertNotIn(tempfile.gettempdir().encode(), data)
        self.assertNotIn(b"Path(", data)

    def test_docstring_and_move_keep_output(self):
        before = project(
            self.tmp / "before",
            {
                "demo/__init__.py": 'from ._a import open\n__all__ = ["open"]\n',
                "demo/_a.py": 'def open(x: int = 5) -> int:\n    """Old docs."""\n    return x\n',
            },
        )
        after = project(
            self.tmp / "after",
            {
                "demo/__init__.py": 'from ._b import open\n__all__ = ["open"]\n',
                "demo/_a.py": "",
                "demo/_b.py": '\n\ndef open(x: int = 5) -> int:\n    """New docs,\n    longer."""\n    return x\n',
            },
        )
        self.assertEqual(run(before, self.tmp / "before.json").returncode, 0)
        self.assertEqual(run(after, self.tmp / "after.json").returncode, 0)
        self.assertEqual((self.tmp / "before.json").read_bytes(), (self.tmp / "after.json").read_bytes())
        result = compare(self.tmp / "before.json", self.tmp / "after.json", self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_overload_change_lists_one_changed_entry(self):
        source = """\
            from typing import overload
            @overload
            def open(x: int) -> int: ...
            @overload
            def open(x: str) -> {ret}: ...
            def open(x): return x
            def other() -> None: ...
            """
        old = project(self.tmp / "old", {"demo/__init__.py": source.format(ret="str")})
        new = project(self.tmp / "new", {"demo/__init__.py": source.format(ret="bytes")})
        self.assertEqual(run(old, self.tmp / "old.json").returncode, 0)
        self.assertEqual(run(new, self.tmp / "new.json").returncode, 0)
        for file in ("old.json", "new.json"):
            items = json.loads((self.tmp / file).read_text(encoding="utf-8"))
            keys = [(item["unit"], item["parent"], item["kind"], item["name"]) for item in items]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual([item["name"] for item in items].count("open"), 1)
        result = compare(self.tmp / "old.json", self.tmp / "new.json", self.tmp)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("0 added, 0 removed, 1 changed", result.stdout)
        changed = [line for line in result.stdout.splitlines() if line.startswith("变 ")]
        self.assertEqual(len(changed), 1)
        self.assertIn('"name": "open"', changed[0])

    def test_workspace_builds_all_members(self):
        root = self.tmp / "ws"
        write(root, {"pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n'})
        project(root / "packages" / "alpha", {"alpha/__init__.py": "def a() -> None: ...\n"}, name="alpha")
        project(root / "packages" / "beta", {"beta/__init__.py": "def b() -> None: ...\n"}, name="beta")
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        items = json.loads((self.tmp / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted({item["unit"] for item in items}), ["alpha", "beta"])

    def test_requirements_only_project_exits_2(self):
        root = write(self.tmp / "p", {"requirements.txt": "requests\n", "app.py": "def main(): ...\n"})
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no pyproject.toml or setup.py", result.stderr)
        self.assertFalse((self.tmp / "out.json").exists())

    def test_build_failure_exits_2(self):
        root = project(self.tmp / "p", {"other/__init__.py": ""})
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("api snapshot (python): uv build failed with exit code", result.stderr)
        self.assertFalse((self.tmp / "out.json").exists())

    def test_syntax_error_in_wheel_exits_2(self):
        warning = 'x = "::error::pwned \\d"\n'
        root = project(self.tmp / "p", {"demo/__init__.py": warning, "demo/broken.py": "def f(:\n"})
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn('"demo/broken.py": cannot parse', result.stderr)
        self.assertNotIn("::", result.stderr.replace("api snapshot (python):", ""))
        self.assertFalse((self.tmp / "out.json").exists())

    def test_working_directory_is_under_the_repo_root(self):
        root = self.tmp / "repo"
        project(root / "svc", {"demo/__init__.py": "def open() -> None: ...\n"})
        result = run(root, self.tmp / "out.json", "svc")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 wheel(s)", result.stdout)

    def test_unexpected_error_exits_2(self):
        root = project(self.tmp / "p", {"demo/__init__.py": ""})
        stderr = io.StringIO()
        with mock.patch("subprocess.run", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(stderr):
            code = python_api.cli([str(root), ".", str(self.tmp / "out.json")])
        self.assertEqual(code, 2)
        self.assertIn('api snapshot (python): RuntimeError: "boom"', stderr.getvalue())

    def test_usage_exits_2(self):
        for argv in ([], [str(self.tmp), str(self.tmp / "out.json")]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(python_api.cli(argv), 2)
                self.assertIn("usage: python_api.py REPO_ROOT WORKING_DIRECTORY OUTPUT", stderr.getvalue())


DRIVER = {
    "demo/__init__.py": "",
    "demo/driver.py": "def read() -> int: ...\n",
    "demo/net.py": "def send() -> None: ...\n",
}


class ScopeCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def generate(self, name: str, files: dict[str, str], scope: str | None, **kwargs) -> Path:
        """Generate from a project whose repo-guards.toml sets scope (a TOML array) or has no file for None."""
        output = self.tmp / f"{name}.json"
        root = project(self.tmp / name, files)
        if scope is not None:
            settings(root, scope_setting(scope))
        result = run(root, output, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def test_no_scope_output_unchanged(self):
        unset = self.generate("unset", RICH, None).read_bytes()
        self.assertEqual(self.generate("whole_unit", RICH, '["demo"]').read_bytes(), unset)
        # The variable WDRC-851 passed the scope in is no longer read.
        env = {"API_SNAPSHOT_SCOPE": '["demo.sub"]'}
        self.assertEqual(self.generate("old_env", RICH, None, env=env).read_bytes(), unset)

    def test_new_public_item_outside_scope_passes(self):
        scope = '["demo.driver"]'
        snapshot = self.generate("before", DRIVER, scope)
        items = json.loads(snapshot.read_text(encoding="utf-8"))
        frozen = {(item["parent"], item["name"]) for item in items}
        self.assertEqual(frozen, {("demo", "driver"), ("demo.driver", "read")})
        grown = {**DRIVER, "demo/net.py": DRIVER["demo/net.py"] + "def recv() -> bytes: ...\n"}
        result = compare(snapshot, self.generate("scoped", grown, scope), self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Without the scope the same addition fails: the scope is what lets it through.
        result = compare(self.generate("all_before", DRIVER, None), self.generate("all_after", grown, None), self.tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 added, 0 removed, 0 changed", result.stdout)

    def test_new_public_item_inside_scope_fails(self):
        scope = '["demo.driver"]'
        snapshot = self.generate("before", DRIVER, scope)
        grown = {**DRIVER, "demo/driver.py": DRIVER["demo/driver.py"] + "def write(data: bytes) -> None: ...\n"}
        result = compare(snapshot, self.generate("after", grown, scope), self.tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 added, 0 removed, 0 changed", result.stdout)
        self.assertIn('"name": "write"', result.stdout)

    def test_scope_named_in_log(self):
        root = settings(project(self.tmp / "p", DRIVER), scope_setting('["demo.driver", "demo.net"]'))
        result = run(root, self.tmp / "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('units: "demo", scope: "demo.driver", "demo.net"', result.stdout)

    def test_bad_scope_exits_2_before_building(self):
        root = settings(project(self.tmp / "p", DRIVER), scope_setting('["demo-driver"]'))
        with mock.patch("subprocess.run") as build, contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = python_api.cli([str(root), ".", str(self.tmp / "out.json")])
        self.assertEqual(code, 2)
        build.assert_not_called()
        self.assertIn(
            'api snapshot (python): [project."."] api-snapshot-scope.python: "demo-driver": must be a dotted module name',
            stderr.getvalue(),
        )
        self.assertFalse((self.tmp / "out.json").exists())


class PinTest(unittest.TestCase):
    def test_requirements_pinned_with_hashes(self):
        self.assertEqual((HERE / "requirements.in").read_text(encoding="utf-8"), "griffelib==2.4.1\n")
        text = (HERE / "requirements.txt").read_text(encoding="utf-8")
        blocks = re.findall(r"^(\S+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", text, re.MULTILINE)
        requirements = [line for line in text.splitlines() if line and not line.startswith((" ", "#"))]
        self.assertEqual(len(blocks), len(requirements))
        for name, _ in blocks:
            self.assertRegex(name, r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$")
        self.assertIn("griffelib==2.4.1", [name for name, _ in blocks])

    def test_workflow_installs_with_hashes(self):
        self.assertIn('--require-hashes -r "$api/requirements.txt"', generate_step())


def generate_step() -> str:
    workflow = (HERE.parents[3] / ".github" / "workflows" / "python.yml").read_text(encoding="utf-8")
    return workflow.split("      - name: Generate\n", 1)[1].split("      - name: Compare\n", 1)[0]


class WorkflowTest(unittest.TestCase):
    def test_generate_passes_repo_root_and_working_directory(self):
        step = generate_step()
        self.assertIn(
            'env -u UV_NO_CONFIG "$RUNNER_TEMP/api-venv/bin/python" -I "$api/python_api.py" '
            '"$GITHUB_WORKSPACE/repo" "$PROJECT_DIR" "$RUNNER_TEMP/api-entries.json"',
            step,
        )
        self.assertNotIn("API_SNAPSHOT_SCOPE", step)
        self.assertNotIn("not wired up", step)


if __name__ == "__main__":
    unittest.main()
