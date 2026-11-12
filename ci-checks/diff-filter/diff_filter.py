import argparse
import io
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class Finding:
    path: Path | None  # None: about the whole project
    line: int | None  # None: about the whole file
    text: str
    anywhere: bool = False  # fails even outside the changed lines, e.g. a compile error


Changes = dict[Path, frozenset[int]]  # every file in the diff, with its added or changed lines


class ToolError(Exception):
    pass


HUNK = re.compile(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@")
ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    '"': '"',
    "\\": "\\",
}


def unquote(name: str) -> str:
    if not name.startswith('"'):
        return name
    return re.sub(
        r"\\([0-7]{3}|.)",
        lambda m: chr(int(m[1], 8)) if len(m[1]) == 3 else ESCAPES[m[1]],
        name[1:-1],
    )


def rows(text: str) -> list[str]:
    # str.splitlines() also splits on form feed, U+2028 and others that a PR can put inside a line.
    return text.split("\n")


def parse_diff(diff: str, top: Path) -> Changes:
    changes: dict[Path, set[int]] = {}
    lines: set[int] | None = None
    in_header = False
    for row in rows(diff):
        if row.startswith("diff --git "):
            in_header, lines = True, None
        elif in_header and row.startswith("+++ "):
            name = unquote(row[4:].rstrip("\t"))
            if name != "/dev/null":
                lines = changes.setdefault((top / name.removeprefix("b/")).resolve(), set())
        elif row.startswith("@@"):
            in_header = False
            hunk = HUNK.match(row)
            if hunk and lines is not None:
                start = int(hunk[1])
                lines.update(range(start, start + int(hunk[2] or 1)))
    return {path: frozenset(found) for path, found in changes.items()}


def capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=False, stdout=subprocess.PIPE, encoding="utf-8", errors="surrogateescape"
    )


def git(*args: str) -> str:
    result = capture(["git", *args])
    if result.returncode:
        raise ToolError(f"git {' '.join(args)} exited with {result.returncode}")
    return result.stdout


def changed_lines(base: str, top: Path) -> Changes:
    # Every file the PR adds, edits or renames, even with no added lines (empty, deletion-only, pure rename).
    names = git("diff", "--name-only", "-z", "-M", "--diff-filter=d", f"{base}...HEAD")
    diff = git(
        "-c",
        "core.quotePath=false",
        "diff",
        "-U0",
        "-M",
        "--no-color",
        "--no-ext-diff",
        "--text",
        "--no-textconv",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        f"{base}...HEAD",
    )
    lines = parse_diff(diff, top)
    files = {(top / name).resolve() for name in names.split("\0") if name}
    return {path: lines.get(path, frozenset()) for path in files}


def where(path: str) -> Path:
    return (Path.cwd() / path).resolve()


def json_lines(out: str) -> Iterator[Any]:
    for row in rows(out):
        if row.strip():
            yield json.loads(row)


def parse_cargo(out: str) -> Iterator[Finding]:
    for record in json_lines(out):
        if record["reason"] != "compiler-message":
            continue
        message = record["message"]
        code = message["code"]
        text = f"{message['message']} [{code['code']}]" if code else message["message"]
        span = next((s for s in message["spans"] if s["is_primary"]), None)
        path, line = (where(span["file_name"]), span["line_start"]) if span else (None, None)
        if message["level"].startswith("error"):
            yield Finding(path, line, text, anywhere=True)
        elif message["level"] == "warning" and span:
            yield Finding(path, line, text)


def parse_ruff(out: str) -> Iterator[Finding]:
    for item in json.loads(out):
        prefix = f"{item['code']} " if item["code"] else ""
        yield Finding(where(item["filename"]), item["location"]["row"], prefix + item["message"])


def parse_ruff_format(out: str) -> Iterator[Finding]:
    for item in json.loads(out):
        yield Finding(where(item["filename"]), None, item["message"])


def parse_pyright(out: str) -> Iterator[Finding]:
    for item in json.loads(out)["generalDiagnostics"]:
        if item["severity"] != "error":
            continue
        rule = f" [{item['rule']}]" if item.get("rule") else ""
        line = item["range"]["start"]["line"] + 1 if "range" in item else None
        yield Finding(
            where(item["file"]) if item.get("file") else None, line, item["message"] + rule
        )


VULTURE = re.compile(r"(.+):(\d+): (.+)")


def parse_vulture(out: str) -> Iterator[Finding]:
    for row in rows(out):
        if not row.strip():
            continue
        match = VULTURE.fullmatch(row)
        if not match:
            raise ValueError(f"unexpected line: {row}")
        yield Finding(where(match[1]), int(match[2]), match[3])


TSC_AT = re.compile(r"\((\d+),\d+\): error (TS\d+: .*)")
TSC_GLOBAL = re.compile(r"error (TS\d+: .*)")


def tsc_location(row: str) -> Finding | None:
    # A message can echo "(1,1): error TS…" from a string type; prefer the split whose path exists.
    splits = [
        Finding(where(row[:i]), int(m[1]), m[2])
        for i in range(1, len(row))
        if row[i] == "(" and (m := TSC_AT.fullmatch(row, i))
    ]
    return next((f for f in splits if f.path and f.path.is_file()), splits[0] if splits else None)


