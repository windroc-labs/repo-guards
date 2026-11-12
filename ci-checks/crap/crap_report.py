import argparse
import csv
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

CONDITIONS = re.compile(r"\((\d+)/(\d+)\)")

# Display path -> line number -> (covered units, total units); a unit is a line or one branch condition.
Hits = dict[str, dict[int, tuple[int, int]]]


class Skip(Exception):
    pass


@dataclass(frozen=True)
class Function:
    file: str
    name: str
    ccn: int
    start: int
    end: int


@dataclass(frozen=True)
class Row:
    file: str
    line: int
    name: str
    ccn: int
    coverage: float
    crap: float


def crap(ccn: int, coverage: float) -> float:
    return ccn**2 * (1 - coverage) ** 3 + ccn


def display(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def resolve(filename: str, sources: list[Path]) -> Path | None:
    for root in (Path.cwd(), *sources):
        path = (root / filename).resolve()
        if path.is_file():
            return path
    return None


def line_units(line: ET.Element) -> tuple[int, int]:
    covered = 1 if int(line.get("hits", "0")) > 0 else 0
    conditions = CONDITIONS.search(line.get("condition-coverage", ""))
    if line.get("branch") == "true" and conditions:
        return covered + int(conditions[1]), 1 + int(conditions[2])
    return covered, 1


def read_hits(report: Path) -> Hits:
    if not report.is_file():
        raise Skip(f"no coverage report at {report} (did the Coverage step run?)")
    try:
        root = ET.parse(report).getroot()
        sources = [Path(s.text.strip()) for s in root.iter("source") if s.text and s.text.strip()]
        hits: Hits = {}
        for cls in root.iter("class"):
            path = resolve(cls.get("filename", ""), sources)
            if path is None:
                continue
            lines = hits.setdefault(display(path), {})
            for line in cls.iter("line"):
                lines[int(line.get("number", "0"))] = line_units(line)
    except (OSError, ET.ParseError, ValueError) as e:
        raise Skip(f"cannot read the coverage report {report} ({e})") from e
    if not hits:
        raise Skip(f"no measured source file of {report} exists here")
    return hits


def read_functions(files: list[str]) -> list[Function]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "lizard", "--csv", *files],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        raise Skip(f"cannot run lizard ({e})") from e
    if result.returncode != 0 and not result.stdout.strip():
        detail = (result.stderr.strip().splitlines() or [f"exit {result.returncode}"])[-1]
        raise Skip(f"lizard failed: {detail}")
    try:
        return [
            Function(file=r[6], name=r[7], ccn=int(r[1]), start=int(r[9]), end=int(r[10]))
            for r in csv.reader(result.stdout.splitlines())
            if len(r) >= 11
        ]
    except ValueError as e:
        raise Skip(f"cannot read lizard's output ({e})") from e


def score(functions: list[Function], hits: Hits) -> list[Row]:
    rows: list[Row] = []
    for f in functions:
        units = [u for n, u in hits.get(f.file, {}).items() if f.start <= n <= f.end]
        total = sum(t for _, t in units)
        if total == 0:
            continue
        coverage = sum(c for c, _ in units) / total
        rows.append(Row(f.file, f.start, f.name, f.ccn, coverage, crap(f.ccn, coverage)))
    if not rows:
        raise Skip("no function in the measured files has coverage data")
    return sorted(rows, key=lambda r: (-r.crap, -r.ccn, r.file, r.line))


def table(rows: list[Row], top: int) -> str:
    lines = [
        "### CRAP report (not enforced)",
        "",
        f"Top {min(top, len(rows))} of {len(rows)} functions by CRAP = complexity² × (1 − coverage)³ + complexity.",
        "",
        "| File | Function | Complexity | Coverage | CRAP |",
        "|---|---|---:|---:|---:|",
    ]
    lines += [
        f"| {r.file}:{r.line} | {r.name} | {r.ccn} | {r.coverage:.0%} | {r.crap:.1f} |"
        for r in rows[:top]
    ]
    return "\n".join(lines) + "\n"


def write(text: str, summary: str | None) -> None:
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="CRAP score report: prints the top functions, never fails."
    )
    parser.add_argument("--coverage", required=True, type=Path, help="Cobertura report")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    try:
        hits = read_hits(args.coverage)
        report = table(score(read_functions(sorted(hits)), hits), args.top)
    except Skip as e:
        report = f"CRAP report skipped: {e}\n"
        print(report, end="")
        if not summary:
            return 0
    try:
        write(report, summary)
    except OSError as e:
        print(f"CRAP report skipped: cannot write the step summary ({e})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001 -- a report never fails the job.
        print(f"CRAP report skipped: {type(e).__name__}: {e}")
        sys.exit(0)
