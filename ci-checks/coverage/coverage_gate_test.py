import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coverage_gate import main

CONFIG = Path(__file__).parent / "coverage.toml"


def cobertura(lines: tuple[int, int], branches: tuple[int, int]) -> str:
    return (
        '<?xml version="1.0" ?>\n'
        f'<coverage lines-valid="{lines[0]}" lines-covered="{lines[1]}" '
        f'branches-valid="{branches[0]}" branches-covered="{branches[1]}" line-rate="0" branch-rate="0">'
        "<packages/></coverage>\n"
    )


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.diff_cover = mock.patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        )
        self.run_mock = self.diff_cover.start()
        self.addCleanup(self.diff_cover.stop)

    def write(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text)
        return str(path)

    def gate(self, head: str, base: str | None, config: str = str(CONFIG)) -> tuple[int, str]:
        args = ["--config", config, "--head", head, "--compare-branch", "origin/main"]
        if base is not None:
            args += ["--base", base]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(args)
        return code, out.getvalue()

    def test_combines_lines_and_branches_and_passes_at_threshold(self):
        # (70 + 10) / (90 + 10) = 80%: lines alone are 77.8%, so branches must count.
        head = self.write("head.xml", cobertura((90, 70), (10, 10)))
        base = self.write("base.xml", cobertura((90, 70), (10, 10)))
        code, out = self.gate(head, base)
        self.assertEqual(code, 0, out)
        self.assertIn("80.00%", out)

    def test_total_below_minimum_fails(self):
        head = self.write("head.xml", cobertura((100, 79), (0, 0)))
        code, out = self.gate(head, self.write("base.xml", cobertura((100, 70), (0, 0))))
        self.assertEqual(code, 1)
        self.assertIn("below 80", out)

    def test_total_below_merge_base_fails_even_above_minimum(self):
        head = self.write("head.xml", cobertura((1000, 900), (0, 0)))
        base = self.write("base.xml", cobertura((100, 91), (0, 0)))
        code, out = self.gate(head, base)
        self.assertEqual(code, 1)
        self.assertIn("below the merge-base", out)

    def test_equal_to_merge_base_passes(self):
        head = self.write("head.xml", cobertura((200, 180), (20, 18)))
        base = self.write("base.xml", cobertura((100, 90), (10, 9)))
        self.assertEqual(self.gate(head, base)[0], 0)

    def test_missing_base_report_skips_only_the_base_comparison(self):
        head = self.write("head.xml", cobertura((100, 85), (0, 0)))
        code, out = self.gate(head, str(self.tmp / "absent.xml"))
        self.assertEqual(code, 0, out)
        self.assertIn("::warning::", out)
        self.run_mock.assert_called_once()

    def test_base_without_code_skips_the_base_comparison(self):
        head = self.write("head.xml", cobertura((100, 85), (0, 0)))
        code, _ = self.gate(head, self.write("base.xml", cobertura((0, 0), (0, 0))))
        self.assertEqual(code, 0)

    def test_head_without_code_is_an_error(self):
        head = self.write("head.xml", cobertura((0, 0), (0, 0)))
        code, out = self.gate(head, None)
        self.assertEqual(code, 2)
        self.assertIn("no release code", out)

    def test_unreadable_head_report_is_an_error(self):
        self.assertEqual(self.gate(str(self.tmp / "absent.xml"), None)[0], 2)
        self.assertEqual(self.gate(self.write("bad.xml", "<coverage"), None)[0], 2)
        self.assertEqual(self.gate(self.write("nototals.xml", "<coverage/>"), None)[0], 2)

    def test_diff_cover_gets_the_configured_minimum_and_its_failure_fails(self):
        head = self.write("head.xml", cobertura((100, 90), (0, 0)))
        self.run_mock.return_value = subprocess.CompletedProcess([], 1)
        code, _ = self.gate(head, None)
        self.assertEqual(code, 1)
        argv = self.run_mock.call_args.args[0]
        self.assertEqual(argv[0], "diff-cover")
        self.assertEqual(argv[argv.index("--fail-under") + 1], "90")
        self.assertEqual(argv[argv.index("--compare-branch") + 1], "origin/main")

    def test_missing_diff_cover_is_an_error(self):
        head = self.write("head.xml", cobertura((100, 90), (0, 0)))
        self.run_mock.side_effect = FileNotFoundError("diff-cover")
        self.assertEqual(self.gate(head, None)[0], 2)

    def test_diff_cover_runs_even_when_the_total_fails(self):
        head = self.write("head.xml", cobertura((100, 50), (0, 0)))
        self.assertEqual(self.gate(head, None)[0], 1)
        self.run_mock.assert_called_once()

    def test_shared_config_has_the_documented_thresholds(self):
        head = self.write("head.xml", cobertura((100, 80), (0, 0)))
        self.assertEqual(self.gate(head, None)[0], 0)
        self.assertEqual(self.run_mock.call_args.args[0][-1], "90")

    def test_bad_config_is_an_error(self):
        head = self.write("head.xml", cobertura((100, 90), (0, 0)))
        for text in (
            "total-min = 80\n",
            "total-min = 80\ndiff-min = 90\nextra = 1\n",
            "total-min = 101\ndiff-min = 90\n",
            "total-min = -1\ndiff-min = 90\n",
            'total-min = "80"\ndiff-min = 90\n',
            "total-min = true\ndiff-min = 90\n",
            "total-min = ",
        ):
            with self.subTest(text=text):
                code, _ = self.gate(head, None, self.write("c.toml", text))
                self.assertEqual(code, 2)
        self.assertEqual(self.gate(head, None, str(self.tmp / "absent.toml"))[0], 2)


if __name__ == "__main__":
    unittest.main()
