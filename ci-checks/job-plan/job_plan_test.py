import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_plan import CHECKS, Error, main, plan

RUST = ["format", "lint", "comment-budget", "type-check", "unused-dependencies", "dependency-rules"]


class Jobs(unittest.TestCase):
    def test_empty_input_is_todays_layout(self):
        for language, checks in CHECKS.items():
            with self.subTest(language=language):
                result = plan(language, " \n", "")
                self.assertEqual(result.jobs, [{"name": "Checks", "checks": list(checks)}])
                self.assertEqual(result.coverage, "Coverage")
                self.assertEqual(result.shards, [])
        self.assertEqual(CHECKS["rust"], tuple(RUST))

    def test_each_line_is_a_job_and_unlisted_checks_do_not_run(self):
        text = "Fast: format lint\n\n  Deps :  unused-dependencies dependency-rules  \nTests: coverage\n"
        result = plan("rust", text, "")
        self.assertEqual(
            result.jobs,
            [
                {"name": "Fast", "checks": ["format", "lint"]},
                {"name": "Deps", "checks": ["unused-dependencies", "dependency-rules"]},
            ],
        )
        self.assertEqual(result.coverage, "Tests")

    def test_coverage_left_out_does_not_run(self):
        self.assertEqual(plan("rust", "Checks: format", "").coverage, "")

    def test_coverage_alone(self):
        result = plan("python", "Coverage: coverage", "")
        self.assertEqual(result.jobs, [])
        self.assertEqual(result.coverage, "Coverage")

    def test_check_ids_are_per_language(self):
        self.assertEqual(plan("python", "A: dead-code", "").jobs[0]["checks"], ["dead-code"])
        with self.assertRaisesRegex(Error, "lint"):
            plan("typescript", "A: lint", "")

    def test_bad_layouts_are_errors(self):
        cases = {
            "A: format\nA: lint": "A",
            "A: format\nB: format": "format",
            "A: format coverage": "coverage",
            "A: nope": "nope",
            "format lint": "format lint",
            ": format": ": format",
            "A$: format": "A\\$",
            "A:": "A:",
        }
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(Error, message):
                plan("rust", text, "")


class Shards(unittest.TestCase):
    def test_rust_filter_lines_and_a_rest_shard_that_skips_them_all(self):
        result = plan("rust", "", "driver::\n\n input:: session:: \n")
        self.assertEqual(
            result.shards,
            [
                {"name": "1", "filters": "driver::"},
                {"name": "2", "filters": "input:: session::"},
                {"name": "rest", "filters": "--skip driver:: --skip input:: --skip session::"},
            ],
        )

    def test_rust_rejects_anything_but_name_filters(self):
        for bad in ("--ignored", "a::b;c", "a*"):
            with self.subTest(bad=bad), self.assertRaisesRegex(Error, "test-name filter"):
                plan("rust", "", f"x::\n{bad}")

    def test_python_keyword_expressions_and_their_complement(self):
        result = plan("python", "", "api\nmodels views\n")
        self.assertEqual(
            result.shards,
            [
                {"name": "1", "k": "api"},
                {"name": "2", "k": "models or views"},
                {"name": "rest", "k": "not (api or models or views)"},
            ],
        )

    def test_python_rejects_operators_and_non_identifiers(self):
        for bad in ("and", "not", "OR", "a.b", "1abc", "a-b", "(a"):
            with self.subTest(bad=bad), self.assertRaisesRegex(Error, "test-name filter"):
                plan("python", "", bad)

    def test_typescript_count(self):
        result = plan("typescript", "", " 3 ")
        self.assertEqual(
            result.shards,
            [
                {"name": "1", "shard": "1/3"},
                {"name": "2", "shard": "2/3"},
                {"name": "3", "shard": "3/3"},
            ],
        )

    def test_typescript_rejects_bad_counts(self):
        for bad in ("1", "0", "x", "2.5", "257", "2\n3"):
            with self.subTest(bad=bad), self.assertRaisesRegex(Error, "count"):
                plan("typescript", "", bad)

    def test_blank_shards_mean_no_sharding(self):
        self.assertEqual(plan("rust", "", " \n ").shards, [])

    def test_shards_need_coverage(self):
        with self.assertRaisesRegex(Error, "coverage"):
            plan("rust", "Checks: format", "a::")


class Main(unittest.TestCase):
    def run_main(self, language: str, jobs: str, shards: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            output.write_text("")
            env = {
                "JOBS": jobs,
                "SHARDS": shards,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_RUN_ID": "7",
            }
            out = io.StringIO()
            with (
                mock.patch.dict(os.environ, env),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(out),
            ):
                code = main(["--language", language])
            return code, output.read_text(), out.getvalue()

    def test_writes_the_outputs(self):
        code, written, _ = self.run_main("rust", "", "a::")
        self.assertEqual(code, 0)
        outputs = dict(line.split("=", 1) for line in written.splitlines())
        self.assertEqual(json.loads(outputs["jobs"])[0]["name"], "Checks")
        self.assertEqual(outputs["coverage"], "Coverage")
        self.assertEqual(json.loads(outputs["shards"])[-1]["name"], "rest")
        self.assertRegex(outputs["artifact"], r"^coverage-7-[0-9a-f]{8}$")

    def test_error_writes_nothing(self):
        code, written, out = self.run_main("rust", "A: nope", "")
        self.assertEqual(code, 2)
        self.assertEqual(written, "")
        self.assertIn("::error::", out)


if __name__ == "__main__":
    unittest.main()
