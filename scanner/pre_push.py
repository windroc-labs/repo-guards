#!/usr/bin/env python3
"""git pre-push hook: refuse a push to a public repository when what it would publish leaks.

git runs it through hooks/pre-push once a clone enables it (hooks/README.md). It scans, with
scan.py's leak checks (private-name, local-path, email), what the push adds beyond the objects the
destination already has: each added blob's path and content, binary included; each commit's
message and author and committer names and emails; each annotated tag's message and tagger; each
pushed ref name. A junk path (__pycache__, node_modules, *.pyc, *.pyo) also hits. Only a
github.com repository that the GitHub API reports private or internal skips the scan. A missing
private-name list refuses, and so does any failure. Output gives positions, never the matched text.
"""

import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

# python3 -I leaves this file's directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import scan  # noqa: E402

GITHUB = re.compile(
    r"(?:(?:https?|ssh)://(?:[^@/]+@)?(?i:github\.com)(?::\d+)?/|(?:[^@/:]+@)?(?i:github\.com):)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?"
)
NOT_PUBLIC = ("private", "internal")
JUNK_DIRS = ("__pycache__", "node_modules")
JUNK_SUFFIXES = (".pyc", ".pyo")
GITLINK = "160000"
# The push sends the original objects, so the scan must not read replace refs' substitutes.
GIT = ("git", "--no-replace-objects", "-c", "i18n.logOutputEncoding=UTF-8")


class Cause(Enum):
    """Why a push is refused, each with its header and one-line fix."""

    HITS = (
        "this push to a public repository would publish the hits below",
        "fix: remove the hits from the listed commits, tags or ref names before they are published"
        " (amend or rebase them locally), then push again",
    )
    NO_LIST = (
        "the private-name list is not set up, so the private-name check cannot run",
        "fix: set %s for the process that pushes (one-time setup: hooks/README.md in repo-guards),"
        " then push again; the commits need no change" % scan.NAMES_ENV,
    )


class Refused(Exception):
    """A scan failure whose message is safe to print."""


@dataclass(frozen=True)
class RefUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


@dataclass(frozen=True)
class PushedFile:
    """A blob a pushed commit adds. `index` numbers it across the push; `entry` within its commit."""

    index: int
    commit: str
    entry: int
    path: str
    oid: str


@dataclass(frozen=True)
class Tag:
    sha: str
    tagger: str
    email: str
    message: str


def git(*args: str, stdin: bytes = b"") -> bytes:
    return subprocess.run([*GIT, *args], input=stdin, capture_output=True, check=True).stdout


def lines(items: list[str]) -> bytes:
    return "".join(item + "\n" for item in items).encode()


def absent(sha: str) -> bool:
    return not sha.strip("0")


def parse_updates(text: str) -> list[RefUpdate]:
    return [RefUpdate(*line.split(" ")) for line in text.splitlines() if line]


