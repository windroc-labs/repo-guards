import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[4] / "guards/ci-checks/python/layer-rules"
SCRIPT = SOURCE / "layer_rules.py"
PREFIX = "layer rules (python): "

FORBIDDEN_TOML = """\
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "core does not import ui"
type = "forbidden"
source_modules = ["app.core"]
forbidden_modules = ["app.ui"]
"""
FORBIDDEN_INI = """\
[importlinter]
root_package = app

[importlinter:contract:core-ui]
name = core does not import ui (ini)
type = forbidden
source_modules = app.core
forbidden_modules = app.ui
"""
# Every module of the sample project records that it ran; the check must never run any of them.
RAN = "open({sentinel!r}, 'a').write(__name__ + '\\n')\n"


class LayerRules(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name) / "project"
        self.project.mkdir()
        self.sentinel = Path(tmp.name) / "ran.txt"

    def write(self, files: dict[str, str]) -> None:
        for relative, text in files.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding="utf-8")

    def package(self, base: str, core_imports_ui: bool) -> None:
        ran = RAN.format(sentinel=str(self.sentinel))
        core = ran + ("from app import ui\n" if core_imports_ui else "")
        self.write(
            {
                f"{base}app/__init__.py": ran,
                f"{base}app/ui/__init__.py": ran + "from app.core import helper\n",
                f"{base}app/core/__init__.py": core,
                f"{base}app/core/helper.py": ran,
            }
        )

    def run_check(self, *source_dirs: str) -> subprocess.CompletedProcess[str]:
        # cwd is the project, as in the workflow, so files there could shadow modules if the cwd were on sys.path.
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(self.project), *source_dirs],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_nothing_ran(self) -> None:
        self.assertFalse(self.sentinel.exists(), self.sentinel.read_text() if self.sentinel.exists() else "")

    def test_no_config_skips_with_one_line(self):
        self.package("", core_imports_ui=True)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"{PREFIX}no import-linter config in .importlinter or pyproject.toml [tool.importlinter]; skipped\n",
        )
        self.assert_nothing_ran()

    def test_pyproject_without_the_table_skips(self):
        self.package("", core_imports_ui=True)
        self.write({"pyproject.toml": '[project]\nname = "app"\n\n[tool.ruff]\nline-length = 100\n'})
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout)

    def test_violation_fails(self):
        self.package("", core_imports_ui=True)
        self.write({"pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("app.core -> app.ui", result.stdout)
        self.assert_nothing_ran()

    def test_no_violation_passes(self):
        self.package("", core_imports_ui=False)
        self.write({"pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 kept, 0 broken", result.stdout)
        self.assertIn(f"{PREFIX}config pyproject.toml", result.stdout)
        self.assert_nothing_ran()

    def test_importlinter_ini_is_read(self):
        self.package("", core_imports_ui=True)
        self.write({".importlinter": FORBIDDEN_INI})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("core does not import ui (ini)", result.stdout)

    def test_importlinter_wins_over_pyproject(self):
        # import-linter itself reads INI files before pyproject.toml.
        self.package("", core_imports_ui=True)
        self.write({".importlinter": FORBIDDEN_INI, "pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"{PREFIX}config .importlinter", result.stdout)
        self.assertIn("(ini)", result.stdout)

    def test_src_layout_found_through_source_dirs(self):
        self.package("src/", core_imports_ui=True)
        self.write({"pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("app.core -> app.ui", result.stdout)
        self.assert_nothing_ran()

    def test_missing_source_dir_is_ignored(self):
        self.package("", core_imports_ui=False)
        self.write({"pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check("src")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_not_found_fails(self):
        self.package("src/", core_imports_ui=False)
        self.write({"pyproject.toml": FORBIDDEN_TOML})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Could not find package 'app'", result.stdout + result.stderr)

    def test_project_files_shadowing_modules_never_run(self):
        ran = RAN.format(sentinel=str(self.sentinel))
        self.package("", core_imports_ui=False)
        self.write({"pyproject.toml": FORBIDDEN_TOML, "tomllib.py": ran, "rich.py": ran, "json/__init__.py": ran})
        result = self.run_check(".")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_nothing_ran()

    def test_contract_type_from_the_project_is_not_run(self):
        self.package("", core_imports_ui=False)
        self.write(
            {
                "pyproject.toml": FORBIDDEN_TOML.replace(
                    'root_package = "app"', 'root_package = "app"\ncontract_types = ["mine: app.core.helper.Mine"]'
                )
            }
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_nothing_ran()

    def test_subpackage_symlinked_outside_the_project_is_not_run(self):
        ran = RAN.format(sentinel=str(self.sentinel))
        shared = self.project.parent / "shared"
        for relative in ("core/__init__.py", "core/helper.py"):
            (shared / relative).parent.mkdir(parents=True, exist_ok=True)
            (shared / relative).write_text(ran, encoding="utf-8")
        self.write({"app/__init__.py": ran, "app/ui/__init__.py": ran})
        (self.project / "app/core").symlink_to(shared / "core", target_is_directory=True)
        self.write(
            {
                "pyproject.toml": FORBIDDEN_TOML.replace(
                    'root_package = "app"',
                    'root_package = "app"\ncontract_types = ["mine: app.core.helper.Mine"]',
                )
            }
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_nothing_ran()

    def test_package_found_through_a_parent_source_dir_is_not_run(self):
        ran = RAN.format(sentinel=str(self.sentinel))
        outside = self.project.parent / "app"
        for relative in ("__init__.py", "core/__init__.py", "core/helper.py", "ui/__init__.py"):
            (outside / relative).parent.mkdir(parents=True, exist_ok=True)
            (outside / relative).write_text(ran, encoding="utf-8")
        self.write(
            {
                "pyproject.toml": FORBIDDEN_TOML.replace(
                    'root_package = "app"',
                    'root_package = "app"\ncontract_types = ["mine: app.core.helper.Mine"]',
                )
            }
        )
        result = self.run_check("..")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_nothing_ran()

    def test_dotted_root_package_does_not_run_its_parent(self):
        ran = RAN.format(sentinel=str(self.sentinel))
        self.write(
            {
                "ns/__init__.py": ran,
                "ns/app/__init__.py": ran,
                "pyproject.toml": FORBIDDEN_TOML.replace('"app', '"ns.app'),
            }
        )
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_nothing_ran()

    def test_config_without_contracts_fails(self):
        self.package("", core_imports_ui=False)
        self.write({"pyproject.toml": '[tool.importlinter]\nroot_package = "app"\n'})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No contracts found", result.stdout + result.stderr)

    def test_importlinter_without_its_section_fails(self):
        self.package("", core_imports_ui=False)
        self.write({".importlinter": "[other]\nkey = value\n"})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_unreadable_pyproject_exits_2(self):
        self.write({"pyproject.toml": "[tool.importlinter\n"})
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(f"{PREFIX}cannot read pyproject.toml", result.stderr)

    def assert_commands_fenced(self, result: subprocess.CompletedProcess[str]) -> None:
        # Every line that could act as a workflow command sits inside the stop-commands block; stderr shares stdout.
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("::stop-commands::"))
        token = lines[start].removeprefix("::stop-commands::")
        self.assertGreaterEqual(len(token), 32)
        end = lines.index(f"::{token}::")
        self.assertEqual(end, len(lines) - 1, result.stdout)
        self.assertTrue(all(i in range(start, end + 1) for i, line in enumerate(lines) if line.startswith("::")))

    def test_tool_output_cannot_issue_workflow_commands(self):
        # A syntax error echoes the PR's source line.
        self.package("", core_imports_ui=False)
        self.write({"pyproject.toml": FORBIDDEN_TOML, "app/core/bad.py": "::error::x = (\n"})
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_commands_fenced(result)
        self.assertIn("::error::x = (", result.stdout)

    def test_tool_crash_stays_inside_the_block(self):
        # rich reads the contract name as markup and raises after lint_imports' own error handling.
        self.package("", core_imports_ui=False)
        name = 'name = "[/x\\n::error title=forged::injected\\n]"'
        self.write({"pyproject.toml": FORBIDDEN_TOML.replace('name = "core does not import ui"', name)})
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assert_commands_fenced(result)
        self.assertIn("::error title=forged::injected", result.stdout)

    def test_wrong_arguments_exit_2(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: layer_rules.py PROJECT [SOURCE_DIR ...]", result.stderr)


class Requirements(unittest.TestCase):
    def test_pinned_with_hashes(self):
        self.assertEqual((SOURCE / "requirements.in").read_text(encoding="utf-8"), "import-linter==2.16\n")
        text = (SOURCE / "requirements.txt").read_text(encoding="utf-8")
        blocks = re.findall(r"^(\S+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", text, re.MULTILINE)
        requirements = [line for line in text.splitlines() if line and not line.startswith((" ", "#"))]
        self.assertEqual(len(blocks), len(requirements))
        for name, _ in blocks:
            self.assertRegex(name, r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$")
        self.assertIn("import-linter==2.16", [name for name, _ in blocks])


class Workflow(unittest.TestCase):
    def test_step_runs_the_wrapper_isolated_from_the_project(self):
        workflow = (SOURCE.parents[3] / ".github" / "workflows" / "python.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Layer rules\n", 1)[1].split("      - name: ", 1)[0]
        self.assertIn('UV_NO_CONFIG: "1"', step)
        self.assertIn('--require-hashes -r "$rules/requirements.txt"', step)
        self.assertIn('-I "$rules/layer_rules.py" . $SOURCE_DIRS', step)


if __name__ == "__main__":
    unittest.main()