def parse_tsc(out: str) -> list[Finding]:
    found: list[Finding] = []
    for row in rows(out):
        if row.startswith((" ", "\t")) and found:
            found[-1] = replace(found[-1], text=f"{found[-1].text}\n{row}")
        elif at := tsc_location(row):
            found.append(at)
        elif glob := TSC_GLOBAL.fullmatch(row):
            found.append(Finding(None, None, glob[1], anywhere=True))
        elif row.strip():
            raise ValueError(f"unexpected line: {row}")
    return found


def knip_items(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        node = cast(dict[str, Any], value)
        if "name" in node:
            yield node
            return
        value = list(node.values())
    if isinstance(value, list):
        for item in cast(list[Any], value):
            yield from knip_items(item)


# Dependency findings are item 7's, which judges package.json, not lines: they fail anywhere.
KNIP_DEPENDENCIES = {
    "dependencies",
    "devDependencies",
    "optionalPeerDependencies",
    "unlisted",
    "binaries",
}


def parse_knip(out: str) -> Iterator[Finding]:
    for issue in json.loads(out)["issues"]:
        path = where(issue["file"])
        for kind, value in issue.items():
            if kind not in ("file", "owners"):
                for item in knip_items(value):
                    yield Finding(
                        path, item.get("line"), f"{kind}: {item['name']}", kind in KNIP_DEPENDENCIES
                    )


def parse_paths(out: str) -> Iterator[Finding]:
    for row in rows(out):
        if row.strip():
            yield Finding(where(row.strip()), None, "not formatted")


@dataclass(frozen=True)
class Format:
    parse: Callable[[str], Iterable[Finding]]
    exits: frozenset[int]  # exit codes of a run that worked, with or without findings
    errors: frozenset[int] = (
        frozenset()
    )  # exit codes that need a fail-anywhere finding to explain them


FORMATS = {
    "cargo": Format(parse_cargo, frozenset({0, 101}), frozenset({101})),
    "ruff": Format(parse_ruff, frozenset({0, 1})),
    "ruff-format": Format(parse_ruff_format, frozenset({0, 1})),
    "pyright": Format(parse_pyright, frozenset({0, 1})),
    "vulture": Format(parse_vulture, frozenset({0, 3})),
    "tsc": Format(parse_tsc, frozenset({0, 1, 2})),
    "knip": Format(parse_knip, frozenset({0, 1})),
    "paths": Format(parse_paths, frozenset({0, 1})),
}


def blocks(finding: Finding, changes: Changes) -> bool:
    if finding.anywhere or finding.path is None:
        return True
    if finding.path not in changes:
        return False
    return finding.line is None or finding.line in changes[finding.path]


def split(findings: Iterable[Finding], changes: Changes) -> tuple[list[Finding], list[Finding]]:
    failing: list[Finding] = []
    rest: list[Finding] = []
    for finding in findings:
        (failing if blocks(finding, changes) else rest).append(finding)
    return failing, rest


def shown(path: Path, top: Path) -> str:
    return str(path.relative_to(top)) if path.is_relative_to(top) else str(path)


def run(fmt: Format, command: list[str]) -> list[Finding]:
    try:
        result = capture(command)
    except OSError as error:
        raise ToolError(f"cannot run {command[0]}: {error}") from error
    try:
        if result.returncode not in fmt.exits:
            raise ToolError(f"{command[0]} exited with {result.returncode}")
        try:
            findings = list(dict.fromkeys(fmt.parse(result.stdout)))
        except (ValueError, KeyError, TypeError) as error:
            raise ToolError(f"cannot read {command[0]} output: {error!r}") from error
        if result.returncode in fmt.errors and not any(f.anywhere for f in findings):
            raise ToolError(f"{command[0]} exited with {result.returncode} but reported no error")
        if result.returncode and not findings:
            raise ToolError(
                f"{command[0]} exited with {result.returncode} but reported no findings"
            )
    except ToolError:
        sys.stdout.write(result.stdout)
        raise
    return findings


def report(failing: list[Finding], rest: list[Finding], top: Path) -> None:
    def key(f: Finding) -> tuple[str, int]:
        return (shown(f.path, top) if f.path else "", f.line or 0)

    for finding in sorted(failing, key=key):
        label = shown(finding.path, top) if finding.path else "(project)"
        if finding.line:
            label = f"{label}:{finding.line}"
        print(f"{label}: {finding.text}")
    if rest:
        counts = Counter(shown(f.path, top) for f in rest if f.path)
        print(f"\n{len(rest)} findings outside the changed lines, not failing:")
        for path, count in sorted(counts.items()):
            print(f"  {path}: {count}")
    if failing:
        print(f"\n{len(failing)} findings fail this check.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a check and fail only on findings in the lines changed since --base."
    )
    parser.add_argument(
        "--base", required=True, help="the git ref to diff against; merge-base for PRs"
    )
    parser.add_argument("--format", required=True, choices=sorted(FORMATS))
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- then the check command")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command after --")
    try:
        top = Path(git("rev-parse", "--show-toplevel").strip()).resolve()
        changes = changed_lines(args.base, top)
        findings = run(FORMATS[args.format], command)
    except ToolError as error:
        print(f"diff_filter: {error}", file=sys.stderr)
        return 2
    failing, rest = split(findings, changes)
    report(failing, rest, top)
    return 1 if failing else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        cast(io.TextIOWrapper, stream).reconfigure(errors="backslashreplace")
    sys.exit(main())
