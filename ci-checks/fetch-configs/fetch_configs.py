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


class Error(Exception):
    pass


def pinned(workflow: str) -> tuple[str, str]:
    pins = set(USES.findall(workflow))
    if not pins:
        raise Error("no repo-guards reusable workflow in the caller workflow")
    if len(pins) > 1:
        raise Error(f"repo-guards workflows pinned to different refs: {sorted(pins)}")
    repo, ref = pins.pop()
    if not SHA.fullmatch(ref):
        raise Error(f"{repo} is pinned to {ref!r}, not a full commit sha")
    return repo, ref


def download(repo: str, sha: str) -> bytes:
    with urllib.request.urlopen(f"https://codeload.github.com/{repo}/tar.gz/{sha}", timeout=60) as response:
        return response.read()


def extract(archive: bytes, out: Path) -> None:
    staging = out.with_name(out.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.split("/")[1:2] == [SUBDIR]]
        if not members:
            raise Error(f"archive has no {SUBDIR}/ directory")
        for member in members:
            member.name = "/".join(member.name.split("/")[1:])
        tar.extractall(staging, members=members, filter="data")
    shutil.rmtree(out, ignore_errors=True)
    staging.rename(out)


def run(argv: list[str], fetch: Callable[[str, str], bytes] = download) -> int:
    parser = argparse.ArgumentParser(description="Download the repo-guards configs that the caller CI pins.")
    parser.add_argument("--workflow", default=".github/workflows/ci.yml", type=Path)
    parser.add_argument("--out", default=".repo-guards", type=Path)
    args = parser.parse_args(argv)
    try:
        repo, sha = pinned(args.workflow.read_text())
        extract(fetch(repo, sha), args.out)
        (args.out / "SHA").write_text(sha + "\n")
    except (OSError, tarfile.TarError, Error) as error:
        print(f"fetch_configs: {error}", file=sys.stderr)
        return 2
    print(f"{repo}@{sha} {SUBDIR}/ -> {args.out / SUBDIR}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
