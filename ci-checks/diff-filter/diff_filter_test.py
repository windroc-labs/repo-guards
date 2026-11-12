import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from diff_filter import FORMATS, Finding, main, parse_diff, split

ROOT = Path("/repo")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def parse(name: str, out: str, cwd: Path = ROOT) -> set[Finding]:
    old = Path.cwd
    Path.cwd = staticmethod(lambda: cwd)  # type: ignore[method-assign]
    try:
        return set(FORMATS[name].parse(out))
    finally:
        Path.cwd = old  # type: ignore[method-assign]


class Diff(unittest.TestCase):
    def test_hunks_give_new_side_lines(self):
        diff = (
            "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1,0 +2,2 @@\n+x\n+y\n@@ -9 +11 @@\n-old\n+new\n@@ -20,3 +23,0 @@\n-gone\n-gone\n-gone\n"
        )
        self.assertEqual(parse_diff(diff, ROOT), {ROOT / "src/a.py": frozenset({2, 3, 11})})

    def test_deleted_file_is_skipped(self):
        diff = "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        self.assertEqual(parse_diff(diff, ROOT), {})

    def test_deletion_only_file_has_no_lines(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3 +2,0 @@\n-x\n"
        self.assertEqual(parse_diff(diff, ROOT), {ROOT / "a.py": frozenset()})

    def test_quoted_and_spaced_names(self):
        diff = (
            'diff --git "a/q\\"t.py" "b/q\\"t.py"\n--- "a/q\\"t.py"\n+++ "b/q\\"t.py"\n@@ -0,0 +1 @@\n+x\n'
            "diff --git a/s p.py b/s p.py\n--- a/s p.py\t\n+++ b/s p.py\t\n@@ -0,0 +1 @@\n+x\n"
        )
        self.assertEqual(
            parse_diff(diff, ROOT),
            {ROOT / 'q"t.py': frozenset({1}), ROOT / "s p.py": frozenset({1})},
        )

    def test_added_line_that_looks_like_a_header(self):
        diff = "diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -0,0 +1,2 @@\n+++ b/other.md\n+@@ -0,0 +9 @@\n"
        self.assertEqual(parse_diff(diff, ROOT), {ROOT / "a.md": frozenset({1, 2})})


class Parsers(unittest.TestCase):
    def test_cargo(self):
        def message(level: str, code: str | None, spans: list[tuple[str, int]], text: str) -> str:
            return json.dumps(
                {
                    "reason": "compiler-message",
                    "message": {
                        "level": level,
                        "message": text,
                        "code": code and {"code": code},
                        "spans": [
                            {"file_name": f, "line_start": n, "is_primary": True} for f, n in spans
                        ],
                    },
                }
            )

        out = "\n".join(
            [
                message("warning", "clippy::eq_op", [("src/lib.rs", 5)], "equal expressions"),
                message("warning", "clippy::eq_op", [("src/lib.rs", 5)], "equal expressions"),
                message("warning", None, [], "2 warnings emitted"),
                message("error", "E0308", [("src/main.rs", 3)], "mismatched types"),
                message("error", None, [], "linking failed"),
                message("failure-note", None, [], "For more information"),
                json.dumps({"reason": "compiler-artifact"}),
                json.dumps({"reason": "build-finished", "success": False}),
            ]
        )
        self.assertEqual(
            parse("cargo", out),
            {
                Finding(ROOT / "src/lib.rs", 5, "equal expressions [clippy::eq_op]"),
                Finding(ROOT / "src/main.rs", 3, "mismatched types [E0308]", anywhere=True),
                Finding(None, None, "linking failed", anywhere=True),
            },
        )

    def test_ruff_check_and_format(self):
        out = json.dumps(
            [
                {
                    "code": "F401",
                    "filename": "/repo/src/m.py",
                    "location": {"row": 1, "column": 8},
                    "message": "`os` imported but unused",
                },
                {
                    "code": None,
                    "filename": "/repo/src/n.py",
                    "location": {"row": 2, "column": 1},
                    "message": "SyntaxError: x",
                },
            ]
        )
        self.assertEqual(
            parse("ruff", out),
            {
                Finding(ROOT / "src/m.py", 1, "F401 `os` imported but unused"),
                Finding(ROOT / "src/n.py", 2, "SyntaxError: x"),
            },
        )
        out = json.dumps(
            [
                {
                    "code": "unformatted",
                    "filename": "/repo/u.py",
                    "location": {"row": 1, "column": 5},
                    "message": "File would be reformatted",
                }
            ]
        )
        self.assertEqual(
            parse("ruff-format", out), {Finding(ROOT / "u.py", None, "File would be reformatted")}
        )

    def test_pyright_keeps_errors_with_one_based_lines(self):
        out = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "/repo/src/m.py",
                        "severity": "error",
                        "message": "bad\n  detail",
                        "range": {"start": {"line": 4, "character": 4}},
                        "rule": "reportAssignmentType",
                    },
                    {
                        "file": "/repo/src/m.py",
                        "severity": "warning",
                        "message": "meh",
                        "range": {"start": {"line": 0, "character": 0}},
                    },
                    {"file": "/repo/src/n.py", "severity": "error", "message": "no range"},
                ]
            }
        )
        self.assertEqual(
            parse("pyright", out),
            {
                Finding(ROOT / "src/m.py", 5, "bad\n  detail [reportAssignmentType]"),
                Finding(ROOT / "src/n.py", None, "no range"),
            },
        )

    def test_vulture(self):
        out = "src/m.py:4: unused function 'old' (60% confidence)\n"
        self.assertEqual(
            parse("vulture", out),
            {Finding(ROOT / "src/m.py", 4, "unused function 'old' (60% confidence)")},
        )

    def test_tsc_continuations_and_global_errors(self):
        out = (
            "src/index.ts(2,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "  Details here.\n"
            "error TS5023: Unknown compiler option '--bogus'.\n"
        )
        self.assertEqual(
            parse("tsc", out),
            {
                Finding(
                    ROOT / "src/index.ts",
                    2,
                    "TS2322: Type 'string' is not assignable to type 'number'.\n  Details here.",
                ),
                Finding(None, None, "TS5023: Unknown compiler option '--bogus'.", anywhere=True),
            },
        )

    def test_knip(self):
        out = json.dumps(
            {
                "issues": [
                    {
                        "file": "src/orphan.ts",
                        "dependencies": [],
                        "files": [{"name": "src/orphan.ts"}],
                        "exports": [],
                    },
                    {
                        "file": "package.json",
                        "dependencies": [{"name": "left-pad", "line": 6, "col": 6, "pos": 86}],
                    },
                    {
                        "file": "src/lib.ts",
                        "exports": [{"name": "oldUnused", "line": 2, "col": 14, "pos": 36}],
                        "duplicates": [[{"name": "a", "line": 1}, {"name": "b", "line": 1}]],
                    },
                    {"file": "src/e.ts", "enumMembers": {"Color": [{"name": "Red", "line": 3}]}},
                ]
            }
        )
        self.assertEqual(
            parse("knip", out),
            {
                Finding(ROOT / "src/orphan.ts", None, "files: src/orphan.ts"),
                Finding(ROOT / "package.json", 6, "dependencies: left-pad"),
                Finding(ROOT / "src/lib.ts", 2, "exports: oldUnused"),
                Finding(ROOT / "src/lib.ts", 1, "duplicates: a"),
                Finding(ROOT / "src/lib.ts", 1, "duplicates: b"),
                Finding(ROOT / "src/e.ts", 3, "enumMembers: Red"),
            },
        )

    def test_paths(self):
        self.assertEqual(
            parse("paths", "src/a.ts\n/repo/b.rs\n"),
            {
                Finding(ROOT / "src/a.ts", None, "not formatted"),
                Finding(ROOT / "b.rs", None, "not formatted"),
            },
        )

    def test_unparsable_output_raises(self):
        for name, out in [
            ("cargo", "not json\n"),
            ("ruff", "{"),
            ("vulture", "garbage\n"),
            ("tsc", "garbage\n"),
            ("knip", "[]"),
            ("pyright", "{}"),
        ]:
            with self.subTest(name), self.assertRaises((ValueError, KeyError, TypeError)):
                parse(name, out)


