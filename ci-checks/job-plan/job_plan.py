import argparse
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass

CHECKS = {
    "rust": (
        "format",
        "lint",
        "comment-budget",
        "type-check",
        "unused-dependencies",
        "dependency-rules",
    ),
    "python": (
        "format",
        "lint",
        "comment-budget",
        "type-check",
        "dead-code",
        "unused-dependencies",
        "vulnerabilities",
        "licenses",
    ),
    "typescript": (
        "format",
        "comment-budget",
        "type-check",
        "dead-code",
        "vulnerabilities",
        "licenses",
    ),
}
COVERAGE = "coverage"
LINE = re.compile(r"([A-Za-z0-9][A-Za-z0-9 _.-]*?)\s*:\s*(\S.*)")
# A job matrix holds at most 256 jobs.
MAX_SHARDS = 256
FILTER = {
    "rust": re.compile(r"[A-Za-z0-9_:]+"),
    "python": re.compile(r"[A-Za-z_][A-Za-z0-9_]*"),
}
PYTEST_OPERATORS = {"and", "or", "not"}


class Error(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    jobs: list[dict]
    coverage: str
    shards: list[dict]


def parse_jobs(language: str, text: str) -> tuple[list[dict], str]:
    known = CHECKS[language]
    if not text.strip():
        return [{"name": "Checks", "checks": list(known)}], "Coverage"
    jobs: list[dict] = []
    coverage = ""
    names: set[str] = set()
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LINE.fullmatch(line)
        if not match:
            raise Error(f"jobs: {line!r} is not '<job name>: <check> ...'")
        name, checks = match.group(1), match.group(2).split()
        if name in names:
            raise Error(f"jobs: job name {name} is used twice")
        names.add(name)
        for check in checks:
            if check not in known and check != COVERAGE:
                raise Error(
                    f"jobs: {check} is not a check here; checks: {', '.join(known)}, {COVERAGE}"
                )
            if check in seen:
                raise Error(f"jobs: {check} is in more than one job")
            seen.add(check)
        if COVERAGE in checks:
            if len(checks) > 1:
                raise Error(f"jobs: {COVERAGE} runs the PR's tests, so its job holds nothing else")
            coverage = name
        else:
            jobs.append({"name": name, "checks": checks})
    return jobs, coverage


def filter_lines(language: str, text: str) -> list[list[str]]:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    for word in (w for line in lines for w in line):
        if not FILTER[language].fullmatch(word) or (
            language == "python" and word.lower() in PYTEST_OPERATORS
        ):
            raise Error(f"coverage-shards: {word} is not a test-name filter")
    if len(lines) >= MAX_SHARDS:
        raise Error(f"coverage-shards: at most {MAX_SHARDS - 1} lines")
    return lines


def parse_shards(language: str, text: str) -> list[dict]:
    if not text.strip():
        return []
    if language == "typescript":
        count = text.strip()
        if not count.isdigit() or not 2 <= int(count) <= MAX_SHARDS:
            raise Error(f"coverage-shards: {count!r} is not a shard count from 2 to {MAX_SHARDS}")
        n = int(count)
        return [{"name": str(i), "shard": f"{i}/{n}"} for i in range(1, n + 1)]
    lines = filter_lines(language, text)
    every = [word for line in lines for word in line]
    if language == "rust":
        # libtest runs a test whose name contains any filter; --skip drops one containing any.
        shards = [{"name": str(i), "filters": " ".join(line)} for i, line in enumerate(lines, 1)]
        rest = " ".join(f"--skip {word}" for word in every)
        return [*shards, {"name": "rest", "filters": rest}]
    shards = [{"name": str(i), "k": " or ".join(line)} for i, line in enumerate(lines, 1)]
    return [*shards, {"name": "rest", "k": f"not ({' or '.join(every)})"}]


def plan(language: str, jobs_text: str, shards_text: str) -> Plan:
    jobs, coverage = parse_jobs(language, jobs_text)
    shards = parse_shards(language, shards_text)
    if shards and not coverage:
        raise Error(f"coverage-shards is set, but jobs runs no {COVERAGE}")
    return Plan(jobs, coverage, shards)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Lay out a shared workflow's jobs and coverage shards from the caller's inputs."
    )
    parser.add_argument("--language", required=True, choices=sorted(CHECKS))
    args = parser.parse_args(argv)
    try:
        result = plan(args.language, os.environ.get("JOBS", ""), os.environ.get("SHARDS", ""))
    except Error as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    artifact = f"coverage-{os.environ.get('GITHUB_RUN_ID', '0')}-{secrets.token_hex(4)}"
    outputs = {
        "jobs": json.dumps(result.jobs, separators=(",", ":")),
        "coverage": result.coverage,
        "shards": json.dumps(result.shards, separators=(",", ":")),
        "artifact": artifact,
    }
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.writelines(f"{key}={value}\n" for key, value in outputs.items())
    for job in result.jobs:
        print(f"job {job['name']}: {' '.join(job['checks'])}")
    print(f"coverage: {result.coverage or 'off'}")
    for shard in result.shards:
        print(f"coverage shard {shard['name']}: {json.dumps(shard)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
