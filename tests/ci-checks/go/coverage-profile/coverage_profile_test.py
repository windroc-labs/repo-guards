import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[4] / "guards/ci-checks/go/coverage-profile"
sys.path.insert(0, str(SOURCE))

from coverage_profile import main, merge

IGNORE = r"(^|/)(testdata|generated)/|_test\.go$|\.pb(\.gw)?\.go$"
MODULE = "m"


class Merge(unittest.TestCase):
    def test_set_mode_counts_a_block_covered_by_any_profile(self):
        mode, blocks = merge(
            [
                ["mode: set", "m/a.go:3.10,5.2 2 0"],
                ["mode: set", "m/a.go:3.10,5.2 2 1"],
            ],
            IGNORE,
            MODULE,
        )
        self.assertEqual(mode, "mode: set")
        self.assertEqual(blocks, {"m/a.go:3.10,5.2 2": 1})

    def test_count_modes_sum_the_hits(self):
        for mode in ("count", "atomic"):
            with self.subTest(mode=mode):
                _, blocks = merge([[f"mode: {mode}", "m/a.go:3.10,5.2 2 4"], [f"mode: {mode}", "m/a.go:3.10,5.2 2 3"]], IGNORE, MODULE)
                self.assertEqual(blocks, {"m/a.go:3.10,5.2 2": 7})

    def test_blocks_keep_their_first_order(self):
        _, blocks = merge([["mode: set", "m/b.go:1.1,2.2 1 0", "m/a.go:1.1,2.2 1 1", "m/b.go:1.1,2.2 1 1"]], IGNORE, MODULE)
        self.assertEqual(list(blocks), ["m/b.go:1.1,2.2 1", "m/a.go:1.1,2.2 1"])

    def test_ignored_files_are_dropped(self):
        _, blocks = merge(
            [
                [
                    "mode: set",
                    "m/a.go:1.1,2.2 1 1",
                    "m/testdata/x.go:1.1,2.2 1 1",
                    "m/api/v1/service.pb.go:1.1,2.2 1 0",
                    "m/api/v1/service_grpc.pb.go:1.1,2.2 1 0",
                    "m/api/v1/service.pb.gw.go:1.1,2.2 1 0",
                    "m/internal/generated/g.go:1.1,2.2 1 0",
                    "m/a_test.go:1.1,2.2 1 1",
                    "m/internal/example/e.go:1.1,2.2 1 0",
                ]
            ],
            IGNORE,
            MODULE,
        )
        self.assertEqual(list(blocks), ["m/a.go:1.1,2.2 1", "m/internal/example/e.go:1.1,2.2 1"])

    def test_only_the_path_inside_the_module_is_matched(self):
        _, blocks = merge(
            [["mode: set", "example.com/testdata/a.go:1.1,2.2 1 1", "example.com/testdata/testdata/b.go:1.1,2.2 1 1"]],
            IGNORE,
            "example.com/testdata",
        )
        self.assertEqual(list(blocks), ["example.com/testdata/a.go:1.1,2.2 1"])

    def test_blank_lines_are_skipped(self):
        _, blocks = merge([["mode: set", "", "m/a.go:1.1,2.2 1 1", "  "]], IGNORE, MODULE)
        self.assertEqual(blocks, {"m/a.go:1.1,2.2 1": 1})

    def test_profiles_of_different_modes_are_an_error(self):
        with self.assertRaises(ValueError):
            merge([["mode: set", "m/a.go:1.1,2.2 1 1"], ["mode: count", "m/a.go:1.1,2.2 1 1"]], IGNORE, MODULE)

    def test_a_profile_without_a_mode_line_is_an_error(self):
        with self.assertRaises(ValueError):
            merge([["m/a.go:1.1,2.2 1 1"]], IGNORE, MODULE)

    def test_a_malformed_block_is_an_error(self):
        with self.assertRaises(ValueError):
            merge([["mode: set", "m/a.go:1.1,2.2 1 x"]], IGNORE, MODULE)


class Main(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "coverage.toml").write_text(f"ignore-filename-regex = '{IGNORE}'\n")

    def write(self, name: str, text: str) -> str:
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def test_writes_the_merged_profile(self):
        one = self.write("1.out", "mode: set\nm/a.go:1.1,2.2 1 0\nm/a_test.go:1.1,2.2 1 1\n")
        two = self.write("2.out", "mode: set\nm/a.go:1.1,2.2 1 1\nm/b.go:3.1,4.2 2 0\n")
        out = self.dir / "merged.out"
        self.assertEqual(main([str(self.dir / "coverage.toml"), MODULE, str(out), one, two]), 0)
        self.assertEqual(out.read_text(), "mode: set\nm/a.go:1.1,2.2 1 1\nm/b.go:3.1,4.2 2 0\n")

    def test_usage_and_unreadable_inputs_exit_two(self):
        one = self.write("1.out", "mode: set\n")
        out = str(self.dir / "merged.out")
        self.assertEqual(main([str(self.dir / "coverage.toml"), MODULE, out]), 2)
        self.assertEqual(main([str(self.dir / "missing.toml"), MODULE, out, one]), 2)
        self.assertEqual(main([str(self.dir / "coverage.toml"), MODULE, out, str(self.dir / "missing.out")]), 2)
        bad = self.write("bad.out", "m/a.go:1.1,2.2 1 1\n")
        self.assertEqual(main([str(self.dir / "coverage.toml"), MODULE, out, bad]), 2)


if __name__ == "__main__":
    unittest.main()
