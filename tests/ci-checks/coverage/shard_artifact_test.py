import contextlib
import io
import re
import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "guards/ci-checks/coverage"
sys.path.insert(0, str(SOURCE))

from shard_artifact import main, name

ODD_DIRECTORIES = [".", "crates/a", "a%2Fb", "a%.b", "dir with space", "[x]*?{y}!", "über/文", 'a"b:c<d>e|f\\g']


class Name(unittest.TestCase):
    def test_only_lowercase_letters_digits_and_hyphens(self):
        for directory in ODD_DIRECTORIES:
            with self.subTest(directory=directory):
                self.assertRegex(name("rust", directory), r"^[a-z0-9-]+$")

    def test_language_comes_first(self):
        self.assertTrue(name("python", "crates/a").startswith("python-coverage-"))

    def test_same_directory_same_name(self):
        self.assertEqual(name("rust", "crates/a"), name("rust", "crates/a"))

    def test_different_directories_different_names(self):
        pairs = [(".", "./"), ("a/b", "a%2Fb"), ("a", "a/"), ("a/b.shard", "a%.b")]
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                self.assertNotEqual(name("rust", first), name("rust", second))

    def test_every_odd_directory_gets_its_own_name(self):
        names = {name("rust", directory) for directory in ODD_DIRECTORIES}
        self.assertEqual(len(names), len(ODD_DIRECTORIES))

    def test_no_name_is_a_prefix_of_another(self):
        # The merge job downloads pattern "<name>-*"; one call's pattern must not match another call's shards.
        for first in ODD_DIRECTORIES:
            for second in ODD_DIRECTORIES:
                if first != second:
                    with self.subTest(first=first, second=second):
                        self.assertFalse(re.match(re.escape(name("rust", first)) + "-", name("rust", second) + "-0"))


class Main(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_prints_the_name(self):
        self.assertEqual(self.run_main(["rust", "crates/a"])[:2], (0, name("rust", "crates/a") + "\n"))

    def test_wrong_argument_count_is_a_usage_error(self):
        for argv in ([], ["rust"], ["rust", "a", "b"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_main(argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("usage", err)


if __name__ == "__main__":
    unittest.main()
