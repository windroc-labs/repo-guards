import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from licenses import expression_ok, run

ALLOW = {"MIT", "Apache-2.0", "MIT License"}


class Expression(unittest.TestCase):
    def test_spdx_expressions(self):
        cases = {
            "MIT": True,
            "MIT*": True,
            "MIT License": True,
            "GPL-3.0": False,
            "(MIT OR GPL-3.0)": True,
            "MIT AND GPL-3.0": False,
            "MIT AND Apache-2.0": True,
            "GPL-3.0 OR (MIT AND Apache-2.0)": True,
            "(MIT": False,
            "UNKNOWN": False,
        }
        for text, ok in cases.items():
            self.assertEqual(expression_ok(text, ALLOW), ok, text)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.deny = self.tmp / "deny.toml"
        self.deny.write_text('[licenses]\nallow = ["MIT", "BSD-3-Clause"]\n')
        self.classifiers = self.tmp / "classifiers.toml"
        self.classifiers.write_text('MIT = "MIT License"\nBSD-3-Clause = "BSD License"\n')

    def judge(self, fmt: str, report: object, exceptions: str | None = None) -> int:
        path = self.tmp / "report.json"
        path.write_text(json.dumps(report))
        argv = ["--format", fmt, "--deny", str(self.deny), "--classifiers", str(self.classifiers)]
        if exceptions is not None:
            (self.tmp / "exc.txt").write_text(exceptions)
            argv += ["--exceptions", str(self.tmp / "exc.txt")]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run([*argv, str(path)])

    def test_pip_classifiers_and_exceptions(self):
        ok = [{"Name": "a", "License": "MIT License"}, {"Name": "b", "License": "BSD License; MIT License"}]
        self.assertEqual(self.judge("pip", ok), 0)
        bad = [*ok, {"Name": "c", "License": "GPL"}]
        self.assertEqual(self.judge("pip", bad), 1)
        self.assertEqual(self.judge("pip", bad, "\n  c  \n\n"), 0)

    def test_npm_names_and_lists(self):
        self.assertEqual(self.judge("npm", {"@s/x@1.0.0": {"licenses": ["GPL", "MIT"]}}), 0)
        self.assertEqual(self.judge("npm", {"@s/x@1.0.0": {"licenses": "GPL"}}), 1)
        self.assertEqual(self.judge("npm", {"@s/x@1.0.0": {"licenses": "GPL"}}, "@s/x\n"), 0)

    def test_blank_or_missing_exceptions_file_is_fine(self):
        self.assertEqual(self.judge("npm", {"x@1": {"licenses": "MIT"}}, ""), 0)
        self.assertEqual(self.judge("npm", {"x@1": {"licenses": "MIT"}}), 0)

    def test_bad_input_fails(self):
        self.assertEqual(self.judge("pip", {"not": "a list"}), 2)
        (self.tmp / "report.json").write_text("not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["--format", "pip", "--deny", str(self.deny), "--classifiers", str(self.classifiers), str(self.tmp / "report.json")]), 2)

    def test_mapping_wider_than_deny_fails(self):
        self.classifiers.write_text('MIT = "MIT License"\nISC = "ISC License (ISCL)"\n')
        self.assertEqual(self.judge("pip", []), 2)


if __name__ == "__main__":
    unittest.main()
