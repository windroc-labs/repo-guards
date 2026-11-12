import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from diff_filter import FORMATS, Finding, main, parse_diff, split

ROOT = Path("/repo")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def golangci(linter: str, text: str, filename: str, line: int) -> dict[str, Any]:
    return {"FromLinter": linter, "Text": text, "Pos": {"Filename": filename, "Line": line}}


def parse(name: str, out: str) -> set[Finding]:
    with mock.patch.object(Path, "cwd", return_value=ROOT):
        return set(FORMATS[name].parse(out))


class Diff(unittest.TestCase):
    def test_hunks_give_new_side_lines(self):
        diff = (
            "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1,0 +2,2 @@\n+x\n+y\n@@ -9 +11 @@\n-old\n+new\n@@ -20,3 +23,0 @@\n-gone\n-gone\n-gone\n"
        )
        self.assertEqual(
            parse_diff(diff.split("\n"), ROOT), {ROOT / "src/a.py": frozenset({2, 3, 11})}
        )

    def test_deleted_file_is_skipped(self):
        diff = "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        self.assertEqual(parse_diff(diff.split("\n"), ROOT), {})

    def test_deletion_only_file_has_no_lines(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3 +2,0 @@\n-x\n"
        self.assertEqual(parse_diff(diff.split("\n"), ROOT), {ROOT / "a.py": frozenset()})

    def test_only_newline_ends_a_line(self):
        diff = (
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -2 +2 @@\n-x\n+v = 2  # \x0cdiff --git a/z b/z\x0c+++ b/decoy.py\u2028@@ -1 +1,20 @@\n"
            "@@ -10 +10 @@\n-y\n+v10 = 99\n"
        )
        self.assertEqual(parse_diff(diff.split("\n"), ROOT), {ROOT / "m.py": frozenset({2, 10})})

    def test_quoted_and_spaced_names(self):
        diff = (
            'diff --git "a/q\\"t.py" "b/q\\"t.py"\n--- "a/q\\"t.py"\n+++ "b/q\\"t.py"\n@@ -0,0 +1 @@\n+x\n'
            "diff --git a/s p.py b/s p.py\n--- a/s p.py\t\n+++ b/s p.py\t\n@@ -0,0 +1 @@\n+x\n"
        )
        self.assertEqual(
            parse_diff(diff.split("\n"), ROOT),
            {ROOT / 'q"t.py': frozenset({1}), ROOT / "s p.py": frozenset({1})},
        )

    def test_added_line_that_looks_like_a_header(self):
        diff = "diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -0,0 +1,2 @@\n+++ b/other.md\n+@@ -0,0 +9 @@\n"
        self.assertEqual(parse_diff(diff.split("\n"), ROOT), {ROOT / "a.md": frozenset({1, 2})})


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

    def test_vulture_path_splits_at_the_last_line_number(self):
        out = "pkg/x:1: y.py:7: unused import 'os' (90% confidence)\n"
        self.assertEqual(
            parse("vulture", out),
            {Finding(ROOT / "pkg/x:1: y.py", 7, "unused import 'os' (90% confidence)")},
        )

    def test_tsc_path_prefers_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "new.ts").write_text("")
            row = "new.ts(5,7): error TS2322: Type '\"a(1,1): error TS1: b\"' is not assignable.\n"
            with mock.patch.object(Path, "cwd", return_value=root):
                found = set(FORMATS["tsc"].parse(row))
        self.assertEqual({(f.path, f.line) for f in found}, {(root / "new.ts", 5)})

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
                Finding(ROOT / "package.json", 6, "dependencies: left-pad", anywhere=True),
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

    def test_golangci_typecheck_fails_anywhere(self):
        out = json.dumps(
            {
                "Issues": [
                    golangci("errcheck", "Error return value is not checked", "a/a.go", 9),
                    golangci("typecheck", "undefined: x", "b/b.go", 3),
                ],
                "Report": {},
            }
        )
        self.assertEqual(
            parse("golangci", out),
            {
                Finding(ROOT / "a/a.go", 9, "errcheck: Error return value is not checked"),
                Finding(ROOT / "b/b.go", 3, "typecheck: undefined: x", anywhere=True),
            },
        )

    def test_golangci_without_issues(self):
        for out in ('{"Issues":null,"Report":{}}', '{"Issues":[],"Report":{}}'):
            with self.subTest(out):
                self.assertEqual(parse("golangci", out), set())

    def test_go_vet_reads_the_object_stream(self):
        diagnostic = {"posn": "/repo/a/a.go:5:24", "end": "/repo/a/a.go:5:27", "message": "bad"}
        out = (
            "{}\n"
            + json.dumps(
                {
                    "example.com/m/a": {
                        "printf": [diagnostic],
                        "unreachable": [{"posn": "/repo/a/a.go:9:2", "message": "unreachable"}],
                    }
                },
                indent="\t",
            )
            + json.dumps(
                {"example.com/m/b": {"copylocks": [{"posn": "/x/b.go:3:1", "message": "c"}]}}
            )
            + "\n"
        )
        self.assertEqual(
            parse("go-vet", out),
            {
                Finding(ROOT / "a/a.go", 5, "printf: bad"),
                Finding(ROOT / "a/a.go", 9, "unreachable: unreachable"),
                Finding(Path("/x/b.go"), 3, "copylocks: c"),
            },
        )

    def test_go_vet_without_diagnostics(self):
        for out in ("", "{}\n", "{}\n{}\n"):
            with self.subTest(out):
                self.assertEqual(parse("go-vet", out), set())

    def test_deadcode(self):
        out = "a/a.go:9:6: unreachable func: F\ncmd/x/main.go:12:1: unreachable func: T.m\n"
        self.assertEqual(
            parse("deadcode", out),
            {
                Finding(ROOT / "a/a.go", 9, "unreachable func: F"),
                Finding(ROOT / "cmd/x/main.go", 12, "unreachable func: T.m"),
            },
        )
        self.assertEqual(parse("deadcode", ""), set())

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

    def test_unparsable_go_output_raises(self):
        for name, out in [
            ("golangci", "level=error msg=boom\n"),
            ("golangci", "{}"),
            ("go-vet", "# example.com/m/a\n"),
            ("go-vet", "{} trailing"),
            ("go-vet", '{"p": {"printf": [{"posn": "a.go", "message": "x"}]}}'),
            ("deadcode", "garbage\n"),
            ("deadcode", "a.go:9: no column\n"),
        ]:
            with self.subTest(name, out=out):
                fmt = FORMATS[name]
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    list(fmt.parse(out))


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
        self.assertEqual(
            set(failing), {new_line, touched_file, deletion_only_file, project, anywhere}
        )
        self.assertEqual(set(rest), {old_line, untouched})


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

    def commit(self, files: dict[str, bytes]) -> None:
        for name, content in files.items():
            (self.repo / name).write_bytes(content)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "more")

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

    def test_file_changed_only_by_deleting_lines_fails_file_level(self):
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-qb", "deletion")
        self.commit({"src/m.py": b"old = 1\n"})
        self.assertEqual(self.run_main("m.py\n", 1, "paths")[0], 1)

    def test_pure_rename_counts_as_changed_file(self):
        git(self.repo, "mv", "src/m.py", "src/renamed.py")
        git(self.repo, "commit", "-qm", "rename")
        self.assertEqual(self.run_main("renamed.py\n", 1, "paths")[0], 1)

    def test_empty_new_file_counts_as_changed_file(self):
        self.commit({"src/empty.ts": b""})
        self.assertEqual(self.run_main("empty.ts\n", 1, "paths")[0], 1)

    def test_bare_carriage_return_does_not_end_a_line(self):
        body = [b"v%d = %d" % (n, n) for n in range(1, 11)]
        git(self.repo, "checkout", "-q", "main")
        self.commit({"src/m.py": b"\n".join(body) + b"\n"})
        git(self.repo, "checkout", "-qb", "carriage-return")
        body[1] = b"v2 = 2  # \rdiff --git a/z b/z\r+++ b/decoy.py\r@@ -1 +1,20 @@"
        body[9] = b"v10 = 99"
        self.commit({"src/m.py": b"\n".join(body) + b"\n"})
        code, out = self.run_main("m.py:10: unused variable 'v10' (60% confidence)\n", 3)
        self.assertEqual(code, 1, out)

    def test_findings_listed_once(self):
        row = "m.py:3: unused variable 'new' (60% confidence)\n"
        _, out = self.run_main(row + row, 3)
        self.assertEqual(out.count("src/m.py:3: unused variable 'new'"), 1)

    def test_binary_attribute_does_not_hide_changed_lines(self):
        self.commit(
            {
                ".gitattributes": b"*.py -diff\n",
                "src/m.py": b"old = 1\nkeep = 2\nnew = 3\nmore = 4\n",
            }
        )
        self.assertEqual(
            self.run_main("m.py:4: unused variable 'more' (60% confidence)\n", 3)[0], 1
        )

    def test_non_utf8_change_is_read(self):
        self.commit({"src/data.txt": b"caf\xe9\n"})
        self.assertEqual(self.run_main("")[0], 0)
        self.assertEqual(self.run_main("data.txt\n", 1, "paths")[0], 1)

    def test_cargo_error_in_untouched_line_fails(self):
        message = {
            "reason": "compiler-message",
            "message": {
                "level": "error",
                "message": "mismatched types",
                "code": {"code": "E0308"},
                "spans": [{"file_name": "m.py", "line_start": 1, "is_primary": True}],
            },
        }
        self.assertEqual(self.run_main(json.dumps(message) + "\n", 101, "cargo")[0], 1)

    def test_cargo_failure_without_an_error_is_a_tool_failure(self):
        message = {
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "message": "unused variable",
                "code": {"code": "unused_variables"},
                "spans": [{"file_name": "m.py", "line_start": 1, "is_primary": True}],
            },
        }
        finished = {"reason": "build-finished", "success": False}
        out = json.dumps(message) + "\n" + json.dumps(finished) + "\n"
        code, text = self.run_main(out, 101, "cargo")
        self.assertEqual(code, 2)
        self.assertIn("reported no error", text)

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

    def test_golangci_issue_in_untouched_line_passes(self):
        issue = golangci("errcheck", "unchecked", "m.py", 1)
        code, out = self.run_main(json.dumps({"Issues": [issue]}), 1, "golangci")
        self.assertEqual(code, 0, out)
        self.assertIn("src/m.py: 1", out)

    def test_golangci_issue_in_changed_line_fails(self):
        issue = golangci("errcheck", "unchecked", "m.py", 3)
        code, out = self.run_main(json.dumps({"Issues": [issue]}), 1, "golangci")
        self.assertEqual(code, 1, out)
        self.assertIn("src/m.py:3: errcheck: unchecked", out)

    def test_golangci_typecheck_in_untouched_line_fails(self):
        issue = golangci("typecheck", "undefined: y", "m.py", 1)
        self.assertEqual(self.run_main(json.dumps({"Issues": [issue]}), 1, "golangci")[0], 1)

    def test_golangci_clean_run_passes(self):
        self.assertEqual(self.run_main('{"Issues":null,"Report":{}}', 0, "golangci")[0], 0)

    def test_golangci_other_exit_is_a_tool_failure(self):
        code, out = self.run_main('{"Issues":null,"Report":{}}', 3, "golangci")
        self.assertEqual(code, 2)
        self.assertIn("exited with 3", out)

    def test_go_vet_finding_in_changed_line_fails_and_untouched_line_passes(self):
        def vet(line: int) -> str:
            posn = f"{self.repo / 'src/m.py'}:{line}:1"
            return json.dumps({"example.com/m": {"printf": [{"posn": posn, "message": "bad"}]}})

        code, out = self.run_main("{}\n" + vet(1), 0, "go-vet")
        self.assertEqual(code, 0, out)
        self.assertIn("src/m.py: 1", out)
        code, out = self.run_main(vet(3), 0, "go-vet")
        self.assertEqual(code, 1, out)
        self.assertIn("src/m.py:3: printf: bad", out)

    def test_go_vet_compile_error_is_a_failure(self):
        code, out = self.run_main("", 1, "go-vet")
        self.assertEqual(code, 2)
        self.assertIn("exited with 1", out)

    def test_deadcode_finding_in_changed_line_fails_and_untouched_line_passes(self):
        code, out = self.run_main("m.py:1:5: unreachable func: old\n", 0, "deadcode")
        self.assertEqual(code, 0, out)
        code, out = self.run_main("m.py:3:5: unreachable func: New\n", 0, "deadcode")
        self.assertEqual(code, 1, out)
        self.assertIn("src/m.py:3: unreachable func: New", out)

    def test_deadcode_nonzero_exit_is_a_tool_failure(self):
        code, out = self.run_main("m.py:3:5: unreachable func: New\n", 1, "deadcode")
        self.assertEqual(code, 2)
        self.assertIn("exited with 1", out)

    def test_unknown_base_is_a_failure(self):
        self.assertEqual(self.run_main("", 0, base="no-such-ref")[0], 2)

    def test_missing_command_is_a_failure(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(["--base", "main", "--format", "paths", "--", "no-such-command-here"]), 2
            )


if __name__ == "__main__":
    unittest.main()
