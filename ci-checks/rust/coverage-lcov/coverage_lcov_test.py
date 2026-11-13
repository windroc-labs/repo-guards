import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage_lcov import FileCoverage, cobertura, lcov, main, merge

ROOT = "/work/repo/app"

X64 = """SF:/work/repo/app/src/lib.rs
FN:1,_RNvCs1x64_3app3add
FNDA:2,_RNvCs1x64_3app3add
BRDA:2,0,0,2
BRDA:2,0,1,0
DA:1,2
DA:2,2
DA:3,0
end_of_record
"""

ARM64 = """SF:/work/repo/app/src/lib.rs
FN:1,_RNvCs9arm_3app3add
FNDA:1,_RNvCs9arm_3app3add
BRDA:2,0,0,0
BRDA:2,0,1,1
DA:1,1
DA:2,1
DA:3,1
end_of_record
SF:/work/repo/app/src/io.rs
BRDA:7,0,0,-
BRDA:7,0,1,-
DA:7,0
end_of_record
"""


class Merge(unittest.TestCase):
    def test_hits_and_taken_counts_add_up_across_reports(self):
        report = merge([X64, ARM64], ROOT)
        self.assertEqual(report["src/lib.rs"].lines, {1: 3, 2: 3, 3: 1})
        self.assertEqual(report["src/lib.rs"].branches, {(2, 0, 0): 2, (2, 0, 1): 1})

    def test_a_file_only_one_report_measures_is_kept(self):
        self.assertEqual(sorted(merge([X64, ARM64], ROOT)), ["src/io.rs", "src/lib.rs"])

    def test_an_unexecuted_branch_counts_as_taken_zero_times(self):
        self.assertEqual(merge([ARM64], ROOT)["src/io.rs"].branches, {(7, 0, 0): 0, (7, 0, 1): 0})

    def test_function_records_do_not_matter(self):
        # Mangled names differ per target; the same lines must merge whatever the names say.
        self.assertEqual(merge([X64], ROOT), merge([X64.replace("1x64", "other")], ROOT))

    def test_relative_paths_are_kept(self):
        self.assertEqual(sorted(merge(["SF:src/a.rs\nDA:1,1\nend_of_record\n"], ROOT)), ["src/a.rs"])

    def test_a_trailing_slash_on_the_root_changes_nothing(self):
        self.assertEqual(sorted(merge([X64], ROOT + "/")), ["src/lib.rs"])

    def test_a_path_outside_the_root_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            merge(["SF:/elsewhere/src/a.rs\nDA:1,1\nend_of_record\n"], ROOT)

    def test_a_record_outside_a_file_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "outside an SF record"):
            merge(["DA:1,1\n"], ROOT)

    def test_a_malformed_record_is_an_error(self):
        with self.assertRaises(ValueError):
            merge(["SF:src/a.rs\nDA:one,1\nend_of_record\n"], ROOT)

    def test_other_records_and_blank_lines_are_ignored(self):
        text = "TN:\nSF:src/a.rs\nFNF:1\nFNH:1\nLF:1\nLH:1\nBRF:0\nBRH:0\n\nDA:1,4\nend_of_record\n"
        self.assertEqual(merge([text], ROOT)["src/a.rs"].lines, {1: 4})


class Lcov(unittest.TestCase):
    def test_merged_lcov_merges_back_to_the_same_report(self):
        report = merge([X64, ARM64], ROOT)
        self.assertEqual(merge([lcov(report)], ROOT), report)

    def test_output_is_sorted_and_has_no_function_records(self):
        self.assertEqual(
            lcov(merge([ARM64], ROOT)),
            "SF:src/io.rs\nBRDA:7,0,0,0\nBRDA:7,0,1,0\nDA:7,0\nend_of_record\n"
            "SF:src/lib.rs\nBRDA:2,0,0,0\nBRDA:2,0,1,1\nDA:1,1\nDA:2,1\nDA:3,1\nend_of_record\n",
        )


