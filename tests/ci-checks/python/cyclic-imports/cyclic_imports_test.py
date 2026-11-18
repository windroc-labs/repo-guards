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
        self.assertIn(f"{PREFIX}3 files in src", result.stdout)

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
        self.assertIn("in src, tools", result.stdout)

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

    def test_no_source_dir_is_an_error(self):
        result = self.run_check("src", "lib")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"{PREFIX}no source dir found: src, lib", result.stderr)

    def test_no_python_files_passes(self):
        self.write({"src/README.md": "x\n"})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"{PREFIX}no tracked .py files in src; nothing to check", result.stdout)

    def test_not_a_git_checkout_is_an_error(self):
        self.write({"src/app/__init__.py": ""})
        subprocess.run(["rm", "-rf", str(self.project / ".git")], check=True)
        self.assertEqual(self.run_check("src").returncode, 2)

    def test_usage(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: cyclic_imports.py PROJECT SOURCE_DIR [SOURCE_DIR ...]", result.stderr)

    def test_report_is_inside_a_stop_commands_block(self):
        self.cycle()
        self.write({"src/app/broken.py": "def f(:\n"})
        result = self.run_check("src")
        match = re.search(r"^::stop-commands::(\w+)$\n(.*)^::\1::$", result.stdout, re.S | re.M)
        self.assertIsNotNone(match, result.stdout)
        self.assertIn("syntax-error", match[2])
        self.assertEqual(result.stderr, "")


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
for name in ("mine", "pkg.sub"):
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
            for relative in ("colorsys.py", "mine.py", "pkg/__init__.py", "pkg/sub.py"):
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text(ran, encoding="utf-8")
            code = GUARD.format(script_dir=str(SCRIPT.parent), project=str(project))
            result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
            self.assertEqual(result.stdout.split("\n"), ["True", "blocked mine", "blocked pkg.sub", ""], result.stderr)
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
