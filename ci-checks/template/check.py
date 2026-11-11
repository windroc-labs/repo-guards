#!/usr/bin/env python3
import argparse
import fnmatch
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def load(config_path):
    config = json.loads(Path(config_path).read_text())
    languages = [json.loads((HERE / "languages" / f"{name}.json").read_text()) for name in config["languages"]]
    return config, languages


def matches(path, patterns):
    name = path.rsplit("/", 1)[-1]
    for p in patterns:
        if "/" in p:
            if fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, "*/" + p):
                return True
        elif fnmatch.fnmatchcase(name, p):
            return True
    return False


def count(rev, rule):
    # git grep exits 1 when nothing matches
    out = subprocess.run(
        ["git", "grep", "-I", "-c", "-E", "-e", rule["pattern"], rev, "--", *[f":(glob)**/{f}" for f in rule["files"]]],
        capture_output=True, text=True,
    )
    if out.returncode not in (0, 1):
        sys.exit(f"git grep failed for {rule['pattern']!r}: {out.stderr.strip()}")
    counts = Counter()
    for line in out.stdout.splitlines():
        path, n = line[len(rev) + 1:].rsplit(":", 1)
        counts[path] += int(n)
    return counts


def exemptions(args):
    config, languages = load(args.config)
    base = git("merge-base", args.base, "HEAD").strip()
    allow = {(a["tool"], a["path"]): a["max"] for a in config.get("exemption_allowlist", [])}
    totals = Counter()
    failures = []
    for lang in languages:
        for rule in lang["exemptions"]:
            before, after = count(base, rule), count("HEAD", rule)
            for path, n in after.items():
                totals[rule["tool"]] += n
                limit = allow.get((rule["tool"], path), before[path])
                if n > limit:
                    failures.append(f"{path}: {rule['tool']} exemptions {before[path]} -> {n} (allowed {limit}), pattern {rule['pattern']!r}")
    for tool, n in sorted(totals.items()):
        print(f"{tool}: {n}")
    if failures:
        print("\nNew inline exemptions. Remove them, or add {tool, path, max, reason} to exemption_allowlist:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


def config_paths(args):
    config, languages = load(args.config)
    patterns = [p for lang in languages for p in lang["config_files"]]
    allowed = config.get("allowed_config_paths", [])
    bad = [p for p in git("ls-files").splitlines() if matches(p, patterns) and not any(fnmatch.fnmatchcase(p, a) for a in allowed)]
    if bad:
        print("Tool config or test hook files outside allowed_config_paths:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"{len(patterns)} config file patterns, no file outside allowed_config_paths")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "config.json"))
    sub = parser.add_subparsers(dest="check", required=True)
    e = sub.add_parser("exemptions")
    e.add_argument("--base", required=True)
    sub.add_parser("config-paths")
    args = parser.parse_args()
    return exemptions(args) if args.check == "exemptions" else config_paths(args)


if __name__ == "__main__":
    sys.exit(main())
