import argparse
import io
import re
import shutil
import sys
import tarfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

USES = re.compile(r"uses:\s*([\w.-]+/repo-guards)/\.github/workflows/[\w.-]+@(\S+)")
SHA = re.compile(r"[0-9a-f]{40}")
SUBDIR = "ci-checks"
REF = "main"


class Error(Exception):
    pass


def caller_repo(workflow: str) -> str:
    uses = set(USES.findall(workflow))
    if not uses:
        raise Error("no repo-guards reusable workflow in the caller workflow")
    refs = sorted({ref for _, ref in uses} - {REF})
    if refs:
        raise Error(f"repo-guards workflows must be called @{REF}, found {refs}")
    if len(uses) > 1:
        raise Error(f"repo-guards workflows called from different repos: {sorted(repo for repo, _ in uses)}")
    return uses.pop()[0]


def download(repo: str, ref: str) -> bytes:
    with urllib.request.urlopen(f"https://codeload.github.com/{repo}/tar.gz/{ref}", timeout=60) as response:
        return response.read()


def extract(archive: bytes, out: Path) -> str:
    staging = out.with_name(out.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.split("/")[1:2] == [SUBDIR]]
        if not members:
            raise Error(f"archive has no {SUBDIR}/ directory")
        for member in members:
            member.name = "/".join(member.name.split("/")[1:])
        tar.extractall(staging, members=members, filter="data")
        commit = tar.pax_headers.get("comment", "")
    if not SHA.fullmatch(commit):
        shutil.rmtree(staging)
        raise Error(f"archive has no commit id (got {commit!r})")
    (staging / "SHA").write_text(commit + "\n")
    (staging / ".gitignore").write_text("*\n")
    shutil.rmtree(out, ignore_errors=True)
    staging.rename(out)
    return commit


def run(argv: list[str], fetch: Callable[[str, str], bytes] = download) -> int:
    parser = argparse.ArgumentParser(description="Download repo-guards' ci-checks/ (configs, checker sources, scripts) the caller CI uses, from main.")
    parser.add_argument("--workflow", default=".github/workflows/ci.yml", type=Path)
    parser.add_argument("--out", default=".repo-guards", type=Path)
    args = parser.parse_args(argv)
    try:
        repo = caller_repo(args.workflow.read_text())
        commit = extract(fetch(repo, REF), args.out)
    except (OSError, tarfile.TarError, Error) as error:
        print(f"fetch_ci_checks: {error}", file=sys.stderr)
        return 2
    print(f"{repo}@{REF} ({commit}) {SUBDIR}/ -> {args.out / SUBDIR}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