class Cobertura(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(cobertura(merge([X64, ARM64], ROOT), ROOT))

    def test_totals_count_lines_and_branches(self):
        self.assertEqual(
            {k: self.root.get(k) for k in ("lines-valid", "lines-covered", "branches-valid", "branches-covered")},
            {"lines-valid": "4", "lines-covered": "3", "branches-valid": "4", "branches-covered": "2"},
        )

    def test_the_root_is_the_source_and_filenames_are_relative(self):
        self.assertEqual([s.text for s in self.root.iter("source")], [ROOT])
        self.assertEqual([c.get("filename") for c in self.root.iter("class")], ["src/io.rs", "src/lib.rs"])

    def test_lines_carry_hits_and_their_branch_conditions(self):
        lines = {
            (c.get("filename"), line.get("number")): line.attrib
            for c in self.root.iter("class")
            for line in c.iter("line")
        }
        self.assertEqual(lines["src/lib.rs", "1"], {"number": "1", "hits": "3"})
        self.assertEqual(
            lines["src/lib.rs", "2"],
            {"number": "2", "hits": "3", "branch": "true", "condition-coverage": "100% (2/2)"},
        )
        self.assertEqual(lines["src/io.rs", "7"]["condition-coverage"], "0% (0/2)")

    def test_a_branch_on_a_line_without_hits_is_still_counted(self):
        root = ET.fromstring(cobertura({"a.rs": FileCoverage({}, {(4, 0, 0): 1, (4, 0, 1): 0})}, ROOT))
        self.assertEqual((root.get("lines-valid"), root.get("branches-valid")), ("0", "2"))
        self.assertEqual(
            [(line.get("hits"), line.get("condition-coverage")) for line in root.iter("line")], [("0", "50% (1/2)")]
        )

    def test_condition_coverage_rounds_down(self):
        branches = {(4, 0, 0): 1, (4, 0, 1): 0, (4, 0, 2): 0}
        root = ET.fromstring(cobertura({"a.rs": FileCoverage({4: 1}, branches)}, ROOT))
        self.assertEqual([line.get("condition-coverage") for line in root.iter("line")], ["33% (1/3)"])

    def test_rates_per_file_package_and_report(self):
        report = merge([X64, ARM64], ROOT)
        root = ET.fromstring(cobertura(report, ROOT))
        rates = {c.get("filename"): (c.get("line-rate"), c.get("branch-rate")) for c in root.iter("class")}
        self.assertEqual(rates, {"src/io.rs": ("0.0000", "0.0000"), "src/lib.rs": ("1.0000", "1.0000")})
        package = next(root.iter("package"))
        self.assertEqual((package.get("line-rate"), package.get("branch-rate")), ("0.7500", "0.5000"))
        self.assertEqual((root.get("line-rate"), root.get("branch-rate")), ("0.7500", "0.5000"))

    def test_nothing_to_measure_rates_as_full(self):
        root = ET.fromstring(cobertura({"a.rs": FileCoverage({1: 0}, {})}, ROOT))
        self.assertEqual((root.get("line-rate"), root.get("branch-rate")), ("0.0000", "1"))

    def test_the_same_reports_give_the_same_bytes(self):
        self.assertEqual(cobertura(merge([X64, ARM64], ROOT), ROOT), cobertura(merge([ARM64, X64], ROOT), ROOT))


class Main(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return main(list(args)), err.getvalue()

    def test_writes_the_chosen_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a.lcov"), Path(tmp, "b.lcov")
            a.write_text(X64)
            b.write_text(ARM64)
            out = Path(tmp, "out.xml")
            self.assertEqual(
                self.run_main("--root", ROOT, "--format", "cobertura", "--output", str(out), str(a), str(b)), (0, "")
            )
            self.assertEqual(ET.parse(out).getroot().get("lines-valid"), "4")
            out = Path(tmp, "out.lcov")
            self.assertEqual(self.run_main("--root", ROOT, "--format", "lcov", "--output", str(out), str(a)), (0, ""))
            self.assertEqual(out.read_text(), lcov(merge([X64], ROOT)))

    def test_reports_measuring_nothing_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, "empty.lcov")
            empty.write_text("")
            code, err = self.run_main("--root", ROOT, "--format", "lcov", "--output", str(Path(tmp, "o")), str(empty))
            self.assertEqual(code, 2)
            self.assertIn("measure no file", err)

    def write_and_run(self, text: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp, "r.lcov")
            report.write_text(text)
            return self.run_main("--root", ROOT, "--format", "cobertura", "--output", str(Path(tmp, "o")), str(report))

    def test_a_truncated_line_record_exits_2(self):
        code, err = self.write_and_run("SF:src/a.rs\nDA:5\nend_of_record\n")
        self.assertEqual(code, 2)
        self.assertIn("too few fields", err)

    def test_a_truncated_branch_record_exits_2(self):
        code, err = self.write_and_run("SF:src/a.rs\nBRDA:5,0,1\nend_of_record\n")
        self.assertEqual(code, 2)
        self.assertIn("too few fields", err)

    def test_a_path_outside_the_root_exits_2(self):
        code, err = self.write_and_run("SF:/elsewhere/a.rs\nDA:1,1\nend_of_record\n")
        self.assertEqual(code, 2)
        self.assertIn("outside", err)

    def test_a_line_record_with_a_checksum_is_read(self):
        self.assertEqual(merge(["SF:src/a.rs\nDA:5,2,abc\nend_of_record\n"], ROOT)["src/a.rs"].lines, {5: 2})

    def test_a_missing_report_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err = self.run_main(
                "--root", ROOT, "--format", "lcov", "--output", str(Path(tmp, "o")), str(Path(tmp, "none"))
            )
            self.assertEqual(code, 2)
            self.assertIn(str(Path(tmp, "none")), err)


if __name__ == "__main__":
    unittest.main()