def is_public(url: str) -> bool:
    """False only when GitHub reports the repository private or internal."""
    match = GITHUB.fullmatch(url)
    if not match:
        return True
    endpoint = "repos/%s/%s" % match.groups()
    try:
        proc = subprocess.run(
            ["gh", "api", "--hostname", "github.com", endpoint, "--jq", ".visibility"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return proc.returncode != 0 or proc.stdout.strip() not in NOT_PUBLIC


def object_types(shas: list[str]) -> dict[str, str]:
    """Each sha's object type here, "missing" when this repository lacks it."""
    out = git("cat-file", "--batch-check", stdin=lines(shas)) if shas else b""
    return dict(line.split()[:2] for line in out.decode().splitlines())


def destination_tips(url: str, updates: list[RefUpdate]) -> list[str]:
    """The destination's ref tips this repository has: what they reach is already there."""
    listed = git("ls-remote", "--end-of-options", url).decode().splitlines()
    advertised = {line.split("\t")[0] for line in listed}
    advertised |= {u.remote_sha for u in updates if not absent(u.remote_sha)}
    return [sha for sha, kind in object_types(sorted(advertised)).items() if kind != "missing"]


def read_tag(sha: str) -> tuple[Tag, str, str]:
    """The tag, and the sha and type of the object it names."""
    head, _, message = git("cat-file", "tag", sha).decode("utf-8", "replace").partition("\n\n")
    fields = dict(line.split(" ", 1) for line in head.splitlines() if " " in line)
    tagger = re.fullmatch(r"(.*?) ?<([^>]*)>.*", fields.get("tagger", ""))
    name, email = (tagger[1], tagger[2]) if tagger else ("", "")
    return Tag(sha, name, email, message), fields["object"], fields["type"]


def pushed_refs(updates: list[RefUpdate], known: set[str]) -> tuple[list[Tag], list[str]]:
    """The annotated tags the push publishes and the commits its refs name."""
    tags, heads = [], []
    starts = [u.local_sha for u in updates if not absent(u.local_sha)]
    types = object_types(starts)
    for sha in starts:
        kind = types[sha]
        while kind == "tag" and sha not in known:
            tag, sha, kind = read_tag(sha)
            tags.append(tag)
        if kind == "tag":
            continue
        if kind != "commit":
            raise Refused("a pushed ref names a %s, which the scan cannot check" % kind)
        heads.append(sha)
    return tags, heads


def pushed_commits(heads: list[str], known: list[str]) -> list[str]:
    if not heads:
        return []
    revs = [*heads, *("^" + sha for sha in known)]
    out = git("rev-list", "--topo-order", "--reverse", "--stdin", stdin=lines(revs))
    return out.decode().split()


def pushed_files(commits: list[str]) -> list[PushedFile]:
    """Blobs each commit adds; a merge's are those differing from every parent (diff-tree -c)."""
    if not commits:
        return []
    out = git("diff-tree", "--stdin", "-r", "-z", "--root", "-c", stdin=lines(commits))
    files: list[PushedFile] = []
    tokens = iter(out.split(b"\0"))
    commit, entry = "", 0
    for token in tokens:
        if not token.startswith(b":"):
            if token.strip():
                commit, entry = token.decode().strip(), 0
            continue
        raw = next(tokens)
        entry += 1
        parts = token.decode().lstrip(":").split()
        parents = len(parts) // 2 - 1
        mode, oid = parts[parents], parts[2 * parents + 1]
        if not absent(oid) and mode != GITLINK:
            files.append(PushedFile(len(files) + 1, commit, entry, os.fsdecode(raw), oid))
    return files


def blob_contents(oids: list[str]) -> Iterator[tuple[str, bytes]]:
    """Each blob in turn, so memory holds one blob at a time."""
    proc = subprocess.Popen([*GIT, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None

    def feed() -> None:
        with stdin:
            stdin.write(lines(oids))

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    for oid in oids:
        size = int(stdout.readline().split()[2])
        data = stdout.read(size)
        stdout.read(1)
        yield oid, data
    feeder.join()
    stdout.close()
    if proc.wait():
        raise subprocess.CalledProcessError(proc.returncode, "git cat-file --batch")


def is_junk(path: str) -> bool:
    return any(part in JUNK_DIRS or part.endswith(JUNK_SUFFIXES) for part in path.split("/"))


class Scan:
    """One push's scanner report: every leak check, plus junk paths."""

    def __init__(self) -> None:
        self.junk = scan.Check("junk", scan.LEAK, True)
        checks = [c for c in scan.CHECKS.values() if c.klass == scan.LEAK]
        self.report = scan.Report([*checks, self.junk])
        self.finders: dict[scan.Check, scan.Finder] = {
            c: scan.TEXT_FINDERS[c.name] for c in checks if c.name in scan.TEXT_FINDERS
        }
        names = scan.load_names(None, os.getcwd())
        if names is None:
            self.report.unavailable = True
        else:
            self.finders[scan.CHECKS["private-name"]] = scan.private_name_finder(names)

    def text(self, text: str, where: scan.Where) -> None:
        scan.scan_text(self.report, self.finders, text, where)

    def files(self, files: list[PushedFile]) -> None:
        """Every path; each distinct blob's content once, at its first file."""
        first: dict[str, PushedFile] = {}
        for f in files:
            first.setdefault(f.oid, f)
            if is_junk(f.path):
                self.report.add(self.junk, scan.Where(f.path, f.index, field="path"))
            for check, find in self.finders.items():
                if check.in_paths and find(f.path):
                    self.report.add(check, scan.Where(f.path, f.index, field="path"))
        for oid, data in blob_contents(list(first)):
            f = first[oid]
            self.text(data.decode("utf-8", "replace"), scan.Where(f.path, f.index))

    def commits(self, commits: list[str]) -> None:
        if not commits:
            return
        out = git("log", "--no-walk=unsorted", "--stdin", "-z",
                  "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B", stdin=lines(commits))
        fields = out.decode("utf-8", "replace").split("\0")
        records = []
        for i in range(0, len(fields) - 5, 6):
            sha, author, author_email, committer, committer_email, message = fields[i : i + 6]
            records.append(scan.Commit(sha, author_email, committer_email, message))
            self.text(author, scan.Where(sha=sha, field="author name"))
            self.text(committer, scan.Where(sha=sha, field="committer name"))
        scan.scan_commits(self.report, self.finders, records)

    def tags(self, tags: list[Tag]) -> None:
        email = scan.CHECKS["email"]
        for tag in tags:
            self.text(tag.message, scan.Where(field="tag %s message" % tag.sha))
            self.text(tag.tagger, scan.Where(field="tag %s tagger name" % tag.sha))
            if tag.email and not scan.email_allowed(tag.email):
                self.report.add(email, scan.Where(field="tag %s tagger" % tag.sha, line=1))

    def refusal(self, files: list[PushedFile]) -> str | None:
        found = {Cause.HITS: bool(self.report.hits), Cause.NO_LIST: self.report.unavailable}
        causes = [cause for cause, on in found.items() if on]
        if not causes:
            return None
        # The scanner's notice says leak hits are already public; before a push they are not.
        out = [line for line in self.report.render(public=False).splitlines() if line != scan.LEAK_NOTICE]
        by_index = {f.index: f for f in files}
        for i in sorted({where.index for _, where in self.report.hits if where.index}):
            f = by_index[i]
            listing = "`git diff-tree -r --root -c %s`" % f.commit
            out.append("file #%d is entry %d of commit %s in %s" % (i, f.entry, f.commit, listing))
        headers = ["pre-push: refused: " + c.value[0] for c in causes]
        return "\n".join([*headers, *out, *(c.value[1] for c in causes)])


def scan_push(url: str, updates: list[RefUpdate]) -> str | None:
    """The refusal text, or None when the push is clean."""
    if all(absent(u.local_sha) for u in updates):
        return None
    check = Scan()
    known = destination_tips(url, updates)
    tags, heads = pushed_refs(updates, set(known))
    commits = pushed_commits(heads, known)
    files = pushed_files(commits)
    check.files(files)
    check.commits(commits)
    check.tags(tags)
    for number, update in enumerate(updates, 1):
        if not absent(update.local_sha):
            check.text(update.remote_ref, scan.Where(field="pushed ref #%d" % number))
    return check.refusal(files)


def main(argv: list[str], stdin: bytes) -> int:
    try:
        refusal = scan_push(argv[1], parse_updates(stdin.decode())) if is_public(argv[1]) else None
    except Exception as error:  # Any failure refuses the push.
        # Print only messages this file wrote: a git error can echo a URL that carries a token.
        detail = str(error) if isinstance(error, Refused) else type(error).__name__
        print("pre-push: refused: the leak scan failed (%s); nothing was pushed" % detail, file=sys.stderr)
        return 1
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.buffer.read()))
