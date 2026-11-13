import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[3] / "guards/ci-checks/crap"
sys.path.insert(0, str(SOURCE))

from crap_report import crap, main


def cobertura(source: str, classes: dict[str, str], methods: str = "") -> str:
    body = "".join(
        f'<class name="c" filename="{name}"><methods>{methods}</methods><lines>{lines}</lines></class>'
        for name, lines in classes.items()
    )
    return (
        '<?xml version="1.0" ?>\n'
        f"<coverage><sources><source>{source}</source></sources>"
        f'<packages><package name="p"><classes>{body}</classes></package></packages></coverage>\n'
    )


def line(number: int, hits: int, conditions: str = "") -> str:
    branch = f' branch="true" condition-coverage="{conditions}"' if conditions else ""
    return f'<line number="{number}" hits="{hits}"{branch}/>'


def lizard_csv(*rows: tuple[str, str, int, int, int]) -> str:
    return "".join(
        f'1,{ccn},10,0,{end - start + 1},"{name}@{start}-{end}@{path}","{path}","{name}","{name} ( )",{start},{end}\n'
        for path, name, ccn, start, end in rows
    )


class Report(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "project"
        (self.project / "src").mkdir(parents=True)
        self.summary = self.tmp / "summary.md"
        cwd = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, cwd)
        env = mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)})
        env.start()
        self.addCleanup(env.stop)

    def source(self, name: str) -> Path:
        path = self.project / "src" / name
        path.write_text("code\n")
        return path

    def report(
        self,
        xml: str | None,
        lizard: str | subprocess.CompletedProcess[str] | OSError = "",
        *extra: str,
    ) -> tuple[int, str, str]:
        path = self.tmp / "coverage.xml"
        if xml is not None:
            path.write_text(xml)
        if isinstance(lizard, OSError):
            run = mock.patch("subprocess.run", side_effect=lizard)
        else:
            result = (
                subprocess.CompletedProcess([], 0, lizard, "")
                if isinstance(lizard, str)
                else lizard
            )
            run = mock.patch("subprocess.run", return_value=result)
        out = io.StringIO()
        with run as self.lizard, contextlib.redirect_stdout(out):
            code = main(["--coverage", str(path), *extra])
        summary = self.summary.read_text() if self.summary.is_file() else ""
        return code, out.getvalue(), summary

    def assert_skipped(self, code: int, out: str, summary: str, reason: str) -> None:
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1, out)
        self.assertTrue(out.startswith("CRAP report skipped: "), out)
        self.assertIn(reason, out)
        self.assertIn(out.strip(), summary)


class Score(unittest.TestCase):
    def test_full_coverage_scores_the_complexity(self) -> None:
        self.assertEqual(crap(5, 1.0), 5)

    def test_no_coverage_scores_complexity_squared_plus_complexity(self) -> None:
        self.assertEqual(crap(5, 0.0), 30)

    def test_half_coverage(self) -> None:
        self.assertAlmostEqual(crap(4, 0.5), 16 * 0.125 + 4)


