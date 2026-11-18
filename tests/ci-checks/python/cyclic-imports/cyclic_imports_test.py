import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "guards/ci-checks/python/cyclic-imports/cyclic_imports.py"
PREFIX = "cyclic imports (python): "
# Every module of the sample project records that it ran; the check must never run any of them.
RAN = "open({sentinel!r}, 'a').write(__name__ + '\\n')\n"
HOOK = "open({sentinel!r}, 'a').write('config\\n')"


class CyclicImports(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name) / "project"
        self.project.mkdir()
        self.sentinel = Path(tmp.name) / "ran.txt"
        self.ran = RAN.format(sentinel=str(self.sentinel))
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)

    def write(self, files: dict[str, str], track: bool = True) -> None:
        for relative, text in files.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if track:
            self.git("add", "-A")

    def cycle(self, base: str = "src/", package: str = "app") -> None:
        dotted = package.replace("/", ".")
        self.write(
            {
                f"{base}{package}/__init__.py": self.ran,
                f"{base}{package}/a.py": self.ran + f"from {dotted} import b\n",
                f"{base}{package}/b.py": self.ran + f"from {dotted} import a\n",
                f"{base}{package}/c.py": self.ran + f"import {dotted}.a\n",
            }
        )

    def run_check(self, *source_dirs: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(self.project), *source_dirs],
            capture_output=True,
            text=True,
            cwd=cwd or self.project,
            check=False,
        )
        self.assertFalse(self.sentinel.exists(), "the check ran project code:\n" + result.stdout + result.stderr)
        return result

    def assert_cycle(self, result: subprocess.CompletedProcess[str], *modules: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("Cyclic import (")]
        self.assertEqual(len(lines), 1, result.stdout)
        for module in modules:
            self.assertIn(module, lines[0])

    def test_a_cycle_fails_and_names_its_modules(self):
        self.cycle()
        self.assert_cycle(self.run_check("src"), "app.a", "app.b")

    def test_no_cycle_passes(self):
        self.write({"src/app/__init__.py": "", "src/app/a.py": "from app import b\n", "src/app/b.py": "import os\n"})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{PREFIX}3 tracked .py files; module names from: src", result.stdout)

    def test_flat_layout_and_a_package_as_its_own_source_dir(self):
        self.cycle(base="")
        self.assert_cycle(self.run_check("."), "app.a", "app.b")
        self.assert_cycle(self.run_check("app"), "app.a", "app.b")

    def test_namespace_package_keeps_its_full_name(self):
        self.cycle(package="ns/app")
        (self.project / "src/ns/app/__init__.py").unlink()
        self.git("add", "-A")
        self.assert_cycle(self.run_check("src"), "ns.app.a", "ns.app.b")

    def test_several_source_dirs_and_missing_ones_are_skipped(self):
        self.cycle()
        self.write({"tools/run.py": "import os\n"})
        result = self.run_check("src", "tools", "nope")
        self.assert_cycle(result, "app.a", "app.b")
        self.assertIn("module names from: src, tools\n", result.stdout)

    def test_files_outside_the_source_dirs_are_checked_too(self):
        self.write(
            {
                "src/app/__init__.py": "",
                "src/app/core.py": "import cli\n",
                "cli.py": "from app import core\n",
                "tests/test_x.py": "import helpers\n",
                "tests/helpers.py": "import test_x\n",
            }
        )
        for source_dirs in (["src"], []):
            with self.subTest(source_dirs=source_dirs):
                result = self.run_check(*source_dirs)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Cyclic import (app.core -> cli)", result.stdout)
                self.assertIn("Cyclic import (helpers -> test_x)", result.stdout)

    def test_a_package_above_the_source_dir_names_modules_from_the_deepest_root(self):
        self.cycle()
        self.write({"manage.py": "import app\n"})
        self.assert_cycle(self.run_check(".", "src"), "app.a", "app.b")
        self.assert_cycle(self.run_check("src", "."), "app.a", "app.b")

    def test_type_checking_imports_are_not_a_cycle(self):
        guarded = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app import {}\n"
        self.write(
            {
                "src/app/__init__.py": "",
                "src/app/a.py": guarded.format("b"),
                "src/app/b.py": guarded.format("a"),
            }
        )
        self.assertEqual(self.run_check("src").returncode, 0)

    def test_an_import_inside_a_function_still_counts(self):
        self.write(
            {
                "src/app/__init__.py": "",
                "src/app/a.py": "from app import b\n",
                "src/app/b.py": "def f():\n    from app import a\n    return a\n",
            }
        )
        self.assert_cycle(self.run_check("src"), "app.a", "app.b")

    def test_only_tracked_files_are_checked(self):
        self.write({"src/app/__init__.py": "", "src/app/a.py": "import os\n"})
        self.write({".venv/lib/x/__init__.py": "from x import y\n", ".venv/lib/x/y.py": "import x\n"}, track=False)
        self.write({"src/app/b.py": "from app import c\n", "src/app/c.py": "from app import b\n"}, track=False)
        result = self.run_check("src", ".venv/lib")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_projects_pylint_config_is_ignored(self):
        self.cycle()
        hook = HOOK.format(sentinel=str(self.sentinel))
        ini = f"[MAIN]\ninit-hook={hook}\nload-plugins=app.a\n[MESSAGES CONTROL]\ndisable=cyclic-import\n"
        toml = (
            f'[tool.pylint.main]\ninit-hook = "{hook}"\nload-plugins = ["app.a"]\n'
            '[tool.pylint."messages control"]\ndisable = ["cyclic-import"]\n'
        )
        self.write(
            {
                "pyproject.toml": toml.replace("'", "\\'"),
                ".pylintrc": ini,
                "pylintrc": ini,
                "setup.cfg": ini.replace("[MAIN]", "[pylint.MAIN]"),
                "tox.ini": ini.replace("[MAIN]", "[pylint.MAIN]"),
            }
        )
        self.assert_cycle(self.run_check("src"), "app.a", "app.b")
        self.assert_cycle(self.run_check("src", cwd=self.project / "src"), "app.a", "app.b")

    def test_pylint_notes_on_the_projects_pragmas_are_not_errors(self):
        self.cycle()
        self.write({"src/app/c.py": "x = 1  # pylint: disable=bad-continuation,missing-param-doc,no-such-thing\n"})
        self.assert_cycle(self.run_check("src"), "app.a", "app.b")

    def test_two_errors_in_one_file_count_as_one_file(self):
        self.write({"src/app/__init__.py": "", "src/app/e.py": "def f(:\n", "src/app/f.py": "x = (\n"})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 2)
        self.assertIn("2 files could not be read", result.stdout)

    def test_a_symlink_to_code_elsewhere_in_the_checkout_never_runs(self):
        # The project is a subdirectory of the checkout; the package it links to lies outside the project.
        project = self.project / "py"
        ran = self.ran
        self.write(
            {
                "other/winreg/__init__.py": ran,
                "other/winreg/nothing.py": ran,
                "py/src/a.py": "import winreg.nothing\nimport json\n",
                "py/src/b.py": "import a\n",
            },
            track=False,
        )
        (project / "src/winreg").symlink_to("../../other/winreg")
        (project / "src/json.py").symlink_to("../../other/winreg/__init__.py")
        self.git("add", "-A")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(project), "src"], capture_output=True, text=True, cwd=project, check=False
        )
        self.assertFalse(self.sentinel.exists(), result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_project_modules_never_run_even_when_they_shadow_the_stdlib(self):
        names = ["json", "difflib", "tokenize", "pickle", "textwrap", "argparse", "platform", "copy", "inspect"]
        self.write({f"src/{name}.py": self.ran for name in names})
        self.cycle()
        self.assert_cycle(self.run_check("src"), "app.a", "app.b")

    def test_pylint_disable_comments_still_silence_it(self):
        # They are inline exemptions, which the Inline exemptions step makes the project register.
        self.cycle()
        self.write({"src/app/a.py": "from app import b  # pylint: disable=cyclic-import\n"})
        self.assertEqual(self.run_check("src").returncode, 0)

    def test_a_syntax_error_means_the_check_could_not_run(self):
        self.cycle()
        self.write({"src/app/broken.py": "def f(:\n"})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("src/app/broken.py:1: syntax-error", result.stdout)

    def test_no_python_files_passes(self):
        self.write({"src/README.md": "x\n"})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"{PREFIX}no tracked .py files; nothing to check", result.stdout)

    def test_pylint_stopping_or_crashing_is_an_error_inside_the_block(self):
        self.cycle()
        for effect in ("SystemExit(1)", "RuntimeError('boom')"):
            code = PATCHED_RUN.format(script_dir=str(SCRIPT.parent), effect=effect, project=str(self.project))
            with self.subTest(effect=effect):
                result = subprocess.run(
                    [sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=self.project, check=False
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                block = re.search(r"^::stop-commands::(\w+)$\n(.*)^::\1::$", result.stdout, re.S | re.M)
                self.assertIsNotNone(block, result.stdout)
                self.assertIn("pylint exited with 1" if "Exit" in effect else "RuntimeError: boom", block[2])

    def test_not_a_git_checkout_is_an_error(self):
        self.write({"src/app/__init__.py": ""})
        subprocess.run(["rm", "-rf", str(self.project / ".git")], check=True)
        self.assertEqual(self.run_check("src").returncode, 2)

    def test_usage(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: cyclic_imports.py PROJECT [SOURCE_DIR ...]", result.stderr)

    def test_report_is_inside_a_stop_commands_block(self):
        self.cycle()
        self.write({"src/app/broken.py": "def f(:\n"})
        result = self.run_check("src")
        match = re.search(r"^::stop-commands::(\w+)$\n(.*)^::\1::$", result.stdout, re.S | re.M)
        self.assertIsNotNone(match, result.stdout)
        self.assertIn("syntax-error", match[2])
        self.assertEqual(result.stderr, "")


# Runs main() with pylint's Run replaced, to reach the paths a real run of the pinned pylint does not take.
PATCHED_RUN = """
import sys
from unittest import mock
sys.path.insert(0, {script_dir!r})
import cyclic_imports
with mock.patch.object(cyclic_imports, "Run", side_effect={effect}):
    sys.exit(cyclic_imports.main([{project!r}, "src"]))
"""

GUARD = """
import importlib, sys
from importlib.machinery import PathFinder
from pathlib import Path
sys.path.insert(0, {script_dir!r})
import cyclic_imports
project = Path({project!r})
sys.meta_path.insert(sys.meta_path.index(PathFinder), cyclic_imports.ProjectGuard([project]))
sys.path.insert(0, str(project))
print(Path(importlib.import_module("colorsys").__file__).parent != project)
for name in ("mine", "pkg.sub", "linked.sub", "linked_module"):
    try:
        importlib.import_module(name)
    except ImportError:
        print("blocked", name)
"""


class Guard(unittest.TestCase):
    def test_project_files_first_on_sys_path_neither_shadow_nor_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, sentinel = Path(tmp) / "project", Path(tmp) / "ran.txt"
            ran = RAN.format(sentinel=str(sentinel))
            for relative in ("colorsys.py", "mine.py", "pkg/__init__.py", "pkg/sub.py", "../elsewhere/__init__.py", "../elsewhere/sub.py"):
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text(ran, encoding="utf-8")
            # Tracked symlinks from the project to code outside it are the project's code too.
            (project / "linked").symlink_to("../elsewhere")
            (project / "linked_module.py").symlink_to("../elsewhere/__init__.py")
            code = GUARD.format(script_dir=str(SCRIPT.parent), project=str(project))
            result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
            blocked = ["blocked mine", "blocked pkg.sub", "blocked linked.sub", "blocked linked_module"]
            self.assertEqual(result.stdout.split("\n"), ["True", *blocked, ""], result.stderr)
            self.assertFalse(sentinel.exists())


class PragmaParity(unittest.TestCase):
    """The inline-exemptions scanner reads pylint pragmas without pylint; this venv has the pinned pylint to compare."""

    @classmethod
    def setUpClass(cls):
        from pylint.lint import PyLinter
        from pylint.utils import pragma_parser

        sys.path.insert(0, str(SCRIPT.parents[1] / "inline-exemptions"))
        import exemptions

        cls.exemptions, cls.parser = exemptions, pragma_parser
        cls.linter = PyLinter()
        cls.linter.load_default_plugins()

    def disables_cycles(self, name: str) -> bool:
        messages = self.linter._get_messages_to_set(name, enable=False, ignore_unknown=True)
        return any(message.symbol == "cyclic-import" for message in messages)

    def pylint_reads(self, comment: str) -> tuple[list[tuple[str, bool]], bool]:
        match = self.parser.OPTION_PO.search(comment)
        found: list[tuple[str, bool]] = []
        if match is None:
            return found, False
        try:
            for pragma in self.parser.parse_pragma(match.group(2)):
                if pragma.action in {"disable-all", "skip-file"}:
                    return found + [(pragma.action, True)], True
                for message in pragma.messages if pragma.action.startswith("disable") else []:
                    if (pragma.action, message) == ("disable", "all"):
                        return found + [(message, True)], True
                    if self.disables_cycles(message):
                        found.append((message, False))
        except self.parser.PragmaParserError:
            pass
        return found, False

    def test_every_name_that_disables_cyclic_import(self):
        names = {"all", "ALL", "refactor", "REFACTOR", *self.linter._checkers}
        for message in self.linter.msgs_store.messages:
            for name in (message.msgid, message.symbol, *(n for pair in message.old_names for n in pair)):
                names |= {name, name.lower(), name.upper()}
        for category in "CREIWF":
            names |= {category, category.lower()}
        for name in sorted(names | {n.upper() for n in self.linter._checkers}):
            with self.subTest(name=name):
                self.assertEqual(self.exemptions.disables_cycles(name), self.disables_cycles(name))

    def test_comments_are_read_like_pylint(self):
        parts = [
            "#", " ", "pylint:", "pylint", ":", "disable", "enable", "disable-next", "disable-msg", "enable-msg",
            "disable-all", "skip-file", "=", ",", ";", "all", "R", "r", "R0401", "cyclic-import", "imports",
            "too-many-arguments", "x", "ab", "C", "#pylint:", "é",
        ]
        rng = random.Random(1066)
        for _ in range(20000):
            comment = "#" + "".join(rng.choice(parts) for _ in range(rng.randint(1, 10)))
            self.assertEqual(self.exemptions.pylint_rules(comment), self.pylint_reads(comment), comment)


if __name__ == "__main__":
    unittest.main()
