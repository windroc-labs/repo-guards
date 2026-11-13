#!/usr/bin/env python3
"""Print ci.yml's gitleaks findings in the scanner's hit format and write hits=N to $GITHUB_OUTPUT.

Usage: gitleaks_report.py TOP TMP. TOP is the scanned checkout; TMP holds gitleaks-files.json,
gitleaks-commits.json and gitleaks-messages.json, gitleaks' JSON reports of the files, commits
and commit messages scans. Like scan.py, a hit names a file by its 1-based index in the scanned
commit's file list, never by its path (FR-21), and never prints the matched text.
"""

import json
import os
import subprocess
import sys
from collections.abc import Iterator

SCANS = ("files", "commits", "messages")


class FileIndex:
    def __init__(self, top: str):
        self.top = top
        self.indexes: dict[str, dict[str, int]] = {}

    def of(self, path: str, commit: str) -> int | None:
        if commit not in self.indexes:
            cmd = ["ls-files", "-z"] if commit == "HEAD" else ["ls-tree", "-r", "-z", "--name-only", commit]
            listed = subprocess.run(["git", "-C", self.top, *cmd], capture_output=True, check=True).stdout
            self.indexes[commit] = {os.fsdecode(p): n for n, p in enumerate(dict.fromkeys(listed.split(b"\0")[:-1]), 1)}
        return self.indexes[commit].get(path)


def where(scan: str, finding: dict, top: str, index: FileIndex) -> str:
    line, col = finding["StartLine"], finding["StartColumn"]
    if scan == "messages":
        return "commit %s line %d" % (os.path.basename(finding["File"]), line)
    commit = finding["Commit"] if scan == "commits" else "HEAD"
    n = index.of(os.path.relpath(finding["File"], top) if scan == "files" else finding["File"], commit)
    place = "file #%d:%d:%d" % (n, line, col) if n else "line %d" % line
    return "commit %s %s" % (commit, place) if scan == "commits" else place


def report(top: str, tmp: str) -> Iterator[str]:
    index = FileIndex(top)
    for scan in SCANS:
        with open(os.path.join(tmp, "gitleaks-%s.json" % scan)) as f:
            for finding in json.load(f):
                yield "%s: gitleaks (leak) rule %s" % (where(scan, finding, top, index), finding["RuleID"])


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gitleaks_report.py TOP TMP", file=sys.stderr)
        return 2
    hits = 0
    for line in report(*argv):
        print(line, flush=True)
        hits += 1
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write("hits=%d\n" % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
