import argparse
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class FileCoverage:
    lines: dict[int, int] = field(default_factory=dict)
    # (line, block, branch) -> times taken, as llvm-cov numbers a line's branches.
    branches: dict[tuple[int, int, int], int] = field(default_factory=dict)


Report = dict[str, FileCoverage]


def relative(path: str, root: str) -> str:
    """A shard's absolute paths name its own checkout; relative ones are the same on every runner."""
    pure = PurePosixPath(path)
    if not pure.is_absolute():
        return path
    try:
        return str(pure.relative_to(root))
    except ValueError:
        raise ValueError(f"{path} is outside {root}") from None


def merge(reports: Iterable[str], root: str) -> Report:
    """Every report's lines and branches, hits added: a line or branch any report measures is measured."""
    merged: Report = {}
    for text in reports:
        current: FileCoverage | None = None
        for raw in text.splitlines():
            tag, _, value = raw.strip().partition(":")
            if tag == "SF":
                current = merged.setdefault(relative(value, root), FileCoverage())
            elif tag == "end_of_record":
                current = None
            elif tag in ("DA", "BRDA"):
                if current is None:
                    raise ValueError(f"{raw!r} is outside an SF record")
                fields = value.split(",")
                if len(fields) < (2 if tag == "DA" else 4):
                    raise ValueError(f"{raw!r} has too few fields")
                if tag == "DA":
                    line, hits = int(fields[0]), int(fields[1])
                    current.lines[line] = current.lines.get(line, 0) + hits
                else:
                    key = (int(fields[0]), int(fields[1]), int(fields[2]))
                    taken = 0 if fields[3] == "-" else int(fields[3])
                    current.branches[key] = current.branches.get(key, 0) + taken
    return merged


def lcov(report: Report) -> str:
    out: list[str] = []
    for path in sorted(report):
        cov = report[path]
        out.append(f"SF:{path}")
        out += [f"BRDA:{line},{block},{n},{taken}" for (line, block, n), taken in sorted(cov.branches.items())]
        out += [f"DA:{line},{hits}" for line, hits in sorted(cov.lines.items())]
        out.append("end_of_record")
    return "".join(f"{line}\n" for line in out)


@dataclass
class Counts:
    lines_covered: int = 0
    lines_valid: int = 0
    branches_covered: int = 0
    branches_valid: int = 0

    def add(self, other: "Counts") -> None:
        self.lines_covered += other.lines_covered
        self.lines_valid += other.lines_valid
        self.branches_covered += other.branches_covered
        self.branches_valid += other.branches_valid

    def rates(self) -> dict[str, str]:
        return {
            "line-rate": rate(self.lines_covered, self.lines_valid),
            "branch-rate": rate(self.branches_covered, self.branches_valid),
        }


def rate(covered: int, valid: int) -> str:
    return f"{covered / valid:.4f}" if valid else "1"


def cobertura(report: Report, root: str) -> str:
    totals = Counts()
    classes: list[ET.Element] = []
    for path in sorted(report):
        cov = report[path]
        # line -> [branches taken, branches]
        conditions: dict[int, list[int]] = {}
        for (line, _, _), taken in cov.branches.items():
            counts = conditions.setdefault(line, [0, 0])
            counts[0] += taken > 0
            counts[1] += 1
        file_counts = Counts(
            lines_covered=sum(1 for h in cov.lines.values() if h > 0),
            lines_valid=len(cov.lines),
            branches_covered=sum(c for c, _ in conditions.values()),
            branches_valid=sum(n for _, n in conditions.values()),
        )
        totals.add(file_counts)
        cls = ET.Element("class", {"name": path, "filename": path, **file_counts.rates(), "complexity": "0"})
        ET.SubElement(cls, "methods")
        lines = ET.SubElement(cls, "lines")
        for number in sorted(cov.lines.keys() | conditions.keys()):
            attrs = {"number": str(number), "hits": str(cov.lines.get(number, 0))}
            if number in conditions:
                covered, valid = conditions[number]
                attrs |= {"branch": "true", "condition-coverage": f"{covered * 100 // valid}% ({covered}/{valid})"}
            ET.SubElement(lines, "line", attrs)
        classes.append(cls)
    root_el = ET.Element(
        "coverage",
        {
            "lines-valid": str(totals.lines_valid),
            "lines-covered": str(totals.lines_covered),
            "branches-valid": str(totals.branches_valid),
            "branches-covered": str(totals.branches_covered),
            **totals.rates(),
            "complexity": "0",
            "version": "1.9",
            "timestamp": "0",
        },
    )
    ET.SubElement(ET.SubElement(root_el, "sources"), "source").text = root
    packages = ET.SubElement(root_el, "packages")
    package = ET.SubElement(packages, "package", {"name": "", **totals.rates(), "complexity": "0"})
    ET.SubElement(package, "classes").extend(classes)
    ET.indent(root_el)
    return '<?xml version="1.0" ?>\n' + ET.tostring(root_el, encoding="unicode") + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Merge LCOV reports into one LCOV or Cobertura report.")
    parser.add_argument("--root", required=True, help="the project directory; absolute paths become relative to it")
    parser.add_argument("--format", required=True, choices=("lcov", "cobertura"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        report = merge((p.read_text() for p in args.reports), args.root)
        if not report:
            raise ValueError("the reports measure no file")
        text = lcov(report) if args.format == "lcov" else cobertura(report, args.root)
        args.output.write_text(text)
    except (OSError, ValueError) as error:
        print(f"coverage_lcov: {error!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
