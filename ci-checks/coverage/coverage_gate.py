import argparse
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

KEYS = ("total-min", "diff-min")


class Error(Exception):
    pass


@dataclass(frozen=True)
class Totals:
    lines_valid: int
    lines_covered: int
    branches_valid: int
    branches_covered: int

    @property
    def valid(self) -> int:
        return self.lines_valid + self.branches_valid

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.lines_covered + self.branches_covered, self.valid)

    def describe(self) -> str:
        return (
            f"{float(self.ratio * 100):.2f}% (lines {self.lines_covered}/{self.lines_valid}, "
            f"branches {self.branches_covered}/{self.branches_valid})"
        )


def load_config(path: Path) -> dict[str, Fraction]:
    try:
        config = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Error(f"{path}: {e}") from e
    if sorted(config) != sorted(KEYS):
        raise Error(f"{path}: keys must be exactly {', '.join(KEYS)}")
    limits: dict[str, Fraction] = {}
    for key in KEYS:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 100:
            raise Error(f"{path}: {key} must be a number from 0 to 100")
        limits[key] = Fraction(value)
    return limits


def read_totals(path: Path) -> Totals:
    try:
        root = ET.parse(path).getroot()
        return Totals(
            *(
                int(root.attrib[k])
                for k in ("lines-valid", "lines-covered", "branches-valid", "branches-covered")
            )
        )
    except (OSError, ET.ParseError, KeyError, ValueError) as e:
        raise Error(f"{path}: not a Cobertura report with line and branch totals ({e})") from e


def check_totals(head: Totals, base: Totals | None, total_min: Fraction) -> list[str]:
    failures = []
    if head.ratio * 100 < total_min:
        failures.append(f"total coverage {head.describe()} is below {float(total_min):g}%")
    if base is not None and head.ratio < base.ratio:
        failures.append(
            f"total coverage {head.describe()} is below the merge-base's {base.describe()}"
        )
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Coverage gate: total, merge-base and changed lines."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--head", required=True, type=Path, help="Cobertura report of the checked-out commit"
    )
    parser.add_argument(
        "--base",
        type=Path,
        help="Cobertura report of the merge-base; absent file skips that comparison",
    )
    parser.add_argument("--compare-branch", required=True, help="ref diff-cover diffs against")
    args = parser.parse_args(argv)
    try:
        limits = load_config(args.config)
        head = read_totals(args.head)
        if head.valid == 0:
            raise Error(
                f"{args.head}: no release code was measured; check the include and exclude settings"
            )
        base = None
        if args.base is not None and not args.base.exists():
            print(
                f"::warning::{args.base} is missing (the merge-base coverage run failed); skipping the merge-base comparison"
            )
        elif args.base is not None:
            base = read_totals(args.base)
            if base.valid == 0:
                base = None
    except Error as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    print(
        f"total coverage: {head.describe()}" + (f"; merge-base: {base.describe()}" if base else "")
    )
    failures = check_totals(head, base, limits["total-min"])
    for failure in failures:
        print(f"::error::{failure}")
    diff_min = f"{float(limits['diff-min']):g}"
    try:
        diff = subprocess.run(
            [
                "diff-cover",
                str(args.head),
                "--compare-branch",
                args.compare_branch,
                "--fail-under",
                diff_min,
            ],
            check=False,
        )
    except OSError as e:
        print(f"::error::cannot run diff-cover: {e}", file=sys.stderr)
        return 2
    if diff.returncode != 0:
        print(f"::error::changed lines are below {diff_min}% covered (diff-cover above)")
    return 1 if failures or diff.returncode != 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
