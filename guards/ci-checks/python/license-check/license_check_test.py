import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from license_check import main, satisfied

LICENSES = Path(__file__).parent.parent.parent / "licenses.toml"
ALLOWED = {"MIT", "Apache-2.0", "BSD-2-Clause", "Apache-2.0 WITH LLVM-exception"}


class Expressions(unittest.TestCase):
    def test_or_needs_one_allowed_side(self):
        self.assertTrue(satisfied("Apache-2.0 OR BSD-2-Clause", ALLOWED))
        self.assertTrue(satisfied("GPL-3.0 OR MIT", ALLOWED))
        self.assertFalse(satisfied("GPL-3.0 OR LGPL-3.0", ALLOWED))

    def test_and_needs_every_side_allowed(self):
        self.assertTrue(satisfied("MIT AND Apache-2.0", ALLOWED))
        self.assertFalse(satisfied("MIT AND GPL-3.0", ALLOWED))

    def test_and_binds_tighter_than_or(self):
        self.assertTrue(satisfied("MIT OR GPL-3.0 AND LGPL-3.0", ALLOWED))
        self.assertFalse(satisfied("(MIT OR GPL-3.0) AND LGPL-3.0", ALLOWED))
        self.assertTrue(satisfied("((MIT) AND (GPL-3.0 OR Apache-2.0))", ALLOWED))

    def test_with_is_one_license_matched_as_written(self):
        self.assertTrue(satisfied("Apache-2.0 WITH LLVM-exception", ALLOWED))
        self.assertTrue(satisfied("(Apache-2.0 WITH LLVM-exception) OR GPL-3.0", ALLOWED))
        self.assertFalse(satisfied("GPL-2.0 WITH Classpath-exception-2.0", ALLOWED))
        self.assertFalse(satisfied("MIT WITH LLVM-exception", ALLOWED))

    def test_ids_and_operators_ignore_case(self):
        self.assertTrue(satisfied("mit", ALLOWED))
        self.assertTrue(satisfied("gpl-3.0 or apache-2.0", ALLOWED))

    def test_only_exact_ids_count(self):
        self.assertFalse(satisfied("MIT-CMU", ALLOWED))
        self.assertFalse(satisfied("Apache-2.0+", ALLOWED))

    def test_malformed_is_denied(self):
        for text in [
            "",
            "MIT OR",
            "(MIT",
            "MIT)",
            "MIT Apache-2.0",
            "OR MIT",
            "MIT AND AND Apache-2.0",
            "Apache-2.0 WITH",
        ]:
            with self.subTest(text=text):
                self.assertFalse(satisfied(text, ALLOWED))


CONFIG = """\
allow = ["MIT", "Apache-2.0"]

[python-classifiers]
"MIT License" = "MIT"
"Apache Software License" = "Apache-2.0"
"GNU General Public License v3 (GPLv3)" = "GPL-3.0-only"
"""


def row(
    name: str, expression: str = "UNKNOWN", classifier: str = "UNKNOWN", metadata: str = "UNKNOWN"
) -> dict[str, str]:
    return {
        "Name": name,
        "Version": "1.0",
        "License-Expression": expression,
        "License-Classifier": classifier,
        "License-Metadata": metadata,
    }


class Report(unittest.TestCase):
    def run_main(self, rows: list[dict[str, str]], config: Path | None = None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            if config is None:
                config = Path(tmp, "licenses.toml")
                config.write_text(CONFIG)
            report = Path(tmp, "licenses.json")
            report.write_text(json.dumps(rows))
            err = StringIO()
            with redirect_stderr(err):
                code = main([str(config), str(report)])
            return code, err.getvalue()

    def test_live_or_expression_passes_the_shared_allowlist(self):
        packaging = row(
            "packaging",
            expression="Apache-2.0 OR BSD-2-Clause",
            metadata="Apache-2.0 OR BSD-2-Clause",
        )
        self.assertEqual(self.run_main([packaging], LICENSES), (0, ""))

    def test_expression_wins_over_classifiers_and_metadata(self):
        rows = [
            row(
                "a",
                expression="MIT",
                classifier="GNU General Public License v3 (GPLv3)",
                metadata="GPL-3.0",
            ),
            row("b", expression="GPL-3.0", classifier="MIT License", metadata="MIT"),
        ]
        code, err = self.run_main(rows)
        self.assertEqual(code, 1)
        self.assertNotIn("a 1.0", err)
        self.assertIn("b 1.0: GPL-3.0", err)

    def test_classifiers_map_to_spdx_and_any_one_suffices(self):
        rows = [
            row("six", classifier="MIT License", metadata="GPL-3.0"),
            row(
                "dual", classifier="Apache Software License; GNU General Public License v3 (GPLv3)"
            ),
        ]
        self.assertEqual(self.run_main(rows), (0, ""))

    def test_metadata_is_one_license_text_never_split(self):
        rows = [
            row("plain", metadata="MIT"),
            row("bundled", metadata="MIT; bundled parser is GPL-3.0-only"),
        ]
        code, err = self.run_main(rows)
        self.assertEqual(code, 1)
        self.assertNotIn("plain 1.0", err)
        self.assertIn("bundled 1.0: MIT; bundled parser is GPL-3.0-only", err)

    def test_denied_packages_are_all_listed(self):
        rows = [
            row("ok", expression="MIT"),
            row("gnu", expression="GPL-3.0 OR LGPL-3.0"),
            row("mixed", expression="MIT AND GPL-3.0"),
            row("gpl", classifier="GNU General Public License v3 (GPLv3)", metadata="MIT"),
            row("free", classifier="Mozilla Public License 2.0 (MPL 2.0)"),
            row("bare"),
        ]
        code, err = self.run_main(rows)
        self.assertEqual(code, 1)
        self.assertNotIn("ok 1.0", err)
        for line in [
            "gnu 1.0: GPL-3.0 OR LGPL-3.0",
            "mixed 1.0: MIT AND GPL-3.0",
            "gpl 1.0: GNU General Public License v3 (GPLv3)",
            "free 1.0: Mozilla Public License 2.0 (MPL 2.0)",
            "bare 1.0: UNKNOWN",
        ]:
            self.assertIn(line, err)

    def test_bad_report_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp, "licenses.json")
            report.write_text("{not json")
            err = StringIO()
            with redirect_stderr(err):
                self.assertEqual(main([str(LICENSES), str(report)]), 2)
                self.assertEqual(main([str(LICENSES), str(Path(tmp, "missing.json"))]), 2)
                self.assertEqual(main([str(LICENSES)]), 2)
        self.assertEqual(
            self.run_main([{"Name": "x", "Version": "1", "License-Expression": "UNKNOWN"}])[0], 2
        )


if __name__ == "__main__":
    unittest.main()
