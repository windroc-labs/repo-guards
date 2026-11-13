import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from pyproject_config import main

SHARED = Path(__file__).resolve().parent.parent / "pyproject.toml"

PYPROJECT = """\
[project]
name = "demo"

[dependency-groups]
dev = ["ruff==0.1.0", "pytest==8.0.0", "pytest-cov==5.0.0", "coverage[toml]==7.0.0"]

[tool.pyright]
typeCheckingMode = "strict"
"""


class Run(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.pyproject = self.tmp / "pyproject.toml"
        self.pyproject.write_text(PYPROJECT)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class Pin(Run):
    def test_prints_the_first_dev_entry_with_the_prefix(self):
        self.assertEqual(self.run_main("pin", str(self.pyproject), "pytest=="), (0, "pytest==8.0.0\n", ""))

    def test_extras_are_part_of_the_prefix(self):
        self.assertEqual(self.run_main("pin", str(self.pyproject), "coverage[toml]==")[1], "coverage[toml]==7.0.0\n")

    def test_no_match_is_an_error(self):
        code, out, err = self.run_main("pin", str(self.pyproject), "deptry==")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("deptry==", err)

    def test_the_shared_pyproject_pins_every_tool_the_workflow_asks_for(self):
        for prefix in ("deptry==", "coverage[toml]==", "pytest=="):
            code, out, _ = self.run_main("pin", str(SHARED), prefix)
            self.assertEqual(code, 0, prefix)
            self.assertTrue(out.startswith(prefix), out)


class ProjectName(Run):
    def test_prints_the_project_name(self):
        self.assertEqual(self.run_main("project-name", str(self.pyproject)), (0, "demo\n", ""))

    def test_no_project_table_prints_an_empty_line(self):
        self.pyproject.write_text("[tool.ruff]\n")
        self.assertEqual(self.run_main("project-name", str(self.pyproject)), (0, "\n", ""))

    def test_a_project_without_a_name_prints_an_empty_line(self):
        self.pyproject.write_text("[project]\nversion = '1'\n")
        self.assertEqual(self.run_main("project-name", str(self.pyproject)), (0, "\n", ""))


class PyrightConfig(Run):
    def test_writes_the_pyright_table_with_absolute_extra_paths(self):
        out = self.tmp / "pyrightconfig.json"
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(self.run_main("pyright-config", str(self.pyproject), str(out), "src", "lib/pkg"), (0, "", ""))
        root = self.tmp.resolve()
        self.assertEqual(
            json.loads(out.read_text()),
            {"typeCheckingMode": "strict", "extraPaths": [str(root / "src"), str(root / "lib/pkg")]},
        )

    def test_no_dirs_gives_empty_extra_paths(self):
        out = self.tmp / "pyrightconfig.json"
        self.assertEqual(self.run_main("pyright-config", str(self.pyproject), str(out))[0], 0)
        self.assertEqual(json.loads(out.read_text())["extraPaths"], [])


class Usage(Run):
    def test_unknown_command_or_missing_arguments_are_usage_errors(self):
        for argv in ([], ["nope"], ["pin", str(self.pyproject)], ["project-name"], ["pyright-config", "x"]):
            code, _, err = self.run_main(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("usage:", err)

    def test_missing_file_is_an_error(self):
        code, _, err = self.run_main("project-name", str(self.tmp / "absent.toml"))
        self.assertEqual(code, 2)
        self.assertIn("pyproject_config:", err)


if __name__ == "__main__":
    unittest.main()
