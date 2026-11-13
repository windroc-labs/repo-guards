import contextlib
import io
import unittest

from snapshot_artifact import main, name

ODD_DIRECTORIES = [".", "crates/a", "a%2Fb", "dir with space", "[x]*?{y}!", "über/文", 'a"b:c<d>e|f\\g']


class Name(unittest.TestCase):
    def test_only_lowercase_letters_digits_and_hyphens(self):
        for directory in ODD_DIRECTORIES:
            with self.subTest(directory=directory):
                self.assertRegex(name("go", directory), r"^go-api-snapshot-[0-9a-f]{16}$")

    def test_same_inputs_same_name(self):
        self.assertEqual(name("rust", "crates/a"), name("rust", "crates/a"))

    def test_each_language_and_directory_gets_its_own_name(self):
        names = {name(language, d) for language in ("rust", "python", "typescript", "go") for d in ODD_DIRECTORIES}
        self.assertEqual(len(names), 4 * len(ODD_DIRECTORIES))

    def test_differs_from_the_coverage_shard_name(self):
        self.assertNotIn("coverage", name("rust", "."))


class Cli(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(args))
        return code, out.getvalue()

    def test_prints_the_name(self):
        self.assertEqual(self.run_main("python", "."), (0, name("python", ".") + "\n"))

    def test_wrong_arguments_exit_2(self):
        self.assertEqual(self.run_main("python")[0], 2)
        self.assertEqual(self.run_main("python", ".", "x")[0], 2)


if __name__ == "__main__":
    unittest.main()
