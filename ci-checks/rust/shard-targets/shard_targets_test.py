import contextlib
import io
import json
import unittest

from shard_targets import main, targets


class Targets(unittest.TestCase):
    def test_a_shard_without_double_dash_is_all_targets(self):
        self.assertEqual(targets(["--lib --bins"]), ["--lib", "--bins"])

    def test_words_after_double_dash_are_left_out(self):
        self.assertEqual(targets(["--test=unit -- input::", "--test=unit -- output:: --exact"]), ["--test=unit"])

    def test_repeated_targets_keep_their_first_place(self):
        self.assertEqual(targets(["--lib -- a::", "--test=hid", "--lib -- b::"]), ["--lib", "--test=hid"])

    def test_any_whitespace_splits_words(self):
        self.assertEqual(targets([" --lib\t--test=unit\n"]), ["--lib", "--test=unit"])

    def test_no_shards_no_targets(self):
        self.assertEqual(targets([]), [])

    def test_a_shard_of_only_filters_adds_nothing(self):
        self.assertEqual(targets(["-- input::"]), [])


class Main(unittest.TestCase):
    def run_main(self, shards: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([json.dumps(shards)])
        return code, out.getvalue()

    def test_prints_targets_space_joined(self):
        self.assertEqual(self.run_main(["--lib -- a::", "--test=hid"]), (0, "--lib --test=hid\n"))

    def test_no_targets_prints_an_empty_line(self):
        self.assertEqual(self.run_main([]), (0, "\n"))

    def test_wrong_argument_count_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main([]), 2)
        self.assertIn("usage:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