class Split(unittest.TestCase):
    def test_classification(self):
        changes = {ROOT / "a.py": frozenset({3}), ROOT / "b.py": frozenset()}
        new_line = Finding(ROOT / "a.py", 3, "x")
        old_line = Finding(ROOT / "a.py", 4, "x")
        touched_file = Finding(ROOT / "a.py", None, "x")
        deletion_only_file = Finding(ROOT / "b.py", None, "x")
        untouched = Finding(ROOT / "c.py", 1, "x")
        project = Finding(None, None, "x")
        anywhere = Finding(ROOT / "c.py", 1, "y", anywhere=True)
        failing, rest = split(
            {new_line, old_line, touched_file, deletion_only_file, untouched, project, anywhere},
            changes,
        )
        self.assertEqual(set(failing), {new_line, touched_file, project, anywhere})
        self.assertEqual(set(rest), {old_line, deletion_only_file, untouched})


class Main(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        (self.repo / "src").mkdir()
        (self.repo / "src/m.py").write_text("old = 1\nkeep = 2\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "change")
        (self.repo / "src/m.py").write_text("old = 1\nkeep = 2\nnew = 3\n")
        git(self.repo, "commit", "-qam", "change")
        old_cwd = os.getcwd()
        os.chdir(self.repo / "src")
        self.addCleanup(os.chdir, old_cwd)

    def run_main(
        self, stdout: str, code: int = 0, fmt: str = "vulture", base: str = "main"
    ) -> tuple[int, str]:
        tool = f"import sys; sys.stdout.write({stdout!r}); sys.exit({code})"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            result = main(["--base", base, "--format", fmt, "--", sys.executable, "-c", tool])
        return result, out.getvalue()

    def test_old_finding_in_untouched_line_passes(self):
        code, out = self.run_main("m.py:1: unused variable 'old' (60% confidence)\n", 3)
        self.assertEqual(code, 0, out)
        self.assertIn("src/m.py: 1", out)

    def test_finding_in_changed_line_fails(self):
        code, out = self.run_main(
            "m.py:1: unused variable 'old' (60% confidence)\nm.py:3: unused variable 'new' (60% confidence)\n",
            3,
        )
        self.assertEqual(code, 1, out)
        self.assertIn("src/m.py:3: unused variable 'new'", out)
        self.assertNotIn("src/m.py:1: unused", out)

    def test_clean_run_passes(self):
        self.assertEqual(self.run_main("")[0], 0)

    def test_file_level_finding_in_changed_file_fails(self):
        self.assertEqual(self.run_main("m.py\n", 1, "paths")[0], 1)

    def test_project_level_finding_fails(self):
        self.assertEqual(self.run_main("error TS5023: Unknown compiler option.\n", 1, "tsc")[0], 1)

    def test_unexpected_exit_code_is_a_tool_failure(self):
        code, out = self.run_main("m.py:9: unused variable 'x' (60% confidence)\n", 1)
        self.assertEqual(code, 2)
        self.assertIn("exited with 1", out)

    def test_failing_exit_without_findings_is_a_tool_failure(self):
        self.assertEqual(self.run_main("", 3)[0], 2)

    def test_unparsable_output_is_a_tool_failure(self):
        code, out = self.run_main("Traceback: boom\n", 3)
        self.assertEqual(code, 2)
        self.assertIn("Traceback: boom", out)

    def test_unknown_base_is_a_failure(self):
        self.assertEqual(self.run_main("", 0, base="no-such-ref")[0], 2)

    def test_missing_command_is_a_failure(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(["--base", "main", "--format", "paths", "--", "no-such-command-here"]), 2
            )


if __name__ == "__main__":
    unittest.main()