class Table(Report):
    def test_function_coverage_counts_lines_and_branches_inside_its_range(self) -> None:
        self.source("a.py")
        xml = cobertura(
            str(self.project),
            {"src/a.py": line(1, 1) + line(2, 0, "50% (1/2)") + line(3, 1) + line(9, 0)},
        )
        code, out, summary = self.report(xml, lizard_csv(("src/a.py", "f", 3, 1, 3)))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        # Declaration line 1 is left out; line 2 and its two conditions (1/3), line 3 (1/1) -> 2/4.
        self.assertIn("| src/a.py:1 | f | 3 | 50% | 4.1 |", summary)

    def test_never_called_function_scores_zero_though_its_declaration_ran(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1) + line(2, 0) + line(3, 0)})
        _, _, summary = self.report(xml, lizard_csv(("src/a.py", "f", 2, 1, 3)))
        self.assertIn("| src/a.py:1 | f | 2 | 0% | 6.0 |", summary)

    def test_method_line_copies_are_ignored(self) -> None:
        self.source("a.ts")
        method = '<method name="f"><lines><line number="2" hits="1"/></lines></method>'
        xml = cobertura(str(self.project), {"src/a.ts": line(1, 0) + line(2, 0)}, method)
        _, _, summary = self.report(xml, lizard_csv(("src/a.ts", "f", 1, 1, 2)))
        self.assertIn("| src/a.ts:1 | f | 1 | 0% | 2.0 |", summary)

    def test_lizard_exit_code_is_ignored_when_it_printed_functions(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        warned = subprocess.CompletedProcess([], 1, lizard_csv(("src/a.py", "f", 30, 1, 1)), "")
        _, _, summary = self.report(xml, warned)
        self.assertIn("| src/a.py:1 | f | 30 | 100% | 30.0 |", summary)

    def test_function_without_measured_lines_is_left_out(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        lizard = lizard_csv(("src/a.py", "f", 1, 1, 1), ("src/a.py", "g", 4, 5, 8))
        _, _, summary = self.report(xml, lizard)
        self.assertIn("| f |", summary)
        self.assertNotIn("| g |", summary)

    def test_rows_are_sorted_by_score_and_cut_at_top(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1) + line(2, 0) + line(3, 0)})
        lizard = lizard_csv(
            ("src/a.py", "low", 1, 1, 1),
            ("src/a.py", "high", 9, 2, 2),
            ("src/a.py", "mid", 2, 3, 3),
        )
        _, _, summary = self.report(xml, lizard, "--top", "2")
        self.assertIn("| File | Function | Complexity | Coverage | CRAP |", summary)
        self.assertLess(summary.index("| high |"), summary.index("| mid |"))
        self.assertNotIn("| low |", summary)
        self.assertIn("Top 2 of 3 functions", summary)

    def test_paths_resolve_against_each_source(self) -> None:
        self.source("b.ts")
        xml = cobertura(str(self.project / "src"), {"b.ts": line(1, 0)})
        _, _, summary = self.report(xml, lizard_csv(("src/b.ts", "h", 2, 1, 1)))
        self.assertIn("| src/b.ts:1 | h | 2 | 0% | 6.0 |", summary)
        self.assertIn("src/b.ts", self.lizard.call_args.args[0])

    def test_without_step_summary_the_table_goes_to_stdout(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        with mock.patch.dict(os.environ, clear=True):
            _, out, summary = self.report(xml, lizard_csv(("src/a.py", "f", 1, 1, 1)))
        self.assertIn("| src/a.py:1 | f | 1 | 100% | 1.0 |", out)
        self.assertEqual(summary, "")


class Skips(Report):
    def test_missing_coverage_report(self) -> None:
        self.assert_skipped(*self.report(None), "no coverage report")

    def test_unreadable_coverage_report(self) -> None:
        self.assert_skipped(*self.report("<coverage"), "cannot read")

    def test_no_file_in_the_report_exists(self) -> None:
        xml = cobertura(str(self.project), {"src/gone.py": line(1, 1)})
        self.assert_skipped(*self.report(xml), "no measured source file")

    def test_lizard_cannot_start(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        self.assert_skipped(*self.report(xml, OSError("boom")), "cannot run lizard")

    def test_lizard_fails_without_output(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        failed = subprocess.CompletedProcess([], 1, "", "No module named lizard\n")
        self.assert_skipped(*self.report(xml, failed), "No module named lizard")

    def test_unreadable_lizard_output(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        garbage = '1,x,1,0,1,"f@1-1@src/a.py","src/a.py","f","f ( )",1,1\n'
        self.assert_skipped(*self.report(xml, garbage), "cannot read lizard's output")

    def test_unwritable_step_summary(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        self.summary.mkdir()
        code, out, _ = self.report(xml, lizard_csv(("src/a.py", "f", 1, 1, 1)))
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1, out)
        self.assertIn("CRAP report skipped: cannot write the step summary", out)

    def test_no_function_has_coverage(self) -> None:
        self.source("a.py")
        xml = cobertura(str(self.project), {"src/a.py": line(1, 1)})
        self.assert_skipped(*self.report(xml, ""), "no function")


if __name__ == "__main__":
    unittest.main()
